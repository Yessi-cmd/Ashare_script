# 阶段 8：多市场行情面板

状态：`COMPLETE`
完成日期：2026-07-30

## 目标

在现有只读 Web 仪表盘中增加港股、韩国、美股、日本四个市场的行情面板，用统一的本地快照链路展示基准指数、涨跌幅、数据时间和数据新鲜度。

## 范围

- 新增独立的跨市场行情采集进程，不改变 A 股评分、持仓止盈止损和通知逻辑。
- 首版监控以下基准指数：恒生指数、韩国 KOSPI、标普 500/纳斯达克/道琼斯、日本日经 225。
- 通过 Yahoo Finance Chart API 获取报价，逐代码超时和失败隔离；成功数据原子写入 SQLite。
- Web 新增“全球市场”页面；页面只读 SQLite，不直接请求行情源。
- 支持配置采集间隔、请求超时和基准指数列表；未配置时使用上述安全默认值。
- `main.py` 和 systemd 提供独立的跨市场采集入口，方便与 Web 服务一起运行。

## 非目标

- 不在本阶段接入港股、韩股、美股或日股个股的持仓、买卖信号、回测或交易接口。
- 不把跨市场指数纳入现有 A 股 V2 技术评分或通知阈值。
- 不在 Web 请求路径调用 Yahoo、AKShare 或其他外部网络服务。
- 不承诺报价无延迟；页面必须明确展示来源和快照新鲜度。

## 设计决策

- 使用 `MarketQuoteSnapshot` 独立于 A 股 `QuoteSnapshot`，避免不同市场代码碰撞，也保留货币、时区、来源等展示元数据。
- 采集器按指数逐个请求，单个源失败不清空旧快照；旧数据通过页面上的过期标记与更新时间区分。
- 采集器不依赖 A 股交易时段，默认每 300 秒轮询；A 股 `monitor.py` 仍只负责 A 股交易时段和告警。
- Yahoo Chart API 是无需凭据的公开报价接口，数据可能延迟、限流或临时不可用；所有网络请求设置有限超时，不引入无限等待。

## 数据/API 变化

- 新增 `market_quote_snapshots(market, symbol, name, price, change_pct, currency, quote_at, market_at, source)`，以 `(market, symbol)` 为联合主键。
- 新增 `global_market_data.py`：默认市场定义、Yahoo 报价解析和快照持久化辅助函数。
- 新增 `market_monitor.py`：`--once`、`--test` 和持续轮询入口。
- 新增 `GET /markets`：认证后的多市场只读页面。
- 新增配置段 `global_markets`，并在 `config.yaml.example` 中提供示例。

## 风险与处理

- 外部源字段和可用性变化：严格校验价格、前收和时间戳；单个指数失败只记录日志并保留已有数据库记录。
- Yahoo 可能限流：默认 300 秒间隔、每次请求有限超时；不在 Web 侧重试。
- 跨时区展示容易误导：页面同时显示本地市场时间、上海时间快照时间和“可能延迟”提示。
- 旧数据库不会自动迁移历史数据：SQLAlchemy `create_all` 只新增表，不修改既有表。

## 验收标准

- 未配置全球市场时，现有 A 股页面、监控、Bot 和测试行为不变。
- 使用 mock 响应可验证六个指数的解析、失败隔离和快照 upsert，不依赖真实凭据或网络。
- 认证用户访问 `/markets` 可看到四个市场分组；无快照、过期快照和正负涨跌均有明确显示。
- 采集器单次运行失败时退出可诊断，持续运行会按间隔重试，不抛出未处理异常退出。
- Ruff、单元测试和编译检查通过。

## 验证命令

```bash
ruff check .
python -m unittest discover -s tests -v
python -m compileall -q .
```

## 实施记录与验证结果

- 新增 `MarketQuoteSnapshot` 表和 `global_market_data.py`，默认覆盖恒生、KOSPI、标普 500、纳斯达克、道指和日经 225；按 `(market, symbol)` 原子 upsert，失败时不删除旧快照。
- 新增 `market_monitor.py`，采集进程独立于 A 股交易时段运行；`main.py --markets` 和 `deploy/ashare_market_monitor.service` 可单独启动。
- 新增认证 `/markets` 页面、导航入口和响应式卡片布局；页面仅读取 SQLite，并显示来源、市场本地时间、上海采集时间和过期标记。
- 新增 `global_markets` 配置模板、配置校验、部署文档和离线测试；可关闭市场或覆盖每个市场的指数列表。
- mock 与页面测试覆盖：六指数默认定义、Yahoo 字段降级解析、单指数失败隔离、联合键 upsert、正负涨跌、过期快照、认证和数据卡片渲染。
- 验证结果：`ruff check .` 通过；`python -m unittest discover -s tests -v` 通过，共 70 项；`PYTHONPYCACHEPREFIX=/private/tmp/ashare-pycache python -m compileall -q .` 通过；`git diff --check` 通过。
- 2026-07-30 真实只读 Yahoo 请求 smoke test 返回 6/6 个指数、0 个错误；报价仍可能延迟或受限流影响，页面已明确提示。
- 本机浏览器策略拒绝访问 `127.0.0.1`，因此未做浏览器截图验证；FastAPI `TestClient` 已完成认证和带数据模板渲染验证。macOS 开发环境未安装 `systemd-analyze`，systemd 单元需在部署机验证。
