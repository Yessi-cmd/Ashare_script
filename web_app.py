#!/usr/bin/env python3
"""Authenticated local dashboard for research and paper trading."""

from __future__ import annotations

import logging
import os
import secrets
import time
from math import isfinite
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, quote

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func

from dashboard_data import (
    load_overview,
    load_market_overview,
    load_recommendations,
    load_screener,
    load_stock_summary,
    load_system_status,
)
from backtest_bt import run_backtest_web
from database import Portfolio, QuoteSnapshot, User, Watchlist, get_db, init_db
from market_data import load_daily_bars, normalize_stock_code
from paper_trading import (
    PaperTradingError,
    cancel_paper_order,
    load_paper_dashboard,
    submit_paper_order,
)
from settings import ConfigError, load_config
from settings import get_owner_user_id
from web_auth import (
    WebAuthError,
    WebPrincipal,
    authenticate_web_user,
    load_web_principal,
    make_signed_token,
    read_signed_token,
    register_web_user,
    registration_code_configured,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
security = HTTPBasic(auto_error=False)
app = FastAPI(title="个人研究台", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

SESSION_COOKIE_NAME = "ashare_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
REMEMBER_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
CSRF_TTL_SECONDS = 12 * 60 * 60

PAPER_RESULT_MESSAGES = {
    "order-created": ("ok", "委托已提交，等待交易时段的下一轮实时行情成交。"),
    "order-cancelled": ("ok", "待成交委托已撤销。"),
    "invalid-side": ("error", "委托方向无效。"),
    "invalid-code": ("error", "股票代码必须是 1-6 位数字。"),
    "invalid-quantity": ("error", "数量必须是 100 股的正整数倍。"),
    "invalid-order-id": ("error", "委托标识失效，请刷新页面后重试。"),
    "duplicate-order-conflict": ("error", "委托标识冲突，请刷新页面后重试。"),
    "no-position": ("error", "没有可卖出的模拟持仓。"),
    "insufficient-shares": ("error", "可卖数量不足，可能含当日买入或待卖股份。"),
    "order-not-found": ("error", "委托不存在。"),
    "order-not-pending": ("error", "只有待成交委托可以撤销。"),
    "request-failed": ("error", "模拟盘操作失败，请稍后重试。"),
}

ACCOUNT_RESULT_MESSAGES = {
    "portfolio-saved": ("ok", "持仓已保存。"),
    "portfolio-deleted": ("ok", "持仓已删除。"),
    "watchlist-saved": ("ok", "关注股票已保存。"),
    "watchlist-deleted": ("ok", "关注股票已删除。"),
    "invalid-data": ("error", "股票代码、价格、股数或风险参数无效。"),
    "legacy-yaml": ("error", "当前遗留 YAML 账号不支持网页编辑，请先切换到数据库用户。"),
    "request-failed": ("error", "个人数据操作失败，请稍后重试。"),
}


def _auth_settings() -> str:
    session_secret = os.getenv("ASHARE_WEB_SESSION_SECRET", "")
    if not session_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Web 认证尚未完整配置",
        )
    return session_secret


def _make_session_token(
    username: str,
    session_secret: str,
    expires_at: int,
    user_id: int = 0,
) -> str:
    """Compatibility wrapper retained for the existing offline tests."""
    return make_signed_token(
        user_id, username, session_secret, expires_at, purpose="session"
    )


def _read_session_token(
    token: str,
    username: str,
    session_secret: str,
    user_id: int | None = None,
) -> bool:
    payload = read_signed_token(token, session_secret, purpose="session")
    return bool(
        payload
        and payload.get("username") == username
        and (user_id is None or payload.get("user_id") == user_id)
    )


def _make_csrf_token(
    username: str,
    session_secret: str,
    expires_at: int,
    user_id: int = 0,
) -> str:
    return make_signed_token(
        user_id, username, session_secret, expires_at, purpose="csrf"
    )


def _valid_csrf_token(
    token: str,
    username: str,
    session_secret: str,
    user_id: int,
) -> bool:
    payload = read_signed_token(token, session_secret, purpose="csrf")
    return bool(
        payload
        and payload.get("username") == username
        and payload.get("user_id") == user_id
    )


def _require_csrf(token: str, principal: WebPrincipal) -> None:
    session_secret = _auth_settings()
    if not _valid_csrf_token(
        token, principal.username, session_secret, principal.user_id
    ):
        raise HTTPException(status_code=403, detail="表单校验失败")


def _valid_basic_auth(
    credentials: HTTPBasicCredentials | None,
    username: str,
    password: str,
) -> bool:
    return credentials is not None and secrets.compare_digest(
        credentials.username.encode("utf-8"), username.encode("utf-8")
    ) and secrets.compare_digest(
        credentials.password.encode("utf-8"), password.encode("utf-8")
    )


def _safe_next_path(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def require_auth(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(security),
) -> WebPrincipal:
    session_secret = _auth_settings()
    session_token = request.cookies.get(SESSION_COOKIE_NAME, "")
    payload = read_signed_token(session_token, session_secret, purpose="session")
    if payload:
        principal = load_web_principal(
            payload["user_id"], payload["username"]
        )
        if principal is not None:
            request.state.web_user = principal
            return principal
    if credentials is not None:
        principal = authenticate_web_user(credentials.username, credentials.password)
        if principal is not None:
            request.state.web_user = principal
            return principal

    if request.url.path.startswith("/api/"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="认证失败",
            headers={"WWW-Authenticate": "Basic"},
        )
    next_path = request.url.path
    if request.url.query:
        next_path += f"?{request.url.query}"
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": f"/login?next={quote(next_path, safe='')}"},
    )


def _personal_user_id(config: dict, principal: WebPrincipal) -> int | None:
    """Resolve the data owner while keeping the old YAML mode intact."""
    if not principal.legacy_env:
        return principal.user_id
    return get_owner_user_id(config)


def _paper_user_id(config: dict, principal: WebPrincipal) -> int:
    """Resolve the paper ledger owner for both new and legacy accounts."""
    if not principal.legacy_env:
        return principal.user_id
    return get_owner_user_id(config) or 0


def _account_user_id(config: dict, principal: WebPrincipal) -> int:
    user_id = _personal_user_id(config, principal)
    if user_id is None:
        raise HTTPException(status_code=400, detail="遗留 YAML 账号不支持网页编辑")
    return user_id


def _load_account_data(user_id: int) -> dict:
    init_db()
    db = get_db()
    try:
        portfolio = [
            {
                "code": row.stock_code,
                "name": row.name,
                "buy_price": row.buy_price,
                "shares": row.shares,
                "stop_loss": row.stop_loss,
                "take_profit": row.take_profit,
            }
            for row in db.query(Portfolio).filter(
                Portfolio.user_id == user_id
            ).order_by(Portfolio.stock_code).all()
        ]
        watchlist = [
            {"code": row.stock_code, "name": row.name}
            for row in db.query(Watchlist).filter(
                Watchlist.user_id == user_id
            ).order_by(Watchlist.stock_code).all()
        ]
        return {"portfolio": portfolio, "watchlist": watchlist}
    finally:
        db.close()


def _config() -> dict:
    path = os.getenv("ASHARE_CONFIG_PATH", str(BASE_DIR / "config.yaml"))
    try:
        return load_config(path)
    except ConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/healthz")
def healthz() -> dict:
    """健康检查：配置可解析且数据库可读时才返回 ok，失败返回 503。"""
    try:
        _config()
        init_db()
        db = get_db()
        try:
            db.query(func.count(QuoteSnapshot.stock_code)).scalar()
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"健康检查失败: {exc}")
        raise HTTPException(status_code=503, detail="服务不可用") from exc
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    next: str = Query(default="/"),
    registered: int = Query(default=0, ge=0, le=1),
):
    session_secret = _auth_settings()
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    payload = read_signed_token(token, session_secret, purpose="session")
    if payload and load_web_principal(payload["user_id"], payload["username"]):
        return RedirectResponse(_safe_next_path(next), status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "title": "登录",
            "next_path": _safe_next_path(next),
            "error": None,
            "notice": "账号创建成功，请登录。" if registered else None,
            "registration_available": registration_code_configured(),
        },
    )


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    session_secret = _auth_settings()
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    submitted_username = form.get("username", [""])[0]
    submitted_password = form.get("password", [""])[0]
    next_path = _safe_next_path(form.get("next", ["/"])[0])
    remember = form.get("remember", [""])[0] == "yes"
    principal = authenticate_web_user(submitted_username, submitted_password)
    if principal is None:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "title": "登录",
                "next_path": next_path,
                "error": "用户名或密码不正确",
                "notice": None,
                "registration_available": registration_code_configured(),
            },
            status_code=401,
        )

    ttl = REMEMBER_SESSION_TTL_SECONDS if remember else SESSION_TTL_SECONDS
    response = RedirectResponse(next_path, status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        _make_session_token(
            principal.username,
            session_secret,
            int(time.time()) + ttl,
            principal.user_id,
        ),
        max_age=ttl if remember else None,
        httponly=True,
        secure=request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https",
        samesite="lax",
        path="/",
    )
    return response


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    _auth_settings()
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={
            "title": "注册",
            "error": None,
            "registration_available": registration_code_configured(),
        },
    )


