import hashlib
import hmac
import base64
import binascii
import os

from fastapi import HTTPException, Request, WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, User


def _encode_basic(username: str, password: str) -> str:
    raw = f"{username}:{password}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _basic_credential(value: str | None) -> tuple[str, str] | None:
    if not value or not value.startswith("Basic "):
        return None
    try:
        raw = base64.b64decode(value.removeprefix("Basic ").strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    if ":" not in raw:
        return None
    username, password = raw.split(":", 1)
    return username, password


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return base64.b64encode(salt).decode("ascii"), base64.b64encode(digest).decode("ascii")


def verify_password(password: str, salt_b64: str, expected_hash_b64: str) -> bool:
    try:
        salt = base64.b64decode(salt_b64)
    except binascii.Error:
        return False
    _, digest = hash_password(password, salt)
    return hmac.compare_digest(digest, expected_hash_b64)


async def get_user_by_credentials(session: AsyncSession, username: str, password: str) -> User | None:
    result = await session.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_salt, user.password_hash):
        return None
    return user


async def require_auth(request: Request) -> User:
    credential = _basic_credential(request.headers.get("Authorization"))
    if not credential:
        raise HTTPException(status_code=401, detail="invalid credentials", headers={"WWW-Authenticate": "Basic"})
    async with SessionLocal() as session:
        user = await get_user_by_credentials(session, *credential)
    if not user:
        raise HTTPException(status_code=401, detail="invalid credentials", headers={"WWW-Authenticate": "Basic"})
    return user


async def require_auth_websocket(websocket: WebSocket) -> bool:
    username = websocket.query_params.get("username")
    password = websocket.query_params.get("password")
    if not username or not password:
        return False
    async with SessionLocal() as session:
        return bool(await get_user_by_credentials(session, username, password))
