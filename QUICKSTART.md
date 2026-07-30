# 快速开始

## 1. 安装与配置

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.yaml.example config.yaml
```

编辑 `config.yaml`。使用 Telegram Bot 时必须把 `app.owner_user_id` 设置为自己的 Telegram User ID；不使用 Bot 时保持为空，并直接填写 YAML 中的 `portfolio` 和 `watchlist`。

## 2. 本地检查

```bash
python monitor.py --test
python monitor.py --once
python bot.py
```

`--test` 不发送通知。Bot 未配置所有者时会安全退出。

## 3. 浏览器仪表盘

```bash
export ASHARE_WEB_USERNAME='owner'
export ASHARE_WEB_PASSWORD='使用长随机密码'
python web_app.py
```

浏览 `http://127.0.0.1:8000`。监控进程运行一轮后，首页会显示本地持仓/关注池行情快照；Web 自身不会调用 AKShare。

## 4. 日线与回测

```bash
python research.py sync 600519 --start 2024-01-01
python research.py backtest 600519 --strategy v2 --start 2024-01-01
python research.py compare 000001 600519 300750 --start 2024-01-01
```

V3 是未晋级的研究候选，不会自动用于实时告警。详细样本外报告见 [docs/roadmap/02-strategy-engine.md](docs/roadmap/02-strategy-engine.md)。

## 5. 质量与性能检查

```bash
pip install -r requirements-dev.txt
ruff check .
python -m unittest discover -s tests -v
python performance_benchmark.py --codes 000001 600519 300750
```

## 6. 服务器

部署文件覆盖 monitor、bot、web、收盘同步和每日备份。推荐 Tailscale 私网访问；完整步骤见 [deploy/README_deploy.md](deploy/README_deploy.md)。