@app.post("/register", response_class=HTMLResponse)
async def register_submit(request: Request):
    _auth_settings()
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    username = form.get("username", [""])[0]
    password = form.get("password", [""])[0]
    password_confirm = form.get("password_confirm", [""])[0]
    registration_code = form.get("registration_code", [""])[0]
    if password != password_confirm:
        error = "两次输入的密码不一致"
    else:
        try:
            register_web_user(username, password, registration_code)
        except WebAuthError as exc:
            error = str(exc)
        else:
            return RedirectResponse("/login?registered=1", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={
            "title": "注册",
            "error": error,
            "registration_available": registration_code_configured(),
            "username": username,
        },
        status_code=400,
    )


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


def _account_context(
    request: Request,
    config: dict,
    principal: WebPrincipal,
    result: Optional[str] = None,
) -> dict:
    user_id = _personal_user_id(config, principal)
    data = (
        _load_account_data(user_id)
        if user_id is not None
        else {"portfolio": [], "watchlist": []}
    )
    session_secret = _auth_settings()
    data["csrf_token"] = _make_csrf_token(
        principal.username,
        session_secret,
        int(time.time()) + CSRF_TTL_SECONDS,
        principal.user_id,
    )
    notice_data = ACCOUNT_RESULT_MESSAGES.get(result)
    return {
        "data": data,
        "notice": (
            {"tone": notice_data[0], "text": notice_data[1]}
            if notice_data
            else None
        ),
        "legacy_yaml": user_id is None,
    }


