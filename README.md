# 📊 A股个人研究与监控平台

> 智能监控 A股行情，自动评估买卖时机，持仓止盈止损提醒，通过 Telegram/钉钉/邮件推送通知。

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## ✨ 核心功能

### 🎯 智能买入信号
- **0-100 分综合评分系统**：基于 RSI、MACD、均线、成交量等指标
- **白话文推荐**：🟢 买入 / 🟡 观望 / 🔴 远离，不需要懂技术分析
- **评分理由解释**："处于超卖区域，有反弹可能"

### 💼 持仓管理
- **个人单用户模式**：监控仅绑定一个 Telegram User ID，避免误用其他数据
- **SQLite 数据库存储**：可靠、高效、并发安全
- **止盈止损自动提醒**：设定百分比阈值，触发时推送通知
- **实时盈亏追踪**：持仓总盈亏、单只股票盈亏实时展示
- **Telegram Bot 交互管理**：命令行添加/删除持仓，无需改配置文件

### 📰 新闻资讯
- **早间简报**：隔夜外盘 + 财经快讯
- **晚间总结**：A股收盘 + 涨跌停统计 + 资金流向
- **即时快讯**：实时财经新闻推送
- **RSS 智能降级**：API 超时自动切换 RSS 源

### 📅 智能调度
- **内置 A股节假日日历**：自动跳过春节、国庆等休市日期
- **交易时段检测**：仅在 9:15-11:30、13:00-15:00 运行
- **告警去重**：同一股票同一策略 5 分钟内不重复通知

### 🔔 多渠道通知
- Telegram Bot（推荐，海外服务器直连）
- 钉钉机器人 Webhook
- 邮件 SMTP

### 🧪 可审计策略研究
- 本地 qfq 日线仓库与显式 raw 指数数据源
- 无未来函数、次日开盘成交、包含佣金/印花税/滑点的回测器
- 横截面选股研究候选：同日因子排名、Top-K 限仓、定期调仓和组合净值
- 训练集选参、严格样本外验证、跨股票与滚动窗口对照
- V3 与横截面候选目前均为研究候选；因稳定性未达标，不会替换实时 V2

### 🌐 个人 Web 仪表盘
- FastAPI + Jinja2 + ECharts；行情和研究页面只读本地快照与日线
- 中文登录页与最长 30 天签名会话，持仓盈亏、K 线和系统状态
- 19 股本地可解释荐股榜：入选理由、反对理由、风险与观察失效位
- 可收藏 URL 的组合自助选股器；明确研究范围，不冒充全市场扫描

### 💰 实时模拟盘
- 首次访问自动创建 10,000 元模拟账户，可提交任意 A 股市价买卖委托
- 监控进程用同一轮实时行情撮合，模拟持仓继续显示 V2 评分与信号阈值
- 100 股整手、T+1、资金与可卖数量校验，并计入佣金、过户费和印花税
- 现金、持仓、盈亏、订单历史与手工登记实盘并列对照；不连接券商、不自动下单

### 🌏 多市场行情面板
- `/markets` 展示港股恒生、韩国 KOSPI、美股标普/纳指/道指、日本日经 225
- 独立采集进程按配置间隔写入本地快照，Web 页面不直接请求外网
- 使用 Yahoo Finance Chart API，页面明确标注可能延迟和数据新鲜度

## 🚀 快速开始

### 1. 安装依赖

```bash
git clone https://github.com/YOUR_USERNAME/Ashare_script.git
cd Ashare_script
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置

复制配置模板并编辑：

```bash
cp config.yaml.example config.yaml
vim config.yaml
```

**必填配置项**：
- 本地模式：填写 `portfolio` 和 `watchlist`
- Bot 模式：填写 `app.owner_user_id`，持仓和关注池通过 Bot 保存到 SQLite
- `notification`: 至少启用一个通知渠道

两种持仓来源不会混用：配置了 `app.owner_user_id` 后，监控只读取该用户的数据库记录；保持 `null` 时只读取 YAML 股票池。

### 3. 运行

```bash
# 测试模式（不发送通知）
python monitor.py --test

# 单次运行（检测一次后退出）
python monitor.py --once

# 持续监控
python monitor.py

# 跨市场指数采集（独立于 A股交易时段）
python market_monitor.py
python market_monitor.py --once
python market_monitor.py --test
```

### 4. Telegram Bot 管理持仓（可选）

```bash
# 启动 Bot（后台运行）
python bot.py &

# 在 Telegram 中使用
/add 600519 1500 100       # 添加持仓：贵州茅台 买入价1500 100股
/list                      # 查看所有持仓
/remove 600519             # 删除持仓
/status                    # 查看监控状态
```

启动 Bot 前必须将 `app.owner_user_id` 设置为你自己的 Telegram User ID；未配置时 Bot 会安全退出，不再默认允许所有人使用。

### 5. 启动 Web 与模拟盘（可选）

```bash
export ASHARE_WEB_USERNAME='你的用户名'
export ASHARE_WEB_PASSWORD='密码管理器生成的长随机密码'
export ASHARE_WEB_SESSION_SECRET='另一段至少32字节的随机值'
python web_app.py
```

打开 `http://127.0.0.1:8000/paper`。模拟委托由持续运行的 `monitor.py` 在交易时段下一轮行情中处理；`--test` 模式不会成交。开发机之外不要直接暴露 8000；服务器步骤见 [部署指南](deploy/README_deploy.md)。

