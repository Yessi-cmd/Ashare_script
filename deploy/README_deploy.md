# A股个人研究台服务器部署

推荐 Ubuntu/Debian、Python 3.10+、2 GB 内存，并将服务器时区设为 `Asia/Shanghai`。以下路径与仓库内 systemd 单元一致：代码 `/opt/ashare_monitor`，服务用户 `ashare`。

## 1. 创建非 root 用户和目录

```bash
sudo useradd --system --home /opt/ashare_monitor --shell /usr/sbin/nologin ashare
sudo install -d -o ashare -g ashare -m 0750 /opt/ashare_monitor
sudo install -d -o root -g ashare -m 0750 /etc/ashare-monitor
sudo install -d -o ashare -g ashare -m 0700 /var/backups/ashare-monitor
sudo timedatectl set-timezone Asia/Shanghai
```

通过 Git、rsync 或发布包把项目上传到 `/opt/ashare_monitor`，不要把本机的 `venv`、日志或凭据一起上传。

## 2. 安装依赖和配置

```bash
cd /opt/ashare_monitor
sudo -u ashare python3 -m venv venv
sudo -u ashare venv/bin/pip install -r requirements.txt
sudo -u ashare cp config.yaml.example config.yaml
sudo chmod 0600 config.yaml
```

编辑 `config.yaml`，设置 `app.owner_user_id`、通知渠道和监控参数。通知 Token/密码当前保存在该 `0600` 文件中。

安装服务环境文件：

```bash
sudo install -o root -g ashare -m 0600 deploy/ashare.env.example /etc/ashare-monitor/ashare.env
sudoedit /etc/ashare-monitor/ashare.env
```

为 `ASHARE_WEB_PASSWORD` 使用密码管理器生成的长随机密码，不要与 Telegram 或邮箱密码复用。`ASHARE_WEB_SESSION_SECRET` 必须是另一段至少 32 字节的随机值，用于签署浏览器会话，不能与密码相同。

## 3. 上线前验证

```bash
sudo -u ashare venv/bin/ruff check .
sudo -u ashare venv/bin/python -m unittest discover -s tests -v
sudo -u ashare venv/bin/python monitor.py --test
sudo -u ashare venv/bin/python market_monitor.py --test
sudo -u ashare venv/bin/python sync_universe.py --source auto
sudo -u ashare venv/bin/python backup_database.py
```

`monitor.py --test` 不发送通知。若公开行情源临时失败，稍后重试；已有日线缓存不会被清空。

## 4. 安装 systemd

```bash
sudo install -m 0644 deploy/ashare_monitor.service /etc/systemd/system/
sudo install -m 0644 deploy/ashare_market_monitor.service /etc/systemd/system/
sudo install -m 0644 deploy/ashare_bot.service /etc/systemd/system/
sudo install -m 0644 deploy/ashare_web.service /etc/systemd/system/
sudo install -m 0644 deploy/ashare_sync.service /etc/systemd/system/
sudo install -m 0644 deploy/ashare_sync.timer /etc/systemd/system/
sudo install -m 0644 deploy/ashare_backup.service /etc/systemd/system/
sudo install -m 0644 deploy/ashare_backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ashare_web
sudo systemctl enable --now ashare_sync.timer ashare_backup.timer

# 独立于 A股交易时段采集 A股与全球基准指数
sudo systemctl enable --now ashare_market_monitor

# 股票池配置并通过 monitor.py --test 后再启用监控
sudo systemctl enable --now ashare_monitor

# 仅在真实 owner_user_id 和已验证的 Telegram 凭据就绪后启用 Bot
sudo systemctl enable --now ashare_bot
```

验证：

```bash
systemctl --no-pager --full status ashare_monitor ashare_market_monitor ashare_bot ashare_web
systemctl list-timers 'ashare_*'
curl --fail http://127.0.0.1:8000/healthz
journalctl -u ashare_web -n 100 --no-pager
```