@app.get("/account", response_class=HTMLResponse)
def account_page(
    request: Request,
    result: Optional[str] = Query(default=None),
    current_user: WebPrincipal = Depends(require_auth),
):
    config = _config()
    context = _account_context(request, config, current_user, result)
    return templates.TemplateResponse(
        request=request,
        name="account.html",
        context={**context, "title": "我的数据"},
    )


def _account_stock_name(db, code: str, submitted_name: str) -> str:
    name = str(submitted_name or "").strip()
    if name:
        return name[:50]
    snapshot = db.get(QuoteSnapshot, code)
    return (snapshot.name if snapshot is not None else code)[:50]


@app.post("/account/portfolio")
async def account_portfolio_submit(
    request: Request,
    current_user: WebPrincipal = Depends(require_auth),
):
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    _require_csrf(form.get("csrf_token", [""])[0], current_user)
    try:
        config = _config()
        user_id = _account_user_id(config, current_user)
        code = normalize_stock_code(form.get("stock_code", [""])[0])
        name = form.get("name", [""])[0]
        buy_price = float(form.get("buy_price", [""])[0])
        shares = int(form.get("shares", [""])[0])
        stop_loss = float(form.get("stop_loss", ["-5"])[0])
        take_profit = float(form.get("take_profit", ["10"])[0])
        if (
            not all(isfinite(value) for value in (buy_price, stop_loss, take_profit))
            or buy_price <= 0
            or shares <= 0
            or stop_loss >= 0
            or take_profit <= 0
        ):
            raise ValueError("invalid portfolio values")
        init_db()
        db = get_db()
        try:
            if db.get(User, user_id) is None:
                raise ValueError("user missing")
            row = db.query(Portfolio).filter(
                Portfolio.user_id == user_id,
                Portfolio.stock_code == code,
            ).first()
            if row is None:
                row = Portfolio(user_id=user_id, stock_code=code)
                db.add(row)
            row.name = _account_stock_name(db, code, name)
            row.buy_price = buy_price
            row.shares = shares
            row.stop_loss = stop_loss
            row.take_profit = take_profit
            db.commit()
        finally:
            db.close()
        result = "portfolio-saved"
    except HTTPException:
        raise
    except (ValueError, TypeError):
        result = "invalid-data"
    except Exception as exc:
        logger.error(f"保存网页用户持仓失败: {exc}")
        result = "request-failed"
    return RedirectResponse(f"/account?result={result}", status_code=303)


