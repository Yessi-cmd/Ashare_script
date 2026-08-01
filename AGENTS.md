# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

**A股行情监控 V2** — A Chinese A-share stock market monitoring system with read-only benchmark panels for Hong Kong, Korea, the United States, and Japan. It polls A-share real-time quotes via [AKShare](https://github.com/akfamilygroup/akshare), computes a 0–100 technical scoring (RSI, MACD, MA, volume) for buy/sell signals, monitors portfolio stop-loss/take-profit thresholds, and pushes alerts via Telegram/DingTalk/Email.

## Running the Project

```bash
# Activate venv first
source venv/bin/activate

# Continuous monitoring loop
python monitor.py

# Single run (check once, notify, then exit)
python monitor.py --once

# Test mode (fetch data, print to stdout, no notifications)
python monitor.py --test

# Telegram Bot (interactive portfolio management)
python bot.py

# Cross-market benchmark collector (HK/KR/US/JP)
python market_monitor.py
python market_monitor.py --once
python market_monitor.py --test

# One-click launcher for all monitors and Bot
python main.py              # start both monitor + bot
python main.py --monitor    # only monitor
python main.py --markets    # only cross-market collector
python main.py --bot        # only bot
python main.py --test       # test mode (monitor once, no notifications)

# Authenticated local Web and paper trading (credentials must be in environment)
ASHARE_WEB_USERNAME=owner ASHARE_WEB_PASSWORD=long-random-password python web_app.py

# Local research and server maintenance
python research.py --help
python sync_universe.py --help
python backup_database.py --help
```

### Setup

1. `cp config.yaml.example config.yaml`
2. Edit `config.yaml` with your notification credentials (Telegram bot token, chat ID, etc.)
3. Ensure `ashare_monitor.db` is created (auto on first run via `database.py`)

## Architecture

The monitor, cross-market collector, Bot, and authenticated Web run independently and share the same database and config.

### Module Dependency Graph

```
                    ┌──────────────┐
                    │   main.py    │  (optional orchestrator, launches subprocesses)
                    └──┬───────┬───┘
                       │       │
          ┌────────────┘       └────────────┐
          ▼                                  ▼
  ┌───────────────┐                 ┌───────────────┐
  │  monitor.py   │                 │    bot.py     │
  │  (main loop)  │                 │ (Telegram Bot)│
  └───┬───┬───┬───┘                 └───┬───┬───┬───┘
      │   │   │                         │   │   │
      ▼   ▼   ▼                         ▼   ▼   ▼
  ┌──────┐ ┌──────┐ ┌──────┐     ┌──────┐ ┌──────┐ ┌──────┐
  │strat │ │notif │ │hols  │     │news  │ │uconf │ │db    │
  └──────┘ └──────┘ └──────┘     └──────┘ └──────┘ └──────┘
```

### Key Modules

| Module | Role | Key Exports |
|--------|------|-------------|
| `monitor.py` | Main monitoring loop. Checks trading hours, fetches quotes, runs checks, executes queued paper orders, prints terminal dashboard, sends alerts. | `monitor_loop()`, `print_dashboard()`, `is_trading_time()` |
| `strategies.py` | Scoring engine + stop-loss/take-profit checks. Uses 5-factor 0–100 score (RSI 25pt, MACD 20pt, MA 15pt, volume 10pt, daily change 5pt, baseline 50). | `Alert` dataclass, `calculate_score()`, `run_all_checks()`, `fetch_realtime_quotes()`, `check_portfolio()`, `prefilter_full_market()` |
| `notifier.py` | Multi-channel notification dispatch. Sends to all enabled channels. | `send_notification()`, `format_alerts()` |
| `bot.py` | Telegram Bot for interactive portfolio/watchlist management. Uses `python-telegram-bot`. | Command handlers: `/add`, `/remove`, `/list`, `/watch`, `/unwatch`, `/status`, `/news`, `/morning`, `/evening` |
| `news.py` | Financial news: morning briefing (overnight markets + flash news), evening summary (indices + money flow), instant news. Thread-based API timeout protection (5s) with RSS fallback. | `get_morning_news()`, `get_evening_news()`, `get_instant_news()` |
| `holidays.py` | Hardcoded 2026 China A-share holiday calendar. | `is_trading_day()`, `is_holiday()`, `get_next_trading_day()` |
| `database.py` | SQLAlchemy ORM (SQLite). Stores personal data, snapshots, alert state, and the paper-trading ledger. | `init_db()`, `get_db()`, `PaperAccount`, `PaperPosition`, `PaperOrder` |
| `user_config.py` | DB-backed user config layer. Exposes dict-based API (compatible with old YAML format) but persists to SQLite. | `load_user_config()`, `save_user_config()`, `get_all_users()`, `get_all_portfolios()`, `get_all_watchlists()` |
| `settings.py` | Validated YAML configuration loader and single-owner resolution. | `load_config()`, `validate_config()`, `get_owner_user_id()` |
| `alert_store.py` | Persists alert cooldown state so restarts do not repeat alerts immediately. | `load_alert_cache()`, `mark_alerted()` |
| `market_data.py` | Local normalized daily-bar repository for stocks and explicit raw index sources. | `sync_daily_bars()`, `load_daily_bars()` |
| `global_market_data.py` | Cross-market benchmark definitions, Yahoo quote parsing, and local snapshot persistence. | `market_definitions()`, `fetch_market_quotes()`, `save_market_snapshots()` |
| `market_monitor.py` | Independent cross-market collector for HK/KR/US/JP benchmark indexes. | `market_monitor_loop()`, `run_market_cycle()` |
| `backtest_engine.py` | No-lookahead, next-open research backtester with explicit costs and evaluation windows. | `BacktestConfig`, `run_backtest()` |
| `strategy_v3.py` | Explainable candidate factor strategy and date-aligned market-context scorer. Not live. | `evaluate_strategy_v3()`, `make_strategy_v3_scorer()` |
| `walk_forward.py` | Training-only threshold selection and strict out-of-sample validation. | `run_walk_forward()` |
| `snapshot_store.py` | Atomic latest-quote/score handoff from monitor to Web. | `save_quote_snapshots()` |
| `paper_trading.py` | Integer-fen paper ledger, order validation, A-share fees/T+1, monitor-driven execution, and dashboard valuation. | `submit_paper_order()`, `process_pending_paper_orders()`, `load_paper_dashboard()` |
| `web_app.py` | Authenticated FastAPI dashboard. Market/research paths stay local-only; `/paper` has CSRF-protected paper-order writes. | `app`, `require_auth()` |
| `sync_universe.py` | Timer-friendly local bar synchronization for the personal universe and CSI 300. | `universe_codes()`, `main()` |
| `backup_database.py` | Verified online SQLite backup with retention pruning. | `create_backup()` |
| `main.py` | Optional orchestration — spawns A-share monitor, cross-market monitor, and Bot as subprocesses with signal handling. | CLI flags: `--monitor`, `--markets`, `--bot`, `--test` |

### Data Flow: Monitoring Cycle

1. `monitor.py` checks `is_trading_time()` via `holidays.py` → if not trading day/hour, sleep 60s
2. Builds one personal monitoring snapshot: SQLite for `app.owner_user_id`, otherwise the YAML portfolio/watchlist
3. Adds pending/held paper symbols, then calls `strategies.fetch_realtime_quotes()` (or `prefilter_full_market()` in full-market mode)
4. Calls `strategies.run_all_checks()` → returns `list[Alert]` + `dict[code → (score, reason)]`
5. Saves quote/score snapshots and executes eligible pending paper orders; test mode and closed sessions never execute
6. Deduplicates alerts by `{stock_code}:{alert_type}` with a persistent 300s cooldown
7. Prints `print_dashboard()` and calls `notifier.send_notification()` for new alerts

The independent `market_monitor.py` process fetches configured HK/KR/US/JP
benchmark quotes and writes `MarketQuoteSnapshot` rows. The Web `/markets`
page only reads those local rows and never calls the external source.

### Data Flow: Bot Commands

1. `bot.py` receives Telegram command → extracts `user_id` from `update.effective_user.id`
2. Permission check against the single `app.owner_user_id`; missing owner configuration fails closed
3. Calls `user_config.load_user_config(user_id)` → gets portfolio/watchlist dict
4. Mutates dict, calls `user_config.save_user_config(user_id, config)` → persists to SQLite via `database.py`

### Database Schema (SQLite via SQLAlchemy)

| `users` | Type | Notes |
|---------|------|-------|
| `user_id` | Integer PK | Telegram user ID |
| `username` | String | Optional display name |
| `created_at` | DateTime | Auto |
| `updated_at` | DateTime | Auto |

User has one-to-many with `portfolios` and `watchlist` (cascade delete).

| `portfolios` | Type | Notes |
|--------------|------|-------|
| `id` | Integer PK auto | |
| `user_id` | Integer FK | Indexed |
| `stock_code` | String(10) | Indexed |
| `name` | String(50) | Display name |
| `buy_price` | Float | Entry price |
| `shares` | Integer | |
| `stop_loss` | Float | Default -5.0 |
| `take_profit` | Float | Default 10.0 |

| `watchlist` | Type | Notes |
|-------------|------|-------|
| `id` | Integer PK auto | |
| `user_id` | Integer FK | Indexed |
| `stock_code` | String(10) | Indexed |
| `name` | String(50) | |

| `market_quote_snapshots` | Type | Notes |
|---------|------|-------|
| `market` + `symbol` | String PK | Cross-market and benchmark symbol key |
| `name` | String(80) | Display name |
| `price` / `change_pct` | Float | Latest price and daily change |
| `currency` | String(10) | Display currency |
| `quote_at` / `market_at` | DateTime | Shanghai collection time and local market time |

### Two Modes

- **Watchlist mode** (default): Monitor specific stocks from config.yaml portfolio + watchlist sections
- **Full-market scan mode**: Set `full_market.enabled: true` in config.yaml. Two-stage pipeline: (1) prefilter ~5000 stocks by price change/volume/market cap, (2) detailed technical scoring on top candidates

### Alert Deduplication

`alert_store.py` loads and persists `AlertState` rows keyed by owner, stock code, and alert type. The in-process cache mirrors this state for the 300-second cooldown. Failed deliveries are not marked as delivered.

### Historical Data Caching

`strategies.py` caches per-stock K-line data (`_history_cache`) for 5 minutes. Keyed by stock code.

### News API Timeout Protection

`news.call_with_timeout(func, timeout=5)` runs AKShare API calls in a daemon thread. If the thread doesn't finish within 5 seconds, returns `None` and falls back to RSS feeds (新浪财经, 华尔街见闻, FT中文网) via `feedparser`.

### Trading Hours

Morning: 09:15–11:30, Afternoon: 13:00–15:00. Hardcoded 2026 holidays in `holidays.py`. Note: 调休补班 days (weekend makeup workdays) are NOT trading days for A-shares.

## Key Configuration (`config.yaml`)

```yaml
app: { owner_user_id: 123456789 }
portfolio: { "600519": { name, buy_price, shares, stop_loss, take_profit } }
watchlist: { "300750": "宁德时代" }
full_market: { enabled, prefilter: { min_price_change, min_volume_ratio, min_market_cap, max_results }, scoring: { min_score } }
global_markets: { enabled, poll_interval_seconds, request_timeout_seconds, markets: { hk, kr, us, jp } }
monitor: { interval_seconds: 30, trading_hours: { morning_start, morning_end, afternoon_start, afternoon_end } }
signal: { buy_threshold: 70, sell_threshold: 30 }
notification: { telegram: { enabled, bot_token, chat_id }, dingtalk: { enabled, webhook_url, secret }, email: { enabled, smtp_server, smtp_port, username, password, to_address } }
logging: { level: INFO, file: monitor.log }
```

## Deployment

- systemd service files in `deploy/` for running `monitor.py`, `market_monitor.py`, and `bot.py` on a server
- TZ=Asia/Shanghai required for correct trading-hour detection
- Python 3.9+ (venv uses Python 3.9)

## Conventions

- All user-facing strings (dashboard, alerts, news) are in **Chinese**
- Stock codes are zero-padded to 6 digits via `str(code).zfill(6)`
- Logging uses `logging.getLogger(__name__)` per module
- Each module has a `if __name__ == "__main__":` block for standalone testing
- Tests use `unittest`; run `python -m unittest discover -s tests -v`
- Ruff is the required static check; run `ruff check .`
- GitHub Actions runs checks on Python 3.9 and 3.11
- Error handling pattern: try/except with `logger.error(f"...: {e}")`, return None/False on failure
- File encoding everywhere: `utf-8`

## Documentation-first staged development

Large changes are implemented in numbered phases under `docs/roadmap/`.

Before changing code for a phase:

1. Create or update its document with status, goal, scope, non-goals, design decisions, data/API changes, risks, and acceptance criteria.
2. Link the phase from `docs/roadmap/README.md` and mark exactly one phase `IN PROGRESS`.
3. Keep implementation within the documented scope. Record material design changes in the phase document before or with the code change.

Before marking a phase complete:

1. Run the validation commands listed in that phase.
2. Record results, important measurements, migrations, and remaining limitations in its implementation log.
3. Update the roadmap status and user-facing documentation.

Do not mark a phase complete merely because code was written. Completion requires its acceptance criteria and validation evidence.
