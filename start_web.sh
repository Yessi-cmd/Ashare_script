#!/bin/bash
# 一键启动 Web 仪表盘（含策略回测页面）
# 用法: ./start_web.sh

set -e

cd "$(dirname "$0")"

# 开发环境默认凭证（可修改）
export ASHARE_WEB_USERNAME="${ASHARE_WEB_USERNAME:-admin}"
export ASHARE_WEB_PASSWORD="${ASHARE_WEB_PASSWORD:-admin123}"
export ASHARE_WEB_SESSION_SECRET="${ASHARE_WEB_SESSION_SECRET:-dev-secret-change-in-production}"

echo "══════════════════════════════════════"
echo "  🔬 个人研究台 · Web 仪表盘"
echo "══════════════════════════════════════"
echo "  用户名: $ASHARE_WEB_USERNAME"
echo "  密码:   $ASHARE_WEB_PASSWORD"
echo "  地址:   http://127.0.0.1:8000"
echo "══════════════════════════════════════"
echo "  策略回测: http://127.0.0.1:8000/strategy"
echo "══════════════════════════════════════"
echo ""

# 自动激活 venv（如果存在且未激活）
if [ -z "$VIRTUAL_ENV" ] && [ -f venv/bin/activate ]; then
    source venv/bin/activate
fi

python web_app.py