@app.post("/account/portfolio/{stock_code}/delete")
async def account_portfolio_delete(
    request: Request,
    stock_code: str,
    current_user: WebPrincipal = Depends(require_auth),
):
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    _require_csrf(form.get("csrf_token", [""])[0], current_user)
    try:
        config = _config()
        user_id = _account_user_id(config, current_user)
        code = normalize_stock_code(stock_code)
        init_db()
        db = get_db()
        try:
            row = db.query(Portfolio).filter(
                Portfolio.user_id == user_id,
                Portfolio.stock_code == code,
            ).first()
            if row is not None:
                db.delete(row)
                db.commit()
        finally:
            db.close()
        result = "portfolio-deleted"
    except HTTPException:
        raise
    except (ValueError, TypeError):
        result = "invalid-data"
    except Exception as exc:
        logger.error(f"删除网页用户持仓失败: {exc}")
        result = "request-failed"
    return RedirectResponse(f"/account?result={result}", status_code=303)


@app.post("/account/watchlist")
async def account_watchlist_submit(
    request: Request,
    current_user: WebPrincipal = Depends(require_auth),
):
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    _require_csrf(form.get("csrf_token", [""])[0], current_user)
    try:
        config = _config()
        user_id = _account_user_id(config, current_user)
        code = normalize_stock_code(form.get("stock_code", [""])[0])
        init_db()
        db = get_db()
        try:
            if db.get(User, user_id) is None:
                raise ValueError("user missing")
            row = db.query(Watchlist).filter(
                Watchlist.user_id == user_id,
                Watchlist.stock_code == code,
            ).first()
            if row is None:
                row = Watchlist(user_id=user_id, stock_code=code)
                db.add(row)
            row.name = _account_stock_name(
                db, code, form.get("name", [""])[0]
            )
            db.commit()
        finally:
            db.close()
        result = "watchlist-saved"
    except HTTPException:
        raise
    except (ValueError, TypeError):
        result = "invalid-data"
    except Exception as exc:
        logger.error(f"保存网页用户关注股票失败: {exc}")
        result = "request-failed"
    return RedirectResponse(f"/account?result={result}", status_code=303)


@app.post("/account/watchlist/{stock_code}/delete")
async def account_watchlist_delete(
    request: Request,
    stock_code: str,
    current_user: WebPrincipal = Depends(require_auth),
):
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    _require_csrf(form.get("csrf_token", [""])[0], current_user)
    try:
        config = _config()
        user_id = _account_user_id(config, current_user)
        code = normalize_stock_code(stock_code)
        init_db()
        db = get_db()
        try:
            row = db.query(Watchlist).filter(
                Watchlist.user_id == user_id,
                Watchlist.stock_code == code,
            ).first()
            if row is not None:
                db.delete(row)
                db.commit()
        finally:
            db.close()
        result = "watchlist-deleted"
    except HTTPException:
        raise
    except (ValueError, TypeError):
        result = "invalid-data"
    except Exception as exc:
        logger.error(f"删除网页用户关注股票失败: {exc}")
        result = "request-failed"
    return RedirectResponse(f"/account?result={result}", status_code=303)


@app.get("/", response_class=HTMLResponse)
def overview(
    request: Request,
    current_user: WebPrincipal = Depends(require_auth),
):
    config = _config()
    user_id = _personal_user_id(config, current_user)
    data = load_overview(config, user_id=user_id)
    data["recommendations"] = load_recommendations(
        config, user_id=user_id, limit=4
    )
    return templates.TemplateResponse(
        request=request,
        name="overview.html",
        context={"data": data, "title": "总览"},
    )


