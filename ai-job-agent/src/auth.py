"""JWT authentication helpers for the multi-user API."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

import db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)

JWT_SECRET = os.getenv("JWT_SECRET", "resume-agent-dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "168"))  # 7 days


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:  # noqa: BLE001 — malformed hash
        return False


def create_access_token(user_id: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="פג תוקף ההתחברות — התחבר מחדש",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="טוקן לא תקין — התחבר מחדש",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def register_user(email: str, password: str, *, db_path=None) -> dict[str, Any]:
    """Create a new auth user. Raises ValueError on validation / duplicate email."""
    email = (email or "").strip().lower()
    password = password or ""
    if not email or "@" not in email:
        raise ValueError("כתובת אימייל לא תקינה")
    if len(password) < 6:
        raise ValueError("הסיסמה חייבת להכיל לפחות 6 תווים")

    path = db_path or db.REGISTRY_DB_PATH
    db.ensure_auth_schema(path)
    existing = db.get_user_by_email(email, db_path=path)
    if existing is not None:
        raise ValueError("משתמש עם אימייל זה כבר קיים")

    user_id = uuid.uuid4().hex
    return db.create_user(
        user_id,
        email=email,
        hashed_password=hash_password(password),
        display_name=email.split("@")[0],
        db_path=path,
    )


def authenticate_user(email: str, password: str, *, db_path=None) -> dict[str, Any] | None:
    email = (email or "").strip().lower()
    path = db_path or db.REGISTRY_DB_PATH
    user = db.get_user_by_email(email, db_path=path)
    if user is None:
        return None
    if not verify_password(password, user.get("hashed_password") or ""):
        return None
    return user


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "email": user.get("email"),
        "display_name": user.get("display_name"),
        "created_at": user.get("created_at"),
    }


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    """Parse Bearer JWT and return the owning user row."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="נדרשת התחברות",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="טוקן לא תקין — התחבר מחדש",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = db.get_user_by_id(str(user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="המשתמש לא נמצא — התחבר מחדש",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
