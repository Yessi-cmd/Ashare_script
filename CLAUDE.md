# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**A股个人研究与监控平台** monitors A-share prices, scores technical signals, manages a single owner's portfolio through a Telegram Bot, sends notifications, stores locally synchronized daily bars for reproducible research, and serves a read-only personal Web dashboard.

All user-facing dashboard, alert, and news text is Chinese. Normalize stock codes to six digits (`str(code).zfill(6)`).

## Development Commands

```bash
# Create/activate the local environment and install runtime dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Install development tooling
pip install -r requirements-dev.txt

# Quality gates (the same checks run in GitHub Actions)
ruff check .
python -m unittest discover -s tests -v
python -m compileall -q .

# Run one test module or one test
python -m unittest tests.test_market_data -v
python -m unittest \
  tests.test_market_data.MarketDataTests.test_upsert_is_idempotent_and_updates_values -v
```

CI runs these checks on Python 3.9 and 3.11 in `.github/workflows/ci.yml`.

### Local Services

```bash
# Copy and configure local settings before running services
cp config.yaml.example config.yaml

# Monitor: continuous, one-shot, or no-notification test mode
python monitor.py
python monitor.py --once
python monitor.py --test

# Local process launcher
python main.py
python main.py --monitor
python main.py --bot
python main.py --test

# Telegram Bot; requires app.owner_user_id and Telegram credentials
python bot.py

# Read-only Web dashboard; requires all three environment variables
export ASHARE_WEB_USERNAME='...'
export ASHARE_WEB_PASSWORD='...'
export ASHARE_WEB_SESSION_SECRET='...'
python web_app.py
```

`main.py` is convenient for local development. In production, use the separate systemd services for A-share monitor, cross-market monitor, Bot, Web, daily-bar synchronization, and backups; do not use `main.py` as the service supervisor. See `deploy/README_deploy.md`.

### Market-data Synchronization and Research

```bash
# Refresh the owner/research universe plus raw CSI 300 market proxy
python sync_universe.py --source auto

# Sync one local daily-bar series
python research.py sync 600519 --start 2024-01-01 --end 2026-07-17

# Backtest, cross-stock comparison, and train/validation evaluation
python research.py backtest 600519 --strategy v2 --start 2024-01-01
python research.py compare 000001 600519 300750 --horizon 20
python research.py walk-forward 000001 600519 300750 \
  --train-start 2024-01-01 --train-end 2025-06-30 \
  --validation-start 2025-07-01 --validation-end 2026-07-17 \
  --strategy v3 --thresholds 60,65,70,75,80

# Create an online SQLite backup (defaults are suitable for deployment)
python backup_database.py
```

## Configuration and Ownership Model

All application entry points load settings through `settings.load_config()`, which validates the YAML schema and critical values. When adding a configuration field, update:

- `config.yaml.example`;
- `settings.validate_config()`;
- relevant tests; and
- deployment documentation when the field affects deployed services.

There are two mutually exclusive personal-stock sources:

- With `app.owner_user_id: null`, monitor and Web use YAML `portfolio` and `watchlist`.
- With a positive `app.owner_user_id`, monitor and Web use only that owner's SQLite portfolio/watchlist; the YAML stock pool is ignored. The Bot starts only in this mode and authorizes only that owner.

Do not reintroduce multi-user monitoring or a permissive Bot access model without an explicit product and security decision.

## Architecture

The repository has three coordinated data flows backed by one SQLite database.

### 1. Monitoring and notifications

```text
config.yaml / configured owner's SQLite holdings
  -> monitor.py
  -> strategies.py realtime quotes + V2 scoring + portfolio checks
  -> notifier.py
  -> notification channels
  -> QuoteSnapshot and AlertState in SQLite
```

`monitor.py` owns the polling loop, Shanghai trading-time checks, runtime stock-universe selection, alert delivery, and terminal output. It writes the latest quotes/scores via `snapshot_store.py` for the Web dashboard.

`strategies.py` contains the live V2 scoring and alert engine. It fetches real-time data, maintains a short-lived historical-data cache, evaluates portfolio stop-loss/take-profit rules, and supports optional full-market prefiltering. The standard V2 score starts at 50 and combines RSI, MACD, moving averages, volume, and daily change.