@app.get("/recommendations", response_class=HTMLResponse)
def recommendations_page(
    request: Request,
    current_user: WebPrincipal = Depends(require_auth),
):
    config = _config()
    data = load_recommendations(
        config,
        user_id=_personal_user_id(config, current_user),
        limit=12,
    )
    return templates.TemplateResponse(
        request=request,
        name="recommendations.html",
        context={"data": data, "title": "荐股研究"},
    )


@app.get("/markets", response_class=HTMLResponse)
def markets_page(
    request: Request,
    _current_user: WebPrincipal = Depends(require_auth),
):
    data = load_market_overview(_config())
    return templates.TemplateResponse(
        request=request,
        name="markets.html",
        context={"data": data, "title": "全球市场"},
    )


@app.get("/screener", response_class=HTMLResponse)
def screener_page(
    request: Request,
    min_score: int = Query(default=50, ge=0, le=100),
    min_momentum: float = Query(default=-100, ge=-100, le=100),
    max_volatility: float = Query(default=100, ge=0, le=300),
    above_ma20: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=50),
    current_user: WebPrincipal = Depends(require_auth),
):
    config = _config()
    data = load_screener(
        config,
        user_id=_personal_user_id(config, current_user),
        min_score=min_score,
        min_momentum=min_momentum,
        max_volatility=max_volatility,
        above_ma20=above_ma20,
        limit=limit,
    )
    return templates.TemplateResponse(
        request=request,
        name="screener.html",
        context={"data": data, "title": "自助选股"},
    )


def _sparkline_svg(values: list[float], width: int = 720, height: int = 180) -> str:
    """Return SVG polyline points for an equity-curve sparkline."""
    if len(values) < 2:
        return ""
    low = min(values)
    high = max(values)
    span = high - low or 1.0
    last_index = len(values) - 1
    points = []
    for idx, val in enumerate(values):
        x = idx / last_index * width
        y = height - ((val - low) / span * (height - 8) + 4)
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


@app.get("/strategy", response_class=HTMLResponse)
def strategy_page(
    request: Request,
    stock_code: str = Query(default=""),
    fast: int = Query(default=5, ge=2, le=120),
    slow: int = Query(default=20, ge=3, le=250),
    start: str = Query(default=""),
    end: str = Query(default=""),
    cash: float = Query(default=100_000.0, ge=10_000, le=10_000_000),
    _current_user: WebPrincipal = Depends(require_auth),
):
    context: dict = {
        "title": "策略回测",
        "stock_code": stock_code,
        "fast": fast,
        "slow": slow,
        "start_date": start,
        "end_date": end,
        "initial_cash": int(cash),
        "has_result": False,
        "error": "",
        "total_return_pct": 0.0,
        "sharpe_ratio": None,
        "max_drawdown": 0.0,
        "max_drawdown_len": 0,
        "win_rate": 0.0,
        "won": 0,
        "lost": 0,
        "total_trades": 0,
        "gross_profit": 0.0,
        "net_profit": 0.0,
        "first_date": "",
        "last_date": "",
        "start_value": 0.0,
        "end_value": 0.0,
        "equity_points": 0,
        "sparkline": "",
        "sparkline_width": 720,
        "sparkline_height": 180,
    }

    if stock_code:
        result = run_backtest_web(
            stock_code=stock_code,
            fast=fast,
            slow=slow,
            start_date=start,
            end_date=end,
            initial_cash=cash,
        )
        context["error"] = result["error"]
        if result["ok"]:
            context["has_result"] = True
            context.update({
                "total_return_pct": result["total_return_pct"],
                "sharpe_ratio": result["sharpe_ratio"],
                "max_drawdown": result["max_drawdown"],
                "max_drawdown_len": result["max_drawdown_len"],
                "win_rate": result["win_rate"],
                "won": result["won"],
                "lost": result["lost"],
                "total_trades": result["total_trades"],
                "gross_profit": result["gross_profit"],
                "net_profit": result["net_profit"],
                "first_date": result["first_date"],
                "last_date": result["last_date"],
                "start_value": result["start_value"],
                "end_value": result["end_value"],
                "equity_points": len(result["equity_curve"]),
            })
            if result["equity_curve"]:
                values = [p["value"] for p in result["equity_curve"]]
                context["sparkline"] = _sparkline_svg(values)

    return templates.TemplateResponse(
        request=request,
        name="strategy.html",
        context=context,
    )