Uvicorn 必须只监听 `127.0.0.1:8000`。不要在云防火墙或安全组开放 8000 端口。

## 5A. 当前推荐：Cloudflare Tunnel + 应用登录页（无需开放端口）

域名已托管在 Cloudflare、并希望避免 Zero Trust 付费授权时，可创建远程管理 Tunnel，把公网域名直接转发到 loopback Web：

```bash
# 从 Cloudflare Dashboard 创建 Tunnel 后，按页面给出的 Debian 命令安装
# 令牌属于敏感凭据，不要写入仓库、聊天记录或普通日志
sudo cloudflared service install <TUNNEL_TOKEN>
sudo systemctl is-enabled cloudflared
sudo systemctl is-active cloudflared
```

在 Tunnel 的 Published application 路由中设置：

```text
Hostname: 自己的完整域名
Service:  http://127.0.0.1:8000
```

Cloudflare 会自动创建指向 Tunnel 的 DNS 记录。应用通过中文登录页签发最长 30 天的签名 Cookie，Basic Auth 仅保留给脚本 API；8000 不对公网开放。若 Zero Trust 激活页要求银行卡或超额扣费授权，而目标是严格零费用，不要激活 Access。

## 5B. 可选：Tailscale 私网 HTTPS

服务器和自己的工作设备加入同一 tailnet 后，在服务器执行：

```bash
sudo tailscale serve --bg http://127.0.0.1:8000
tailscale serve status
```

通过 Tailscale 分配的 `https://服务器名.tailnet名.ts.net` 访问。保持应用 Basic Auth，形成“私网身份 + 应用密码”两层保护。若工作设备不能安装 Tailscale，使用下一节。

## 5C. 可选：Caddy + Cloudflare Access

1. 域名接入 Cloudflare，先为该域名创建只允许本人邮箱/身份提供商的 Access Application。
2. 安装 Caddy，把 `deploy/Caddyfile.example` 复制到 `/etc/caddy/Caddyfile` 并替换域名。
3. 只在防火墙开放 80/443；8000 保持关闭。
4. 重载并验证 HTTPS、安全响应头、Cloudflare Access 和应用 Basic Auth 都生效。

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -I https://stocks.example.com/healthz
```

仅 Caddy + Basic Auth 可以临时使用，但长期公网访问建议加 Cloudflare Access。

## 6. 备份与恢复演练

手动触发并查看备份：

```bash
sudo systemctl start ashare_backup.service
sudo journalctl -u ashare_backup.service -n 50 --no-pager
sudo -u ashare ls -lh /var/backups/ashare-monitor
```

恢复前停止所有会访问数据库的服务，并保留现有数据库：

```bash
sudo systemctl stop ashare_monitor ashare_market_monitor ashare_bot ashare_web ashare_sync.service
sudo -u ashare cp /opt/ashare_monitor/ashare_monitor.db /var/backups/ashare-monitor/pre-restore.db
sudo -u ashare cp /var/backups/ashare-monitor/ashare_monitor-时间戳.db /opt/ashare_monitor/ashare_monitor.db
sudo -u ashare /opt/ashare_monitor/venv/bin/python -c "import sqlite3; print(sqlite3.connect('ashare_monitor.db').execute('PRAGMA integrity_check').fetchone()[0])"
sudo systemctl start ashare_monitor ashare_market_monitor ashare_bot ashare_web
```

只有完整性检查输出 `ok` 才继续运行。

## 7. 日常运维

```bash
journalctl -u ashare_monitor -f
journalctl -u ashare_market_monitor -f
journalctl -u ashare_bot -f
journalctl -u ashare_web -f
sudo systemctl restart ashare_monitor ashare_market_monitor ashare_bot ashare_web
curl --fail http://127.0.0.1:8000/healthz
```

更新代码前先备份数据库，更新后重新安装依赖、运行测试，再逐个重启服务。不要用 `main.py` 托管服务器进程；systemd 已分别监督每个服务。
