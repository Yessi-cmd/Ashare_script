"""
A股行情监控 V2 - 通知模块
支持: Telegram Bot / 钉钉机器人 / 邮件 SMTP
消息格式为用户友好的白话中文
"""

import hashlib
import hmac
import base64
import json
import logging
import smtplib
import time
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
import yaml

logger = logging.getLogger(__name__)


# ── Telegram ──────────────────────────────────────────────────

def send_telegram(message: str, config: dict) -> bool:
    """通过 Telegram Bot 发送消息"""
    if not config.get("enabled"):
        return False

    bot_token = config["bot_token"]
    chat_id = config["chat_id"]
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info("Telegram 消息发送成功")
            return True
        else:
            logger.error(f"Telegram 发送失败: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Telegram 发送异常: {e}")
        return False


# ── 钉钉 ──────────────────────────────────────────────────────

def _dingtalk_sign(secret: str) -> tuple[str, str]:
    """生成钉钉加签参数"""
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign


def send_dingtalk(message: str, config: dict) -> bool:
    """通过钉钉机器人 Webhook 发送消息"""
    if not config.get("enabled"):
        return False

    webhook_url = config["webhook_url"]
    secret = config.get("secret", "")

    if secret:
        timestamp, sign = _dingtalk_sign(secret)
        webhook_url += f"&timestamp={timestamp}&sign={sign}"

    headers = {"Content-Type": "application/json"}
    payload = {
        "msgtype": "text",
        "text": {"content": message}
    }

    try:
        resp = requests.post(webhook_url, headers=headers,
                             data=json.dumps(payload), timeout=10)
        result = resp.json()
        if result.get("errcode") == 0:
            logger.info("钉钉消息发送成功")
            return True
        else:
            logger.error(f"钉钉发送失败: {result}")
            return False
    except Exception as e:
        logger.error(f"钉钉发送异常: {e}")
        return False


# ── 邮件 ──────────────────────────────────────────────────────

def send_email(subject: str, body: str, config: dict) -> bool:
    """通过 SMTP 发送邮件"""
    if not config.get("enabled"):
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = config["username"]
        msg["To"] = config["to_address"]
        msg.attach(MIMEText(body, "plain", "utf-8"))

        html_body = body.replace("\n", "<br>")
        msg.attach(MIMEText(f"<html><body>{html_body}</body></html>",
                            "html", "utf-8"))

        with smtplib.SMTP(config["smtp_server"], config["smtp_port"]) as server:
            server.starttls()
            server.login(config["username"], config["password"])
            server.send_message(msg)

        logger.info("邮件发送成功")
        return True
    except Exception as e:
        logger.error(f"邮件发送异常: {e}")
        return False


# ── 统一发送入口 ──────────────────────────────────────────────

def send_notification(message: str, config: dict,
                      subject: str = "A股行情提醒") -> dict:
    """向所有启用的通知渠道发送消息"""
    results = {}
    notif_config = config.get("notification", {})

    tg_cfg = notif_config.get("telegram", {})
    if tg_cfg.get("enabled"):
        results["telegram"] = send_telegram(message, tg_cfg)

    dd_cfg = notif_config.get("dingtalk", {})
    if dd_cfg.get("enabled"):
        results["dingtalk"] = send_dingtalk(message, dd_cfg)

    email_cfg = notif_config.get("email", {})
    if email_cfg.get("enabled"):
        results["email"] = send_email(subject, message, email_cfg)

    if not results:
        logger.warning("未启用任何通知渠道，提醒仅输出到日志和终端")

    return results


# ── 格式化告警消息 ────────────────────────────────────────────

def format_alerts(alerts: list) -> str:
    """将告警列表格式化为用户友好的通知消息"""
    if not alerts:
        return ""

    # 按紧急程度排序：CRITICAL > WARNING > INFO
    priority = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    alerts_sorted = sorted(alerts, key=lambda a: priority.get(a.level, 3))

    lines = ["📊 A股行情提醒", "━" * 20, ""]

    # 先展示紧急的（止损/止盈）
    urgent = [a for a in alerts_sorted if a.alert_type in ("stop_loss", "take_profit")]
    signals = [a for a in alerts_sorted if a.alert_type in ("buy_signal", "sell_signal")]

    if urgent:
        for alert in urgent:
            lines.append(alert.message)
            lines.append("")

    if signals:
        for alert in signals:
            lines.append(alert.message)
            lines.append("")

    lines.append(f"⏰ {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return "\n".join(lines)


# ── 测试入口 ──────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    config_path = "config.yaml"
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    test_message = (
        "📊 A股行情提醒 - 测试消息\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "这是一条测试通知！\n"
        "如果你收到了，说明通知渠道配置正确 ✅\n\n"
        f"⏰ {time.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    results = send_notification(test_message, config, subject="A股监控 - 测试")
    print(f"发送结果: {results}")
    if not results:
        print("⚠️  未启用任何通知渠道，请在 config.yaml 中配置")