### 6. 策略研究

```bash
# 同步本地日线
python research.py sync 600519 --start 2024-01-01 --end 2026-07-17

# 运行 V2/V3 回测
python research.py backtest 600519 --strategy v2 --start 2024-01-01

# 训练集选阈值，再评估后续验证集
python research.py walk-forward 000001 600519 300750 \
  --train-start 2024-01-01 --train-end 2025-06-30 \
  --validation-start 2025-07-01 --validation-end 2026-07-17 \
  --strategy v3 --thresholds 60,65,70,75,80

# 横截面 Top-K 组合回测（只读本地缓存）
python research.py portfolio-backtest 000001 000333 000858 002415 002475 \
  002594 300059 300124 300750 600030 600036 600276 600519 601012 \
  601088 601318 601899 603259 688981 \
  --top-n 3 --rebalance-every 5 --benchmark-code 000300
```

横截面候选只用于研究，不会自动接入实时告警；完整设计、实验结果和限制见 [升级路线](docs/roadmap/README.md)。

## 📋 示例输出

### 持仓仪表盘
```
💼 我的持仓
五粮液     ¥106.06  🔴+1.38%   持仓🔴+1.0%   58分
贵州茅台   ¥1485.30 🟢-0.09%   持仓🟢-1.0%   53分
                         持仓总盈亏: 🟢-940元
```

### 关注池评分
```
👀 关注池
比亚迪     ¥90.27   61分  🟡 观望
中国平安   ¥65.29   20分  🔴 远离（理由：向下趋势转折 + 跌破均线）
```

### 告警通知
```
🔴 远离信号 | 中国平安(601318)
现价 ¥65.29 | 评分 20/100
理由：近期出现向下趋势转折；跌破所有均线，形态偏弱

🚨 止损警告 | 贵州茅台(600519)
现价 ¥1425.00 | 买入价 ¥1500.00
亏损 -5.0%（-¥7,500）
⚡ 建议立即卖出止损！
```

## 🏗️ 项目结构

```
Ashare_script/
├── main.py             # 主入口（一键启动所有服务）
├── monitor.py          # 主监控程序
├── market_monitor.py   # 港股/韩国/美股/日本指数采集
├── bot.py              # Telegram Bot 交互管理
├── database.py         # SQLAlchemy ORM 模型
├── user_config.py      # 用户配置数据库访问层
├── settings.py         # 配置加载与启动校验
├── strategies.py       # 评分系统 + 止盈止损
├── market_data.py      # 本地日线仓库和外部源降级
├── backtest_engine.py  # 无未来函数回测器
├── strategy_v3.py      # 多因子研究候选（未上线）
├── cross_sectional_strategy.py # 同日因子排名研究候选
├── portfolio_backtest.py # 有限持仓组合回测
├── walk_forward.py     # 训练/验证分离
├── research.py         # 研究 CLI
├── paper_trading.py    # 模拟账户、费用、T+1、委托与撮合
├── web_app.py          # 本地行情研究与模拟盘 Web
├── snapshot_store.py   # 监控到 Web 的本地快照
├── global_market_data.py # 跨市场定义、解析和快照存储
├── notifier.py         # 通知模块
├── news.py             # 新闻资讯模块
├── holidays.py         # A股节假日日历
├── config.yaml         # 全局配置文件（不提交到 Git）
├── config.yaml.example # 配置模板
├── requirements.txt    # Python 依赖
├── ashare_monitor.db   # SQLite 数据库（不提交到 Git）
└── deploy/
    ├── ashare_monitor.service  # systemd 服务文件
    ├── ashare_bot.service      # Bot 服务文件
    ├── ashare_web.service      # Web 服务文件
    ├── ashare_market_monitor.service # 跨市场指数采集
    ├── ashare_sync.timer       # 收盘后日线同步
    ├── ashare_backup.timer     # SQLite 在线备份
    └── README_deploy.md        # 部署指南
```

## ✅ 自动化测试

```bash
source venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
python -m unittest discover -s tests -v
```

## 🌐 服务器部署

支持普通 Linux 服务器，优先使用 Tailscale 私网 HTTPS；无法安装客户端时使用 Caddy + Cloudflare Access。详见 [部署指南](deploy/README_deploy.md)。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 开源协议

[MIT License](LICENSE)

## ⚠️ 免责声明

本项目仅供学习交流和个人投资参考使用，不构成任何投资建议。股市有风险，投资需谨慎。

## 🙏 致谢

- 数据源：[AKShare](https://github.com/akfamilygroup/akshare)
- 通知：Telegram Bot API / 钉钉开放平台
