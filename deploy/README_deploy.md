# A股行情监控 - 部署指南

## 📋 前置要求

- Python 3.10+
- pip
- systemd (Linux)

## 🚀 东京服务器部署步骤

### 1. 上传代码

```bash
# 在服务器上创建目录
ssh your_tokyo_server
mkdir -p /opt/ashare_monitor
```

```bash
# 从本地上传代码
scp -r ./* your_tokyo_server:/opt/ashare_monitor/
```

### 2. 创建 Python 虚拟环境

```bash
cd /opt/ashare_monitor
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. 配置

```bash
# 编辑配置文件，填入你的股票池和通知渠道
vim config.yaml
```

**重点配置项：**
- `stocks`: 添加需要监控的股票代码
- `notification.telegram`: 填入 Bot Token 和 Chat ID
- `strategies`: 调整监控策略参数

### 4. 测试运行

```bash
# 测试数据获取和策略检测（不发送通知）
python monitor.py --test

# 测试通知发送
python notifier.py
```

### 5. 设置时区

确保服务器时区为 `Asia/Shanghai` 或 `Asia/Tokyo`（脚本内部使用北京时间判断交易时段）：

```bash
sudo timedatectl set-timezone Asia/Shanghai
```

### 6. 安装 systemd 服务

```bash
# 复制服务文件
sudo cp deploy/ashare_monitor.service /etc/systemd/system/

# 重新加载 systemd
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start ashare_monitor

# 设为开机自启
sudo systemctl enable ashare_monitor

# 查看状态
sudo systemctl status ashare_monitor
```

### 7. 查看日志

```bash
# 实时查看日志
journalctl -u ashare_monitor -f

# 查看最近 100 行
journalctl -u ashare_monitor -n 100

# 也可以查看文件日志
tail -f /opt/ashare_monitor/monitor.log
```

## 🔧 常用操作

```bash
# 重启服务
sudo systemctl restart ashare_monitor

# 停止服务
sudo systemctl stop ashare_monitor

# 单次运行（手动触发一次检测）
cd /opt/ashare_monitor && source venv/bin/activate
python monitor.py --once
```

## 📱 Telegram Bot 创建指南

1. 在 Telegram 搜索 `@BotFather`
2. 发送 `/newbot`，按提示创建 Bot
3. 记录返回的 **Bot Token**
4. 向你的 Bot 发送一条消息
5. 访问 `https://api.telegram.org/bot<TOKEN>/getUpdates` 获取 **Chat ID**
6. 将 Token 和 Chat ID 填入 `config.yaml`

## 🤖 启动 Telegram Bot 管理持仓

Bot 和主程序可以同时运行，互不干扰：

```bash
# 启动 Bot（后台运行）
cd /opt/ashare_monitor && source venv/bin/activate
nohup python bot.py > bot.log 2>&1 &

# Bot 使用（在 Telegram 中）
/add 600519 1500 100        # 添加持仓
/list                       # 查看持仓
/remove 600519              # 删除持仓
/watch 300750 宁德时代      # 添加关注
/status                     # 查看状态
```

**自动启动 Bot (可选)**:
```bash
# 创建 bot 服务文件
sudo cp deploy/ashare_bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ashare_bot
sudo systemctl start ashare_bot
```
