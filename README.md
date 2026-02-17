# 📊 A股行情监控 V2

> 智能监控 A股行情，自动评估买卖时机，持仓止盈止损提醒，通过 Telegram/钉钉/邮件推送通知。

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## ✨ 核心功能

### 🎯 智能买入信号
- **0-100 分综合评分系统**：基于 RSI、MACD、均线、成交量等指标
- **白话文推荐**：🟢 买入 / 🟡 观望 / 🔴 远离，不需要懂技术分析
- **评分理由解释**："处于超卖区域，有反弹可能"

### 💼 持仓管理
- **多用户支持**：每个用户独立持仓，数据隔离
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
- `portfolio`: 你的持仓（代码、买入价、股数、止损止盈阈值）
- `watchlist`: 关注池（不持仓但想监控的股票）
- `notification`: 至少启用一个通知渠道

### 3. 运行

```bash
# 测试模式（不发送通知）
python monitor.py --test

# 单次运行（检测一次后退出）
python monitor.py --once

# 持续监控
python monitor.py
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
├── bot.py              # Telegram Bot 交互管理
├── database.py         # SQLAlchemy ORM 模型
├── user_config.py      # 用户配置数据库访问层
├── strategies.py       # 评分系统 + 止盈止损
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
    └── README_deploy.md        # 部署指南
```

## 🌐 服务器部署

**推荐部署到东京服务器**（延迟低 ~30-60ms，时区友好）

详见 [部署指南](deploy/README_deploy.md)

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 开源协议

[MIT License](LICENSE)

## ⚠️ 免责声明

本项目仅供学习交流和个人投资参考使用，不构成任何投资建议。股市有风险，投资需谨慎。

## 🙏 致谢

- 数据源：[AKShare](https://github.com/akfamilygroup/akshare)
- 通知：Telegram Bot API / 钉钉开放平台