@app.get("/paper", response_class=HTMLResponse)
def paper_page(
    request: Request,
    result: Optional[str] = Query(default=None),
    current_user: WebPrincipal = Depends(require_auth),
):
    config = _config()
    data = load_paper_dashboard(
        config, user_id=_personal_user_id(config, current_user)
    )
    session_secret = _auth_settings()
    csrf_token = _make_csrf_token(
        current_user.username,
        session_secret,
        int(time.time()) + CSRF_TTL_SECONDS,
        current_user.user_id,
    )
    data["csrf_token"] = csrf_token
    data["buy_client_order_id"] = secrets.token_urlsafe(18)
    for position in data["positions"]:
        position["sell_client_order_id"] = secrets.token_urlsafe(18)
    message = PAPER_RESULT_MESSAGES.get(result)
    notice = {"tone": message[0], "text": message[1]} if message else None
    return templates.TemplateResponse(
        request=request,
        name="paper.html",
        context={"data": data, "notice": notice, "title": "模拟盘"},
    )


@app.post("/paper/orders")
async def paper_order_submit(
    request: Request,
    current_user: WebPrincipal = Depends(require_auth),
):
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    _require_csrf(form.get("csrf_token", [""])[0], current_user)
    config = _config()
    try:
        submit_paper_order(
            _paper_user_id(config, current_user),
            form.get("side", [""])[0],
            form.get("stock_code", [""])[0],
            form.get("quantity", [""])[0],
            form.get("client_order_id", [""])[0],
        )
        result = "order-created"
    except PaperTradingError as exc:
        result = exc.code if exc.code in PAPER_RESULT_MESSAGES else "request-failed"
    except Exception as exc:
        logger.error(f"提交模拟盘委托失败: {exc}")
        result = "request-failed"
    return RedirectResponse(f"/paper?result={result}", status_code=303)


@app.post("/paper/orders/{order_id}/cancel")
async def paper_order_cancel(
    request: Request,
    order_id: int,
    current_user: WebPrincipal = Depends(require_auth),
):
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    _require_csrf(form.get("csrf_token", [""])[0], current_user)
    try:
        config = _config()
        cancel_paper_order(_paper_user_id(config, current_user), order_id)
        result = "order-cancelled"
    except PaperTradingError as exc:
        result = exc.code if exc.code in PAPER_RESULT_MESSAGES else "request-failed"
    except Exception as exc:
        logger.error(f"撤销模拟盘委托失败: {exc}")
        result = "request-failed"
    return RedirectResponse(f"/paper?result={result}", status_code=303)


@app.get("/stocks/{stock_code}", response_class=HTMLResponse)
def stock_detail(
    request: Request,
    stock_code: str,
    _current_user: WebPrincipal = Depends(require_auth),
):
    try:
        code = normalize_stock_code(stock_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request=request,
        name="stock_detail.html",
        context={"stock": load_stock_summary(code), "title": f"股票 {code}"},
    )


@app.get("/api/stocks/{stock_code}/bars")
def stock_bars(
    stock_code: str,
    limit: int = Query(default=180, ge=20, le=500),
    _current_user: WebPrincipal = Depends(require_auth),
) -> dict:
    try:
        code = normalize_stock_code(stock_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    bars = load_daily_bars(code, adjust="qfq").tail(limit)
    return {
        "stock_code": code,
        "bars": [
            {
                "date": row.trade_date.isoformat(),
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
            }
            for row in bars.itertuples(index=False)
        ],
    }


@app.get("/system", response_class=HTMLResponse)
def system_page(
    request: Request,
    _current_user: WebPrincipal = Depends(require_auth),
):
    return templates.TemplateResponse(
        request=request,
        name="system.html",
        context={"data": load_system_status(), "title": "系统状态"},
    )


if __name__ == "__main__":
    uvicorn.run("web_app:app", host="127.0.0.1", port=8000)
