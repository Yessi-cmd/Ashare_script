# 阶段 4：服务器部署与安全

状态：`VALIDATING`

## 目标

把采集、监控、Bot 和 Web 稳定部署到个人 Linux 服务器，并允许本人从工作环境安全访问。

## 计划

- systemd 分离运行 monitor、bot、web 和定时数据同步。
- 使用非 root 系统用户和最小文件权限。
- Cloudflare Tunnel 提供出站连接、HTTPS 和公网域名，不改动共享服务器上的既有 Caddy。
- 应用 Basic Auth 作为不需要银行卡的个人访问控制。
- 配置和凭据迁移到权限受控的环境文件。
- SQLite 在线备份、保留策略和恢复演练。
- 健康检查、日志轮转和服务失败提醒。

## 实施设计

### 进程边界

- `ashare-monitor.service`：盘中行情、评分、快照和通知。
- `ashare-bot.service`：Telegram 个人管理入口。
- `ashare-web.service`：仅监听 `127.0.0.1:8000`，不得直接绑定公网地址。
- `ashare-sync.service/.timer`：交易日收盘后同步持仓、关注池和沪深 300 本地日线。
- `ashare-backup.service/.timer`：每日 SQLite 在线备份并按天数保留。

### 推荐访问路径

1. 当前实机方案：Cloudflare Tunnel 以出站连接把 `norliva.top` 转发到 `127.0.0.1:8000`，应用 Basic Auth 负责身份验证。
2. 不为 Web 开放公网端口，不让 Caddy 接触项目配置或数据库，也不改动服务器上的既有站点。
3. Cloudflare Access 因激活免费方案仍要求银行卡和超额扣费授权而不启用；用户明确要求零付费风险。

### 凭据与权限

- 代码目录属于非 root 用户 `ashare`；服务全部以该用户运行。
- Web 账号和运行路径放在权限 `0600` 的 `/etc/ashare-monitor/ashare.env`；Telegram/邮件凭据当前保留在权限 `0600` 的 `config.yaml`。
- `config.yaml` 与数据库不可由 Caddy 用户读取；Caddy 只能连接 loopback HTTP。
- 备份目录默认 `/var/backups/ashare-monitor`，只允许 `ashare` 和 root 访问。

### 备份与恢复

- 使用 Python `sqlite3.Connection.backup()` 做在线一致性备份，不直接复制 WAL 活跃中的数据库文件。
- 备份先写临时文件，完成后原子改名；自动删除超过保留天数的旧备份。
- 恢复必须在停止 monitor、bot、web、sync 后进行，并先保留当前数据库副本。

### 实时行情降级

- 关注池模式优先按当前持仓/关注代码请求腾讯行情，仅提取策略和 Web 所需字段；腾讯失败时才使用 AKShare 东方财富全市场快照兜底。
- VPS 实测东方财富连接失败要等待约 53 秒，而腾讯两股目标请求约 2 秒完成，因此不能让已知不可达的全市场接口阻塞每轮少量股票监控。
- 腾讯响应只提取代码、名称、最新价、昨收和成交量，并由最新价/昨收计算涨跌幅；响应解析和东方财富兜底必须有模拟数据测试，不让自动化测试依赖公网。
- 全市场扫描不能使用目标代码降级源，仍保持东方财富全市场数据和原有预筛选语义。
- 技术评分优先从 SQLite `daily_bars` 读取最近 60 条 qfq 日线；本地少于 20 条时才用带 10 秒超时的网络请求补齐。服务器已缓存两股各 614 条日线，不应让每轮评分重复访问公网。

## 非目标

- 不自动购买域名、修改 DNS 或创建 Cloudflare/Tailscale 账号。
- 不在本机执行需要 root 的安装脚本。
- 不开放数据库端口，不部署 PostgreSQL。

## 验收标准

- 公网扫描无法绕过身份验证访问页面。
- 服务重启后监控和告警状态可恢复。
- 备份可以在空目录恢复出持仓、行情和研究结果。
- 单个服务失败不会拖垮其他服务。
- 部署文档可在一台新 Linux 服务器上重复执行。

## 本地实施与验证记录

