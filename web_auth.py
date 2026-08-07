"""Web account storage, password hashing, and signed identity helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy import func

from database import User, WebUser, get_db, init_db

LOGIN_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$")
PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000
PASSWORD_SALT_BYTES = 16
REGISTRATION_CODE_ENV = "ASHARE_WEB_REGISTRATION_CODE"


class WebAuthError(ValueError):
    """A user-correctable account or registration error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WebPrincipal:
    """The authenticated web identity used by request handlers."""

    user_id: int
    username: str
    legacy_env: bool = False


def normalize_login_username(value: str) -> str:
    username = str(value or "").strip()
    if not LOGIN_USERNAME_PATTERN.fullmatch(username):
        raise WebAuthError(
            "invalid-username",
            "用户名需为 3-32 位字母、数字、点、短横线或下划线",
        )
    return username.lower()


def validate_registration_password(value: str) -> str:
    password = str(value or "")
    if len(password) < 8:
        raise WebAuthError("weak-password", "密码至少需要 8 位")
    if len(password) > 256:
        raise WebAuthError("weak-password", "密码不能超过 256 位")
    return password


def hash_password(password: str) -> str:
    """Return a self-describing PBKDF2 password hash."""
    salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii")
    return (
        f"{PASSWORD_SCHEME}${PASSWORD_ITERATIONS}$"
        f"{encode(salt)}${encode(digest)}"
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    """Verify a PBKDF2 hash without exposing timing-sensitive comparisons."""
    try:
        scheme, raw_iterations, raw_salt, raw_digest = str(encoded_hash).split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(raw_iterations)
        if iterations <= 0 or iterations > 2_000_000:
            return False
        decode = base64.urlsafe_b64decode
        salt = decode(raw_salt.encode("ascii"))
        expected = decode(raw_digest.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256", str(password).encode("utf-8"), salt, iterations
        )
        return secrets.compare_digest(actual, expected)
    except (TypeError, ValueError, UnicodeError):
        return False


def make_signed_token(
    user_id: int,
    username: str,
    session_secret: str,
    expires_at: int,
    *,
    purpose: str = "session",
) -> str:
    payload = json.dumps(
        {
            "uid": int(user_id),
            "sub": username,
            "exp": int(expires_at),
            "purpose": purpose,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(
        str(session_secret).encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded}.{signature}"


def read_signed_token(
    token: str,
    session_secret: str,
    *,
    purpose: str = "session",
) -> dict | None:
    try:
        encoded, signature = str(token).rsplit(".", 1)
        expected = hmac.new(
            str(session_secret).encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not secrets.compare_digest(signature, expected):
            return None
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
        if payload.get("purpose") != purpose:
            return None
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        user_id = int(payload.get("uid"))
        username = str(payload.get("sub", ""))
        if not username:
            return None
        return {"user_id": user_id, "username": username}
    except (AttributeError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return None


def registration_code_configured() -> bool:
    return bool(os.getenv(REGISTRATION_CODE_ENV, "").strip())


def _legacy_credentials() -> tuple[str, str] | None:
    username = os.getenv("ASHARE_WEB_USERNAME", "").strip()
    password = os.getenv("ASHARE_WEB_PASSWORD", "")
    if not username or not password:
        return None
    return username, password


def _legacy_data_user_id() -> int:
    """Resolve the old single-user data key without blocking account login."""
    try:
        from settings import get_owner_user_id, load_config

        config_path = os.getenv(
            "ASHARE_CONFIG_PATH",
            str(Path(__file__).resolve().with_name("config.yaml")),
        )
        return get_owner_user_id(load_config(config_path)) or 0
    except Exception:
        return 0


def _principal(account: WebUser) -> WebPrincipal:
    return WebPrincipal(
        user_id=int(account.user_id),
        username=account.login_username,
        legacy_env=bool(account.legacy_env),
    )


def _next_web_user_id(db) -> int:
    """Use a negative ID namespace so Telegram IDs cannot collide with Web users."""
    minimum = db.query(func.min(User.user_id)).scalar()
    return min(-1, int(minimum) - 1) if minimum is not None else -1


def ensure_legacy_web_user() -> WebPrincipal | None:
    """Lazily migrate the old environment-variable account into the DB."""
    credentials = _legacy_credentials()
    if credentials is None:
        return None
    raw_username, password = credentials
    try:
        username = normalize_login_username(raw_username)
    except WebAuthError:
        return None

    init_db()
    db = get_db()
    try:
        account = db.query(WebUser).filter(
            WebUser.login_username == username
        ).first()
        if account is not None:
            return _principal(account)

        data_user_id = _legacy_data_user_id()
        user = db.get(User, data_user_id)
        if user is None:
            user = User(user_id=data_user_id, username=username)
            db.add(user)
            db.flush()
        account = WebUser(
            user_id=user.user_id,
            login_username=username,
            password_hash=hash_password(password),
            legacy_env=True,
        )
        db.add(account)
        db.commit()
        db.refresh(account)
        return _principal(account)
    except IntegrityError:
        db.rollback()
        account = db.query(WebUser).filter(
            WebUser.login_username == username
        ).first()
        return _principal(account) if account is not None else None
    finally:
        db.close()


def authenticate_web_user(username: str, password: str) -> WebPrincipal | None:
    """Authenticate a database account, including the legacy env migration."""
    try:
        normalized = normalize_login_username(username)
    except WebAuthError:
        return None

    init_db()
    db = get_db()
    try:
        account = db.query(WebUser).filter(
            WebUser.login_username == normalized
        ).first()
        if account is not None:
            if verify_password(password, account.password_hash):
                return _principal(account)
            legacy = _legacy_credentials()
            if account.legacy_env and legacy and legacy[0].strip().lower() == normalized:
                if secrets.compare_digest(str(password), legacy[1]):
                    account.password_hash = hash_password(legacy[1])
                    db.commit()
                    return _principal(account)
            return None
    finally:
        db.close()

    legacy = _legacy_credentials()
    if legacy is None or legacy[0].strip().lower() != normalized:
        return None
    if not secrets.compare_digest(str(password), legacy[1]):
        return None
    return ensure_legacy_web_user()


def load_web_principal(user_id: int, username: str) -> WebPrincipal | None:
    """Resolve a session subject against the current database account."""
    init_db()
    db = get_db()
    try:
        account = db.query(WebUser).filter(
            WebUser.user_id == int(user_id),
            WebUser.login_username == str(username),
        ).first()
        return _principal(account) if account is not None else None
    finally:
        db.close()


def register_web_user(
    username: str,
    password: str,
    registration_code: str,
) -> WebPrincipal:
    """Create a new isolated web account when the invitation is valid."""
    expected_code = os.getenv(REGISTRATION_CODE_ENV, "").strip()
    if not expected_code:
        raise WebAuthError("registration-disabled", "当前未开放注册，请联系管理员")
    if not secrets.compare_digest(str(registration_code or ""), expected_code):
        raise WebAuthError("invalid-registration-code", "邀请码不正确")
    normalized = normalize_login_username(username)
    checked_password = validate_registration_password(password)

    init_db()
    db = get_db()
    try:
        if db.query(WebUser).filter(
            WebUser.login_username == normalized
        ).first() is not None:
            raise WebAuthError("username-taken", "用户名已存在，请换一个")

        user = User(user_id=_next_web_user_id(db), username=normalized)
        db.add(user)
        db.flush()
        account = WebUser(
            user_id=user.user_id,
            login_username=normalized,
            password_hash=hash_password(checked_password),
            legacy_env=False,
        )
        db.add(account)
        db.commit()
        db.refresh(account)
        return _principal(account)
    except WebAuthError:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise WebAuthError("username-taken", "用户名已存在，请换一个") from exc
    finally:
        db.close()
