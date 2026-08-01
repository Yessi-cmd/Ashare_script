#!/usr/bin/env python3
"""Authenticated local dashboard for research and paper trading."""

from __future__ import annotations

import logging
import os
import base64
import hashlib
import hmac
import json
import secrets
import time
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
from database import QuoteSnapshot, get_db, init_db
from market_data import load_daily_bars, normalize_stock_code
from paper_trading import (
    PaperTradingError,
    cancel_paper_order,
    load_paper_dashboard,
    paper_owner_user_id,
    submit_paper_order,
)
from settings import ConfigError, load_config

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


def _auth_settings() -> tuple[str, str, str]:
    username = os.getenv("ASHARE_WEB_USERNAME", "")
    password = os.getenv("ASHARE_WEB_PASSWORD", "")
    session_secret = os.getenv("ASHARE_WEB_SESSION_SECRET", "")
    if not username or not password or not session_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Web 认证尚未完整配置",
        )
    return username, password, session_secret


def _make_session_token(username: str, session_secret: str, expires_at: int) -> str:
    payload = json.dumps(
        {"sub": username, "exp": expires_at},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(
        session_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{encoded}.{signature}"


def _read_session_token(token: str, username: str, session_secret: str) -> bool:
    try:
        encoded, signature = token.rsplit(".", 1)
        expected = hmac.new(
            session_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not secrets.compare_digest(signature, expected):
            return False
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
        return (
            payload.get("sub") == username
            and int(payload.get("exp", 0)) >= int(time.time())
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        return False


def _make_csrf_token(username: str, session_secret: str, expires_at: int) -> str:
    return _make_session_token(f"paper:{username}", session_secret, expires_at)


def _valid_csrf_token(token: str, username: str, session_secret: str) -> bool:
    return _read_session_token(token, f"paper:{username}", session_secret)


def _require_csrf(token: str, username: str) -> None:
    _auth_username, _password, session_secret = _auth_settings()
    if not _valid_csrf_token(token, username, session_secret):
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
) -> str:
    username, password, session_secret = _auth_settings()
    session_token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if session_token and _read_session_token(session_token, username, session_secret):
        return username
    if _valid_basic_auth(credentials, username, password):
        return username

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
def login_page(request: Request, next: str = Query(default="/")):
    username, _password, session_secret = _auth_settings()
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if token and _read_session_token(token, username, session_secret):
        return RedirectResponse(_safe_next_path(next), status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"title": "登录", "next_path": _safe_next_path(next), "error": None},
    )


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    username, password, session_secret = _auth_settings()
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    submitted_username = form.get("username", [""])[0]
    submitted_password = form.get("password", [""])[0]
    next_path = _safe_next_path(form.get("next", ["/"])[0])
    remember = form.get("remember", [""])[0] == "yes"
    valid = secrets.compare_digest(
        submitted_username.encode("utf-8"), username.encode("utf-8")
    ) and secrets.compare_digest(
        submitted_password.encode("utf-8"), password.encode("utf-8")
    )
    if not valid:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"title": "登录", "next_path": next_path, "error": "用户名或密码不正确"},
            status_code=401,
        )

    ttl = REMEMBER_SESSION_TTL_SECONDS if remember else SESSION_TTL_SECONDS
    response = RedirectResponse(next_path, status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        _make_session_token(username, session_secret, int(time.time()) + ttl),
        max_age=ttl if remember else None,
        httponly=True,
        secure=request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https",
        samesite="lax",
        path="/",
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@app.get("/", response_class=HTMLResponse)
def overview(request: Request, _username: str = Depends(require_auth)):
    config = _config()
    data = load_overview(config)
    data["recommendations"] = load_recommendations(config, limit=4)
    return templates.TemplateResponse(
        request=request,
        name="overview.html",
        context={"data": data, "title": "总览"},
    )


@app.get("/recommendations", response_class=HTMLResponse)
def recommendations_page(request: Request, _username: str = Depends(require_auth)):
    data = load_recommendations(_config(), limit=12)
    return templates.TemplateResponse(
        request=request,
        name="recommendations.html",
        context={"data": data, "title": "荐股研究"},
    )


@app.get("/markets", response_class=HTMLResponse)
def markets_page(request: Request, _username: str = Depends(require_auth)):
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
    _username: str = Depends(require_auth),
):
    data = load_screener(
        _config(),
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


@app.get("/paper", response_class=HTMLResponse)
def paper_page(
    request: Request,
    result: Optional[str] = Query(default=None),
    username: str = Depends(require_auth),
):
    config = _config()
    data = load_paper_dashboard(config)
    _auth_username, _password, session_secret = _auth_settings()
    csrf_token = _make_csrf_token(
        username, session_secret, int(time.time()) + CSRF_TTL_SECONDS
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
    username: str = Depends(require_auth),
):
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    _require_csrf(form.get("csrf_token", [""])[0], username)
    config = _config()
    try:
        submit_paper_order(
            paper_owner_user_id(config),
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
    username: str = Depends(require_auth),
):
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    _require_csrf(form.get("csrf_token", [""])[0], username)
    try:
        cancel_paper_order(paper_owner_user_id(_config()), order_id)
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
    _username: str = Depends(require_auth),
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
    _username: str = Depends(require_auth),
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
def system_page(request: Request, _username: str = Depends(require_auth)):
    return templates.TemplateResponse(
        request=request,
        name="system.html",
        context={"data": load_system_status(), "title": "系统状态"},
    )


if __name__ == "__main__":
    uvicorn.run("web_app:app", host="127.0.0.1", port=8000)
