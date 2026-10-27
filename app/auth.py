import hashlib
import hmac
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .database import connect

COOKIE = "docscan_session"
router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=10, max_length=128)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=3, maxmem=64 * 1024 * 1024
    )
    return f"scrypt${salt}${digest.hex()}"


DUMMY_HASH = hash_password("unused dummy credential")


def verify_password(password: str, stored: str) -> bool:
    return hmac.compare_digest(hash_password(password, stored.split("$")[1]), stored)


def browser_write(request: Request) -> None:
    origin = request.headers.get("origin")
    same_origin = f"{request.url.scheme}://{request.url.netloc}"
    allowed = (*request.app.state.settings.allowed_origins, same_origin)
    if request.headers.get("x-requested-with") != "docscan" or (origin and origin not in allowed):
        raise HTTPException(403, "Yêu cầu không hợp lệ. Hãy mở lại ứng dụng.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Không cho phép yêu cầu từ website khác.")


def rate_limit(request: Request, action: str, maximum: int) -> None:
    now = int(time.time())
    # Do not trust a client-supplied X-Forwarded-For header.
    ip = request.client.host if request.client else "unknown"
    key = f"{action}:{ip}"
    with connect(request.app.state.settings) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM rate_limits WHERE reset_at <= ?", (now,))
        row = db.execute("SELECT attempts FROM rate_limits WHERE key = ?", (key,)).fetchone()
        if row and row["attempts"] >= maximum:
            raise HTTPException(
                429,
                "Bạn đã thử quá nhiều lần. Vui lòng thử lại sau 5 phút.",
                headers={"Retry-After": "300"},
            )
        db.execute(
            "INSERT INTO rate_limits VALUES (?, 1, ?) ON CONFLICT(key) DO UPDATE SET attempts = attempts + 1",
            (key, now + 300),
        )


def current_user(request: Request) -> dict:
    authorization = request.headers.get("authorization", "")
    bearer = authorization.startswith("Bearer ")
    token = authorization[7:] if bearer else request.cookies.get(COOKIE, "")
    if not token or len(token) > 200:
        raise HTTPException(401, "Vui lòng đăng nhập để tiếp tục.")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with connect(request.app.state.settings) as db:
        row = db.execute(
            "SELECT users.id, users.email, sessions.kind FROM sessions JOIN users ON users.id = sessions.user_id WHERE token_hash = ? AND expires_at > ?",
            (token_hash, int(time.time())),
        ).fetchone()
    if not row or row["kind"] != ("bearer" if bearer else "cookie"):
        raise HTTPException(401, "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.")
    if not bearer and request.method not in ("GET", "HEAD", "OPTIONS"):
        browser_write(request)
    request.state.session_hash = token_hash
    request.state.session_kind = row["kind"]
    return {"id": row["id"], "email": row["email"]}


@router.post("/register", status_code=201)
def register(credentials: Credentials, request: Request):
    browser_write(request)
    rate_limit(request, "register", 5)
    user = {"id": uuid.uuid4().hex, "email": str(credentials.email).casefold()}
    password_hash = hash_password(credentials.password)
    try:
        with connect(request.app.state.settings) as db:
            db.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?)",
                (user["id"], user["email"], password_hash, datetime.now(timezone.utc).isoformat()),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Email đã được đăng ký. Vui lòng đăng nhập.") from None
    return user


def issue_session(credentials: Credentials, request: Request, kind: str) -> tuple[dict, str]:
    rate_limit(request, "login", 10)
    settings = request.app.state.settings
    with connect(settings) as db:
        row = db.execute(
            "SELECT * FROM users WHERE email = ?", (str(credentials.email).casefold(),)
        ).fetchone()
    valid = verify_password(credentials.password, row["password_hash"] if row else DUMMY_HASH)
    if not row or not valid:
        raise HTTPException(401, "Email hoặc mật khẩu không đúng.")
    token = secrets.token_urlsafe(32)
    with connect(settings) as db:
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),))
        db.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                row["id"],
                kind,
                int(time.time()) + settings.session_seconds,
            ),
        )
    return {"id": row["id"], "email": row["email"]}, token


@router.post("/session")
def login(credentials: Credentials, request: Request, response: Response):
    browser_write(request)
    user, token = issue_session(credentials, request, "cookie")
    settings = request.app.state.settings
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=settings.secure_cookie,
        samesite="lax",
        max_age=settings.session_seconds,
        path="/api",
    )
    return user


@router.post("/token")
def token(credentials: Credentials, request: Request):
    if request.headers.get("origin"):
        browser_write(request)
    _, value = issue_session(credentials, request, "bearer")
    return {
        "access_token": value,
        "token_type": "bearer",
        "expires_in": request.app.state.settings.session_seconds,
    }


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return user


@router.delete("/session", status_code=204)
def logout(request: Request, response: Response, user: dict = Depends(current_user)):
    with connect(request.app.state.settings) as db:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (request.state.session_hash,))
    if request.state.session_kind == "cookie":
        response.delete_cookie(COOKIE, path="/api")
