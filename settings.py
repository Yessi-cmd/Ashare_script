"""Application configuration loading and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml


class ConfigError(ValueError):
    """Raised when application configuration is invalid."""


def _as_int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} 必须是整数") from exc


def get_owner_user_id(config: dict) -> Optional[int]:
    """Return the single Telegram owner id, if database-backed mode is enabled."""
    value = config.get("app", {}).get("owner_user_id")
    if value in (None, ""):
        return None
    owner_user_id = _as_int(value, "app.owner_user_id")
    if owner_user_id <= 0:
        raise ConfigError("app.owner_user_id 必须是正整数")
    return owner_user_id


def validate_config(config: Any) -> dict:
    """Validate important settings and return the normalized mapping."""
    if not isinstance(config, dict):
        raise ConfigError("配置文件顶层必须是 YAML 对象")

    interval = config.get("monitor", {}).get("interval_seconds", 30)
    try:
        interval = float(interval)
    except (TypeError, ValueError) as exc:
        raise ConfigError("monitor.interval_seconds 必须是数字") from exc
    if interval <= 0:
        raise ConfigError("monitor.interval_seconds 必须大于 0")

    signal = config.get("signal", {})
    buy_threshold = _as_int(signal.get("buy_threshold", 70), "signal.buy_threshold")
    sell_threshold = _as_int(signal.get("sell_threshold", 30), "signal.sell_threshold")
    if not 0 <= sell_threshold < buy_threshold <= 100:
        raise ConfigError("信号阈值必须满足 0 <= sell_threshold < buy_threshold <= 100")

    get_owner_user_id(config)

    global_markets = config.get("global_markets", {}) or {}
    if not isinstance(global_markets, dict):
        raise ConfigError("global_markets 必须是 YAML 对象")
    if "enabled" in global_markets and not isinstance(global_markets["enabled"], bool):
        raise ConfigError("global_markets.enabled 必须是布尔值")
    try:
        market_interval = float(
            global_markets.get("poll_interval_seconds", 300)
        )
        market_timeout = float(
            global_markets.get("request_timeout_seconds", 10)
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError("global_markets 的轮询间隔和请求超时必须是数字") from exc
    if market_interval <= 0 or market_timeout <= 0:
        raise ConfigError("global_markets 的轮询间隔和请求超时必须大于 0")

    configured_markets = global_markets.get("markets", {}) or {}
    if not isinstance(configured_markets, dict):
        raise ConfigError("global_markets.markets 必须是 YAML 对象")
    supported_markets = {"a_share", "hk", "kr", "us", "jp"}
    for market_key, market_config in configured_markets.items():
        if market_key not in supported_markets:
            raise ConfigError(f"global_markets.markets 不支持市场: {market_key}")
        if not isinstance(market_config, dict):
            raise ConfigError(f"global_markets.markets.{market_key} 必须是 YAML 对象")
        indices = market_config.get("indices")
        if indices is None:
            continue
        if not isinstance(indices, list) or not indices:
            raise ConfigError(
                f"global_markets.markets.{market_key}.indices 必须是非空列表"
            )
        symbols = set()
        for index in indices:
            if not isinstance(index, dict) or not str(index.get("symbol", "")).strip():
                raise ConfigError(
                    f"global_markets.markets.{market_key}.indices 含无效指数配置"
                )
            symbol = str(index["symbol"]).strip()
            if symbol in symbols:
                raise ConfigError(
                    f"global_markets.markets.{market_key}.indices 不能重复: {symbol}"
                )
            symbols.add(symbol)

    portfolio = config.get("portfolio", {}) or {}
    watchlist = config.get("watchlist", {}) or {}
    if not isinstance(portfolio, dict) or not isinstance(watchlist, dict):
        raise ConfigError("portfolio 和 watchlist 必须是 YAML 对象")
    for raw_code, holding in portfolio.items():
        code = str(raw_code)
        if not code.isdigit() or len(code) != 6 or not isinstance(holding, dict):
            raise ConfigError(f"无效持仓配置: {raw_code}")
        try:
            buy_price = float(holding.get("buy_price", 0))
            shares = int(holding.get("shares", 0))
            stop_loss = float(holding.get("stop_loss", -5))
            take_profit = float(holding.get("take_profit", 10))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"持仓 {code} 的价格、股数或阈值格式错误") from exc
        if buy_price <= 0 or shares <= 0 or stop_loss >= 0 or take_profit <= 0:
            raise ConfigError(f"持仓 {code} 必须满足价格/股数为正、止损为负、止盈为正")
    for raw_code in watchlist:
        code = str(raw_code)
        if not code.isdigit() or len(code) != 6:
            raise ConfigError(f"无效关注股票代码: {raw_code}")

    full_market = config.get("full_market", {})
    if full_market.get("enabled"):
        prefilter = full_market.get("prefilter", {})
        try:
            max_results = int(prefilter.get("max_results", 100))
        except (TypeError, ValueError) as exc:
            raise ConfigError("full_market.prefilter.max_results 必须是整数") from exc
        if not 1 <= max_results <= 500:
            raise ConfigError("full_market.prefilter.max_results 必须在 1-500 之间")

    notification = config.get("notification", {})
    required = {
        "telegram": ("bot_token", "chat_id"),
        "dingtalk": ("webhook_url",),
        "email": ("smtp_server", "smtp_port", "username", "password", "to_address"),
    }
    for channel, fields in required.items():
        channel_config = notification.get(channel, {})
        if channel_config.get("enabled"):
            missing = [field for field in fields if not channel_config.get(field)]
            if missing:
                raise ConfigError(f"notification.{channel} 缺少配置: {', '.join(missing)}")

    return config


def load_config(path: str | Path = "config.yaml") -> dict:
    """Load a UTF-8 YAML configuration file and validate it."""
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"配置文件不存在: {config_path}")
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 YAML 格式错误: {exc}") from exc
    return validate_config(config)