- 新增独立 Web、日线同步和 SQLite 备份 systemd 单元及 timer；现有 monitor/bot 单元统一增加环境文件、`UMask=0077` 和能力限制。
- 新增权限受控的环境文件样例、Caddy 安全响应头样例、Tailscale Serve 首选流程和 Cloudflare Access 备选流程。
- `sync_universe.py` 从单用户数据库/YAML 得到去重股票池，并同步 qfq 股票与 raw 沪深 300。
- `backup_database.py` 使用 SQLite 在线 backup API、临时文件、完整性检查、原子改名和保留期清理；真实数据库烟雾备份成功，文件权限为 `0600`。
- Ruff、编译检查和累计 49 项测试通过。

## 待服务器验收

- Cloudflare Tunnel 与域名已完成；用户明确选择不绑定银行卡、不接受超额扣费授权，因此不启用 Cloudflare Access，公网身份验证由应用 Basic Auth 提供。
- 已把 `600519 贵州茅台`、`300750 宁德时代` 配成非持仓研究观察池并启用 monitor；下一个交易日继续观察持续运行，不填写虚假成本或股数。
- Telegram 配置含历史凭据但通知关闭；Bot 在真实 owner ID 和轮换后的令牌就绪前保持禁用。
- 在不替换生产数据库的临时目录恢复备份已通过；生产库替换式恢复留到确有恢复需求时执行。

这些检查依赖目标服务器和用户自己的网络/身份配置；未完成前不标记本阶段 `COMPLETE`。

## 目标服务器执行记录

- 2026-07-18：通过 SSH 别名 `racknerd-vps` 部署到 Debian 12，公网 IPv4 为 `107.173.180.142`。
- 保留既有 Caddy、Xray 和 Dawnpilot 配置；Caddy 的 80/443 与 Dawnpilot 的 `127.0.0.1:8787` 均未被覆盖或中断。
- 创建非 root 用户 `ashare`、代码目录 `/opt/ashare_monitor`、环境目录 `/etc/ashare-monitor` 和备份目录 `/var/backups/ashare-monitor`，敏感文件权限为 `0600`。
- 完成 Python 3.11 虚拟环境依赖安装、编译检查和远端 46 项单元测试，全部通过。
- 7 个 systemd 单元通过 `systemd-analyze verify`；已启用 `ashare_web.service`、`ashare_sync.timer`、`ashare_backup.timer`。
- 当前没有真实 owner 或持仓；`ashare_monitor.service` 使用 YAML 两股研究观察池并已启用，`ashare_bot.service` 因没有真实 owner 和轮换后的凭据而保持禁用。
- Web 健康检查返回 200，未认证首页返回 401，正确 Basic Auth 返回 200；Uvicorn 只监听 `127.0.0.1:8000`，防火墙未开放 8000。
- 手动在线备份成功，备份权限为 `0600`；复制到临时空目录后的 `PRAGMA integrity_check` 返回 `ok`，数据库包含 6 张表。
- 安装 Cloudflare `cloudflared 2026.7.2`，创建远程管理 Tunnel `ashare-monitor` 并注册为 systemd 服务；Cloudflare 控制台显示 1 个健康副本。
- 将根域名 `norliva.top` 通过 Tunnel 发布到 `http://127.0.0.1:8000`；Cloudflare 自动创建 CNAME，未修改现有 Caddy 配置，也未开放新端口。
- 公网实测 `https://norliva.top/healthz` 返回 200 和 `{"status":"ok"}`，未认证首页返回 401，证明 Tunnel 和应用 Basic Auth 均正常。
- Cloudflare Zero Trust Free 激活流程要求银行卡及超额用量扣费授权；用户选择退出结账，未填写支付资料、未勾选扣费授权、未激活订阅。最终采用 Cloudflare Tunnel + 应用 Basic Auth 的零费用访问路径。阶段状态继续保持 `IN PROGRESS`。
- Web 与 Tunnel 单独重启后约 4 秒恢复；公网健康检查返回 200、未认证首页返回 401，既有 Caddy/Dawnpilot 全程保持 active。
- VPS 的东方财富实时全市场接口不可达，关注池已改为腾讯目标行情优先；两股实时报价约 0.8 秒返回，东方财富只作为兜底，全市场扫描语义不变。
- 技术评分改为 SQLite 本地 qfq 日线优先；服务器两股各有 614 条缓存。完整无通知监控由超过 80 秒仍未完成降至 4.653 秒，并得到宁德时代与贵州茅台的价格、涨跌幅和评分。
- 远端累计 49 项测试通过，认证后的公网首页返回 200。
- `ashare_monitor.service` 已设为开机自启，周末正确进入休市等待，当前内存约 78 MiB；正式交易日持续运行仍待 2026-07-20 验收。