Alert cooldowns are 300 seconds and persisted in `AlertState`, keyed by owner, stock code, and alert type. On storage failure the monitor falls back to an in-memory cache. Do not assume a restart clears cooldown state.

`holidays.py` is a closed trading-calendar implementation. Add and test calendar entries before a new supported year; unsupported years fail closed as non-trading time.

### 2. Local data and strategy research

```text
AKShare primary/fallback sources
  -> market_data.py normalization and validation
  -> DailyBar in SQLite
  -> research.py / backtest_engine.py / strategy_comparison.py / walk_forward.py
```

`market_data.py` is the normalized local daily-bar repository. It validates OHLCV invariants, normalizes adjustment modes, uses source fallback/timeouts, and upserts data without discarding existing cache on fetch failure.

`research.py` is the CLI entry point for synchronization, backtests, strategy comparisons, and walk-forward evaluation. Backtests generate a signal after a close and enter at the next trading day's open; commission, stamp duty, and slippage are included.

`strategy_v3.py` is a research candidate, not the live signal strategy. V3 may be used for research, comparison, and out-of-sample evaluation, but must not replace live V2 behavior in `strategies.py` without explicit approval and new auditable evidence.

### 3. Read-only Web dashboard

```text
QuoteSnapshot + DailyBar + personal holdings
  -> dashboard_data.py
  -> web_app.py
  -> Jinja templates and static assets
```

`web_app.py` is a FastAPI application with Basic Auth and signed session cookies. Its credentials come from `ASHARE_WEB_USERNAME`, `ASHARE_WEB_PASSWORD`, and `ASHARE_WEB_SESSION_SECRET`; Web authentication fails closed when they are missing.

`dashboard_data.py` must remain read-only with respect to external market services: it reads only local SQLite snapshots, daily bars, and configured owner data. Do not add AKShare, RSS, or other external network calls to Web request paths. `sync_universe.py` maintains the data the dashboard/research views require.

`market_monitor.py` owns cross-market benchmark collection independently of A-share trading hours. It writes `MarketQuoteSnapshot` rows through `global_market_data.py`; the `/markets` page only reads those local rows.

## Shared Storage

`database.py` owns the SQLAlchemy engine and models. The default database is repository-local `ashare_monitor.db`; `ASHARE_DATABASE_URL` can override it. SQLite is configured for WAL mode, foreign-key enforcement, and a busy timeout because the monitor, Bot, sync job, Web service, and backup utility may access it concurrently.

The main storage responsibilities are:

| Model | Responsibility |
|---|---|
| `User`, `Portfolio`, `Watchlist` | Configured owner's Bot-managed holdings and watchlist |
| `AlertState` | Persistent per-alert cooldown state |
| `DailyBar` | Normalized local daily OHLCV data for research and Web analytics |
| `QuoteSnapshot` | Most recent monitor quote, score, reason, and timestamp for Web display |
| `MarketQuoteSnapshot` | Most recent foreign-market benchmark quote and source timestamp |

Use the dedicated storage helpers (`user_config.py`, `alert_store.py`, `market_data.py`, and `snapshot_store.py`) rather than duplicating database access patterns in callers.

## Supporting Modules

- `bot.py`: owner-only Telegram command handlers for portfolio/watchlist management and news commands.
- `notifier.py`: dispatches formatted alerts through all enabled Telegram, DingTalk, and email channels.
- `news.py`: morning, evening, and instant financial news; external calls have thread-based timeouts and RSS fallback.
- `backup_database.py`: creates and verifies online SQLite backups, then prunes expired backups.
- `research_universe.py`: fixed local universe used for research candidates and the dashboard screener.

## Conventions and Constraints

- Use `logging.getLogger(__name__)` in modules and retain the existing Chinese user-facing wording.
- Existing entry points expose `if __name__ == "__main__":` behavior for direct use.
- Preserve the local-first data boundary: monitoring/synchronization fetch external data; research and Web consumers operate from persisted data.
- The deployed Web service must remain bound to loopback (`127.0.0.1:8000`); use the documented reverse-proxy/private-network deployment paths rather than exposing port 8000 publicly.
