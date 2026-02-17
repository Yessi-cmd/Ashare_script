# 🚀 快速开始指南

## 第一步：推送到 GitHub

你的 SSH 密钥已配置（id_ed25519），可以直接推送：

```bash
cd /Users/zhongyan/Code/Playground/Ashare_script

# 运行推送脚本（会引导你完成所有步骤）
./push_to_github.sh
```

脚本会提示你：
1. 输入 GitHub 用户名和邮箱
2. 在浏览器创建新仓库
3. 自动推送代码

**手动推送（如果脚本有问题）**：
```bash
# 1. 配置 git
git config --global user.name "你的名字"
git config --global user.email "你的邮箱"

# 2. 提交
git commit -m "Initial commit: A股行情监控 V2"

# 3. 切换分支
git branch -M main

# 4. 在 GitHub 网页创建仓库后，运行：
git remote add origin git@github.com:你的用户名/Ashare_script.git
git push -u origin main
```

---

## 第二步：配置 Bot 权限

打开 `bot.py`，找到第 42 行：

```python
ALLOWED_USERS = [
    # 465948141,  # 示例：替换为你的 Telegram User ID
]
```

**获取你的 Telegram User ID：**
1. 在 Telegram 搜索 `@userinfobot`
2. 向它发送任意消息
3. 它会回复：`Id: 123456789` ← 这就是你的 User ID
4. 填入 `ALLOWED_USERS`：
   ```python
   ALLOWED_USERS = [
       123456789,  # 你的 User ID
   ]
   ```

**权限说明：**
- 如果 `ALLOWED_USERS` 为空 → 任何人都能用（会有告警）
- 如果填了 User ID → 只有你能用，其他人会收到 "⛔ 无权限"

---

## 第三步：测试 Bot

```bash
# 安装 telegram bot 依赖（如果还没装）
cd /Users/zhongyan/Code/Playground/Ashare_script
source venv/bin/activate
pip install python-telegram-bot

# 启动 Bot
python bot.py
```

在 Telegram 中测试：
```
/start              # 查看帮助
/add 600519 1500 100  # 添加贵州茅台持仓
/list               # 查看持仓
```

**如果收到 "⛔ 无权限"**：
- 检查你的 User ID 是否正确填入 `ALLOWED_USERS`
- 重启 Bot

---

## 部署到服务器（可选）

参考 [deploy/README_deploy.md](deploy/README_deploy.md)
