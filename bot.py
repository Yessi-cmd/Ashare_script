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

import asyncio
import logging
import sys
from functools import wraps

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# 导入新闻模块
from news import get_morning_news, get_evening_news, get_instant_news

# 导入用户配置模块
from settings import ConfigError, get_owner_user_id, load_config
from user_config import load_user_config, save_user_config

# ── 日志 ──────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── 权限控制 ──────────────────────────────────────────────────

# 个人模式只允许配置的 owner 使用。
OWNER_USER_ID = None

def check_permission(user_id: int) -> bool:
    """检查用户是否有权限"""
    return OWNER_USER_ID is not None and user_id == OWNER_USER_ID


def owner_only(handler):
    """Restrict a Telegram command handler to the configured owner."""
    @wraps(handler)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user is None or not check_permission(user.id):
            if update.message:
                await update.message.reply_text("⛔ 无权限使用此 Bot")
            return
        return await handler(update, context)
    return wrapped


# ── 辅助函数 ──────────────────────────────────────────────────

def normalize_stock_code(value: str) -> str:
    """Validate and normalize an A-share code."""
    value = value.strip()
    if not value.isdigit() or not 1 <= len(value) <= 6:
        raise ValueError("股票代码必须是 1-6 位数字")
    return value.zfill(6)


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

@owner_only
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
        "*新闻资讯:*\n"
        "/news - 即时财经快讯\n"
        "/morning - 早间新闻（外盘+快讯）\n"
        "/evening - 晚间总结（收盘+资金）\n\n"
        "*监控:*\n"
        "/status - 查看监控运行状态\n"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


@owner_only
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """添加持仓"""
    user_id = update.effective_user.id

    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "❌ 用法: /add <代码> <买入价> <股数> [止损%] [止盈%]\n"
            "例: /add 600519 1500 100 -5 10"
        )
        return

    try:
        code = normalize_stock_code(args[0])
        buy_price = float(args[1])
        shares = int(args[2])
        stop_loss = float(args[3]) if len(args) > 3 else -5.0
        take_profit = float(args[4]) if len(args) > 4 else 10.0
    except ValueError:
        await update.message.reply_text("❌ 股票代码、价格或股数格式不正确")
        return

    if buy_price <= 0 or shares <= 0 or stop_loss >= 0 or take_profit <= 0:
        await update.message.reply_text("❌ 买入价和股数必须为正，止损必须为负，止盈必须为正")
        return

    # 获取股票名称
    name = await asyncio.to_thread(get_stock_name, code)

    # 加载用户配置
    config = load_user_config(user_id)

    # 添加持仓
    config["portfolio"][code] = {
        "name": name,
        "buy_price": buy_price,
        "shares": shares,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }

    save_user_config(user_id, config)

    msg = (
        f"✅ 已添加持仓: *{name}({code})*\n"
        f"买入价 ¥{buy_price:.2f} × {shares}股\n"
        f"止损 {stop_loss:+.1f}% / 止盈 {take_profit:+.1f}%"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


@owner_only
async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """删除持仓"""
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text("❌ 用法: /remove <代码>")
        return

    try:
        code = normalize_stock_code(context.args[0])
    except ValueError as exc:
        await update.message.reply_text(f"❌ {exc}")
        return
    config = load_user_config(user_id)

    if code not in config["portfolio"]:
        await update.message.reply_text(f"❌ 未找到持仓: {code}")
        return

    name = config["portfolio"][code].get("name", code)
    del config["portfolio"][code]
    save_user_config(user_id, config)

    await update.message.reply_text(f"✅ 已删除持仓: *{name}({code})*", parse_mode="Markdown")


@owner_only
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """列出所有持仓"""
    user_id = update.effective_user.id

    config = load_user_config(user_id)
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


@owner_only
async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """添加关注股票"""
    user_id = update.effective_user.id

    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ 用法: /watch <代码> <名称>\n"
            "例: /watch 300750 宁德时代"
        )
        return

    try:
        code = normalize_stock_code(context.args[0])
    except ValueError as exc:
        await update.message.reply_text(f"❌ {exc}")
        return
    name = " ".join(context.args[1:])

    config = load_user_config(user_id)
    config["watchlist"][code] = name
    save_user_config(user_id, config)

    await update.message.reply_text(
        f"✅ 已添加关注: *{name}({code})*",
        parse_mode="Markdown"
    )


@owner_only
async def cmd_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """删除关注股票"""
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text("❌ 用法: /unwatch <代码>")
        return

    try:
        code = normalize_stock_code(context.args[0])
    except ValueError as exc:
        await update.message.reply_text(f"❌ {exc}")
        return
    config = load_user_config(user_id)

    if code not in config.get("watchlist", {}):
        await update.message.reply_text(f"❌ 未找到关注股票: {code}")
        return

    name = config["watchlist"][code]
    del config["watchlist"][code]
    save_user_config(user_id, config)

    await update.message.reply_text(f"✅ 已删除关注: *{name}({code})*", parse_mode="Markdown")


@owner_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看监控状态"""
    user_id = update.effective_user.id
    
    # 读取用户的持仓和关注池数量
    config = load_user_config(user_id)
    portfolio_count = len(config.get("portfolio", {}))
    watchlist_count = len(config.get("watchlist", {}))
    
    # 读取全局配置获取通知渠道
    global_config = load_config()
    notif = global_config.get("notification", {})
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


# ── 新闻命令 ──────────────────────────────────────────────────

@owner_only
async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """即时新闻"""
    await update.message.reply_text("📰 正在获取最新资讯...")
    try:
        news = await asyncio.to_thread(get_instant_news)
        await update.message.reply_text(news)
    except Exception as e:
        logger.error(f"获取新闻失败: {e}")
        await update.message.reply_text("❌ 获取新闻失败，请稍后重试")


@owner_only
async def cmd_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """早间新闻"""
    await update.message.reply_text("🌅 正在获取早间新闻...")
    try:
        news = await asyncio.to_thread(get_morning_news)
        await update.message.reply_text(news)
    except Exception as e:
        logger.error(f"获取早间新闻失败: {e}")
        await update.message.reply_text("❌ 获取新闻失败，请稍后重试")


@owner_only
async def cmd_evening(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """晚间新闻"""
    await update.message.reply_text("🌆 正在获取晚间总结...")
    try:
        news = await asyncio.to_thread(get_evening_news)
        await update.message.reply_text(news)
    except Exception as e:
        logger.error(f"获取晚间新闻失败: {e}")
        await update.message.reply_text("❌ 获取新闻失败，请稍后重试")


# ── 主程序 ────────────────────────────────────────────────────

def main():
    # 加载配置获取 Bot Token
    try:
        config = load_config()
    except ConfigError as exc:
        logger.error(f"❌ {exc}")
        sys.exit(2)

    global OWNER_USER_ID
    OWNER_USER_ID = get_owner_user_id(config)
    if OWNER_USER_ID is None:
        logger.error("❌ 请在 config.yaml 配置 app.owner_user_id 后再启动 Bot")
        sys.exit(2)
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
    # 新闻命令
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("morning", cmd_morning))
    app.add_handler(CommandHandler("evening", cmd_evening))

    # 启动
    logger.info("🤖 Telegram Bot 启动成功")
    logger.info("发送 /start 查看帮助")
    app.run_polling()


if __name__ == "__main__":
    main()
