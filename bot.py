#!/usr/bin/env python3
"""
A股行情监控 - Telegram Bot 持仓管理
通过 Telegram 命令交互式管理持仓，无需手动编辑配置文件。

命令列表:
  /start        - 显示帮助
  /add <code> <price> <shares> [stop_loss] [take_profit]
                - 添加持仓
  /remove <code> - 删除持仓
  /list         - 查看所有持仓
  /watch <code> <name> - 添加关注股票
  /unwatch <code> - 删除关注股票
  /status       - 查看监控状态
"""

import logging
import os
import sys
from typing import Optional

import yaml
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# ── 日志 ──────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── 配置文件路径 ──────────────────────────────────────────────

CONFIG_FILE = "config.yaml"


# ── 辅助函数 ──────────────────────────────────────────────────

def load_config() -> dict:
    """加载配置文件"""
    if not os.path.exists(CONFIG_FILE):
        logger.error(f"配置文件不存在: {CONFIG_FILE}")
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(config: dict):
    """保存配置文件"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
    logger.info("配置已保存")


def get_stock_name(code: str) -> str:
    """从 AKShare 获取股票名称（简化版，可选）"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        df["代码"] = df["代码"].astype(str).str.zfill(6)
        match = df[df["代码"] == code]
        if not match.empty:
            return match.iloc[0]["名称"]
    except Exception as e:
        logger.warning(f"获取股票名称失败: {e}")
    return code


# ── Bot 命令处理 ──────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示帮助信息"""
    help_text = (
        "📊 *A股行情监控 Bot*\n\n"
        "*持仓管理:*\n"
        "/add `<代码>` `<买入价>` `<股数>` `[止损%]` `[止盈%]`\n"
        "  例: `/add 600519 1500 100 -5 10`\n\n"
        "/remove `<代码>` - 删除持仓\n"
        "/list - 查看所有持仓\n\n"
        "*关注池:*\n"
        "/watch `<代码>` `<名称>` - 添加关注\n"
        "  例: `/watch 300750 宁德时代`\n\n"
        "/unwatch `<代码>` - 删除关注\n\n"
        "*监控:*\n"
        "/status - 查看监控运行状态\n"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """添加持仓"""
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "❌ 用法: /add <代码> <买入价> <股数> [止损%] [止盈%]\n"
            "例: /add 600519 1500 100 -5 10"
        )
        return

    code = args[0].zfill(6)
    try:
        buy_price = float(args[1])
        shares = int(args[2])
        stop_loss = float(args[3]) if len(args) > 3 else -5.0
        take_profit = float(args[4]) if len(args) > 4 else 10.0
    except ValueError:
        await update.message.reply_text("❌ 价格和股数必须是数字")
        return

    # 获取股票名称
    name = get_stock_name(code)

    # 加载配置
    config = load_config()
    if "portfolio" not in config:
        config["portfolio"] = {}

    # 添加持仓
    config["portfolio"][code] = {
        "name": name,
        "buy_price": buy_price,
        "shares": shares,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }

    save_config(config)

    msg = (
        f"✅ 已添加持仓: *{name}({code})*\n"
        f"买入价 ¥{buy_price:.2f} × {shares}股\n"
        f"止损 {stop_loss:+.1f}% / 止盈 {take_profit:+.1f}%"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """删除持仓"""
    if not context.args:
        await update.message.reply_text("❌ 用法: /remove <代码>")
        return

    code = context.args[0].zfill(6)
    config = load_config()

    if "portfolio" not in config or code not in config["portfolio"]:
        await update.message.reply_text(f"❌ 未找到持仓: {code}")
        return

    name = config["portfolio"][code].get("name", code)
    del config["portfolio"][code]
    save_config(config)

    await update.message.reply_text(f"✅ 已删除持仓: *{name}({code})*", parse_mode="Markdown")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """列出所有持仓"""
    config = load_config()
    portfolio = config.get("portfolio", {})

    if not portfolio:
        await update.message.reply_text("📭 当前没有持仓")
        return

    lines = ["💼 *我的持仓*\n"]
    for code, info in portfolio.items():
        name = info.get("name", code)
        buy_price = info.get("buy_price", 0)
        shares = info.get("shares", 0)
        stop_loss = info.get("stop_loss", -5)
        take_profit = info.get("take_profit", 10)

        lines.append(
            f"• *{name}({code})*\n"
            f"  买入价 ¥{buy_price:.2f} × {shares}股\n"
            f"  止损 {stop_loss:+.1f}% / 止盈 {take_profit:+.1f}%"
        )

    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """添加关注股票"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ 用法: /watch <代码> <名称>\n"
            "例: /watch 300750 宁德时代"
        )
        return

    code = context.args[0].zfill(6)
    name = " ".join(context.args[1:])

    config = load_config()
    if "watchlist" not in config:
        config["watchlist"] = {}

    config["watchlist"][code] = name
    save_config(config)

    await update.message.reply_text(
        f"✅ 已添加关注: *{name}({code})*",
        parse_mode="Markdown"
    )


async def cmd_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """删除关注股票"""
    if not context.args:
        await update.message.reply_text("❌ 用法: /unwatch <代码>")
        return

    code = context.args[0].zfill(6)
    config = load_config()

    if "watchlist" not in config or code not in config["watchlist"]:
        await update.message.reply_text(f"❌ 未找到关注股票: {code}")
        return

    name = config["watchlist"][code]
    del config["watchlist"][code]
    save_config(config)

    await update.message.reply_text(f"✅ 已删除关注: *{name}({code})*", parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看监控状态"""
    config = load_config()
    portfolio_count = len(config.get("portfolio", {}))
    watchlist_count = len(config.get("watchlist", {}))

    notif = config.get("notification", {})
    channels = []
    if notif.get("telegram", {}).get("enabled"):
        channels.append("Telegram")
    if notif.get("dingtalk", {}).get("enabled"):
        channels.append("钉钉")
    if notif.get("email", {}).get("enabled"):
        channels.append("邮件")

    notif_status = "、".join(channels) if channels else "未启用"

    msg = (
        "📊 *监控状态*\n\n"
        f"持仓股票: {portfolio_count} 只\n"
        f"关注股票: {watchlist_count} 只\n"
        f"通知渠道: {notif_status}\n\n"
        "提示: 主程序需单独运行 `python monitor.py`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


# ── 主程序 ────────────────────────────────────────────────────

def main():
    # 加载配置获取 Bot Token
    config = load_config()
    bot_token = config.get("notification", {}).get("telegram", {}).get("bot_token")

    if not bot_token or bot_token == "YOUR_BOT_TOKEN":
        logger.error("❌ 未配置 Telegram Bot Token，请在 config.yaml 中填入")
        sys.exit(1)

    # 创建 Bot
    app = ApplicationBuilder().token(bot_token).build()

    # 注册命令
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("status", cmd_status))

    # 启动
    logger.info("🤖 Telegram Bot 启动成功")
    logger.info("发送 /start 查看帮助")
    app.run_polling()


if __name__ == "__main__":
    main()
