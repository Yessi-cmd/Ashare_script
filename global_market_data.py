"""Cross-market benchmark definitions, quote parsing, and local persistence.

The Web dashboard must remain local-first.  Network access therefore lives in
this module and is used only by ``market_monitor.py``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import logging
import math
from typing import Callable, Iterable, Optional
from urllib.parse import quote

import requests
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database import MarketQuoteSnapshot, get_db, init_db

logger = logging.getLogger(__name__)

YAHOO_CHART_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
YAHOO_SOURCE = "yahoo_chart"
DEFAULT_POLL_INTERVAL_SECONDS = 300.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class MarketIndexDefinition:
    """Display and source metadata for one benchmark index."""

    market: str
    market_name: str
    symbol: str
    name: str
    currency: str
    timezone: str
    local_code: Optional[str] = None


@dataclass(frozen=True)
class MarketQuote:
    """One normalized quote ready to be persisted."""

    market: str
    symbol: str
    name: str
    price: float
    change_pct: float
    currency: str
    quote_at: datetime
    market_at: Optional[datetime]
    source: str = YAHOO_SOURCE


DEFAULT_MARKET_DEFINITIONS = (
    MarketIndexDefinition(
        market="hk",
        market_name="港股",
        symbol="^HSI",
        name="恒生指数",
        currency="HKD",
        timezone="Asia/Hong_Kong",
    ),
    MarketIndexDefinition(
        market="kr",
        market_name="韩国",
        symbol="^KS11",
        name="KOSPI",
        currency="KRW",
        timezone="Asia/Seoul",
    ),
    MarketIndexDefinition(
        market="us",
        market_name="美股",
        symbol="^GSPC",
        name="标普 500",
        currency="USD",
        timezone="America/New_York",
    ),
    MarketIndexDefinition(
        market="us",
        market_name="美股",
        symbol="^IXIC",
        name="纳斯达克综合",
        currency="USD",
        timezone="America/New_York",
    ),
    MarketIndexDefinition(
        market="us",
        market_name="美股",
        symbol="^DJI",
        name="道琼斯工业平均",
        currency="USD",
        timezone="America/New_York",
    ),
    MarketIndexDefinition(
        market="jp",
        market_name="日本",
        symbol="^N225",
        name="日经 225",
        currency="JPY",
        timezone="Asia/Tokyo",
    ),
)

# A-share benchmarks are kept separate from the global-market page, but use
# the same collector and local snapshot table.  ``local_code`` aligns the
# Yahoo symbol with raw index bars stored by ``sync_universe.py``.
DEFAULT_A_SHARE_DEFINITIONS = (
    MarketIndexDefinition(
        market="a_share",
        market_name="A股",
        symbol="000001.SS",
        name="上证指数",
        currency="CNY",
        timezone="Asia/Shanghai",
        local_code="000001",
    ),
    MarketIndexDefinition(
        market="a_share",
        market_name="A股",
        symbol="399001.SZ",
        name="深证成指",
        currency="CNY",
        timezone="Asia/Shanghai",
        local_code="399001",
    ),
    MarketIndexDefinition(
        market="a_share",
        market_name="A股",
        symbol="399006.SZ",
        name="创业板指",
        currency="CNY",
        timezone="Asia/Shanghai",
        local_code="399006",
    ),
    MarketIndexDefinition(
        market="a_share",
        market_name="A股",
        symbol="000300.SS",
        name="沪深 300",
        currency="CNY",
        timezone="Asia/Shanghai",
        local_code="000300",
    ),
)


def global_markets_enabled(config: dict | None) -> bool:
    """Return whether the cross-market collector and page are enabled.

    Missing configuration intentionally means enabled so an existing install
    can use the new page immediately after upgrading.  Set
    ``global_markets.enabled: false`` to disable collection.
    """

    section = (config or {}).get("global_markets", {})
    return bool(section.get("enabled", True)) if isinstance(section, dict) else True


def _market_overrides(config: dict | None) -> dict:
    section = (config or {}).get("global_markets", {})
    if not isinstance(section, dict):
        return {}
    overrides = section.get("markets", {})
    return overrides if isinstance(overrides, dict) else {}


def market_definitions(
    config: dict | None = None,
    *,
    include_a_share: bool = False,
) -> tuple[MarketIndexDefinition, ...]:
    """Resolve benchmark definitions with optional YAML overrides.

    The public global-market page intentionally remains focused on overseas
    benchmarks.  The independent collector passes ``include_a_share=True``
    so the home page can show A-share context through the same local-first
    persistence path.
    """

    if not global_markets_enabled(config):
        return ()

    overrides = _market_overrides(config)
    defaults_to_resolve = DEFAULT_MARKET_DEFINITIONS + (
        DEFAULT_A_SHARE_DEFINITIONS if include_a_share else ()
    )
    grouped_defaults: dict[str, list[MarketIndexDefinition]] = {}
    for default in defaults_to_resolve:
        grouped_defaults.setdefault(default.market, []).append(default)

    resolved: list[MarketIndexDefinition] = []
    for market, defaults in grouped_defaults.items():
        default = defaults[0]
        market_config = overrides.get(market, {})
        if not isinstance(market_config, dict):
            market_config = {}
        if market_config.get("enabled", True) is False:
            continue

        index_specs = market_config.get("indices")
        if index_specs is None:
            resolved.extend(defaults)
            continue

        for spec in index_specs:
            if not isinstance(spec, dict):
                continue
            symbol = str(spec.get("symbol", default.symbol)).strip()
            if not symbol:
                continue
            resolved.append(
                MarketIndexDefinition(
                    market=market,
                    market_name=str(
                        market_config.get("name", default.market_name)
                    ),
                    symbol=symbol,
                    name=str(spec.get("name", symbol)),
                    currency=str(
                        spec.get("currency", market_config.get("currency", default.currency))
                    ),
                    timezone=str(
                        spec.get("timezone", market_config.get("timezone", default.timezone))
                    ),
                    local_code=(
                        str(spec["local_code"]).zfill(6)
                        if spec.get("local_code")
                        else default.local_code
                    ),
                )
            )
    return tuple(resolved)


def market_poll_interval(config: dict | None = None) -> float:
    section = (config or {}).get("global_markets", {})
    if isinstance(section, dict):
        try:
            return max(1.0, float(section.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS)))
        except (TypeError, ValueError):
            pass
    return DEFAULT_POLL_INTERVAL_SECONDS


def market_request_timeout(config: dict | None = None) -> float:
    section = (config or {}).get("global_markets", {})
    if isinstance(section, dict):
        try:
            return max(1.0, float(section.get("request_timeout_seconds", DEFAULT_REQUEST_TIMEOUT_SECONDS)))
        except (TypeError, ValueError):
            pass
    return DEFAULT_REQUEST_TIMEOUT_SECONDS


def _number(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _utc_datetime(timestamp) -> Optional[datetime]:
    """Convert an epoch timestamp to naive UTC for unambiguous storage."""
    if timestamp in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(
            float(timestamp), tz=timezone.utc
        ).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


def parse_yahoo_chart_payload(
    payload: dict,
    definition: MarketIndexDefinition,
    quote_at: Optional[datetime] = None,
) -> MarketQuote:
    """Validate and normalize one Yahoo Chart API response."""

    chart = payload.get("chart") if isinstance(payload, dict) else None
    if not isinstance(chart, dict):
        raise ValueError("响应缺少 chart 节点")
    if chart.get("error"):
        error = chart["error"]
        message = error.get("description") if isinstance(error, dict) else error
        raise ValueError(f"Yahoo 返回错误: {message}")
    results = chart.get("result") or []
    if not results or not isinstance(results[0], dict):
        raise ValueError("响应没有行情结果")

    result = results[0]
    meta = result.get("meta") or {}
    price = _number(meta.get("regularMarketPrice"))
    market_timestamp = meta.get("regularMarketTime")

    if price is None or price <= 0:
        timestamps = result.get("timestamp") or []
        quote_rows = ((result.get("indicators") or {}).get("quote") or [])
        closes = quote_rows[0].get("close", []) if quote_rows else []
        for index in range(len(closes) - 1, -1, -1):
            candidate = _number(closes[index])
            if candidate is not None:
                price = candidate
                if index < len(timestamps):
                    market_timestamp = timestamps[index]
                break
    if price is None or price <= 0:
        raise ValueError("响应缺少有效最新价")

    change_pct = _number(meta.get("regularMarketChangePercent"))
    previous_close = _number(meta.get("previousClose"))
    if previous_close is None or previous_close <= 0:
        previous_close = _number(meta.get("chartPreviousClose"))
    if change_pct is None:
        if previous_close is None or previous_close <= 0:
            raise ValueError("响应缺少有效前收价")
        change_pct = (price / previous_close - 1) * 100

    return MarketQuote(
        market=definition.market,
        symbol=definition.symbol,
        name=definition.name,
        price=price,
        change_pct=change_pct,
        currency=definition.currency,
        quote_at=quote_at or datetime.now(timezone.utc).replace(tzinfo=None),
        market_at=_utc_datetime(market_timestamp),
    )


def fetch_market_quotes(
    definitions: Iterable[MarketIndexDefinition],
    timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    http_get: Optional[Callable] = None,
) -> tuple[list[MarketQuote], list[str]]:
    """Fetch each index independently in parallel; return quotes/errors."""

    request = http_get or requests.get
    quotes: list[MarketQuote] = []
    errors: list[str] = []
    headers = {"User-Agent": "AshareMonitor/2.0 (+local dashboard)"}

    def fetch_one(definition: MarketIndexDefinition) -> MarketQuote:
        url = f"{YAHOO_CHART_BASE_URL}/{quote(definition.symbol, safe='')}"
        response = request(
            url,
            params={
                "range": "1d",
                "interval": "1m",
                "includePrePost": "true",
            },
            headers=headers,
            timeout=timeout,
        )
        raise_for_status = getattr(response, "raise_for_status", None)
        if raise_for_status is not None:
            raise_for_status()
        return parse_yahoo_chart_payload(response.json(), definition)

    definitions = list(definitions)
    with ThreadPoolExecutor(
        max_workers=max(1, min(len(definitions), 8))
    ) as executor:
        futures = [executor.submit(fetch_one, definition) for definition in definitions]
        for definition, future in zip(definitions, futures):
            try:
                quotes.append(future.result())
            except Exception as exc:
                errors.append(
                    f"{definition.market_name} {definition.name}"
                    f"({definition.symbol}): {exc}"
                )
    return quotes, errors


def save_market_snapshots(quotes: Iterable[MarketQuote]) -> int:
    """Atomically upsert successful cross-market quotes without deleting old data."""

    records = []
    for quote_row in quotes:
        record = asdict(quote_row) if isinstance(quote_row, MarketQuote) else dict(quote_row)
        if not record.get("market") or not record.get("symbol"):
            continue
        records.append(record)
    if not records:
        return 0

    init_db()
    db = get_db()
    try:
        statement = sqlite_insert(MarketQuoteSnapshot).values(records)
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=["market", "symbol"],
            set_={
                "name": excluded.name,
                "price": excluded.price,
                "change_pct": excluded.change_pct,
                "currency": excluded.currency,
                "quote_at": excluded.quote_at,
                "market_at": excluded.market_at,
                "source": excluded.source,
            },
        )
        db.execute(statement)
        db.commit()
        return len(records)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
