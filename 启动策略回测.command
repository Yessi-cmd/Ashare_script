#!/bin/bash
# 双击启动 Web 仪表盘（macOS .command 文件）

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 停掉旧进程
pkill -f "web_app.py" 2>/dev/null && echo "已停止旧进程" && sleep 1

export ASHARE_WEB_USERNAME="admin"
export ASHARE_WEB_PASSWORD="admin123"
export ASHARE_WEB_SESSION_SECRET="dev-secret-change-in-production"

PYTHON="$SCRIPT_DIR/venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "❌ 找不到 venv，请先执行: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    read -p "按回车退出..."
    exit 1
fi

clear
echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║     🔬 个人研究台 · Web 仪表盘       ║"
echo "  ╚══════════════════════════════════════╝"
echo ""
echo "   ✅ 服务启动中..."
echo "   ──────────────────────────────────"
echo "   登录地址:  http://127.0.0.1:8000"
echo "   策略回测:  http://127.0.0.1:8000/strategy"
echo "   ──────────────────────────────────"
echo "   用户名:    ${ASHARE_WEB_USERNAME}"
echo "   密码:      ${ASHARE_WEB_PASSWORD}"
echo "   ──────────────────────────────────"
echo ""
echo "   按 Ctrl+C 停止"
echo ""

sleep 2
open http://127.0.0.1:8000/strategy

exec "$PYTHON" web_app.py
