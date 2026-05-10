"""FastAPI dependencies: DB connection per request, admin auth."""
from __future__ import annotations

import secrets
import sqlite3
from typing import Iterator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from clinical_news import db
from clinical_news.config import Settings

_settings: Settings | None = None
_security = HTTPBasic()


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings


def get_db(settings: Settings = Depends(get_settings)) -> Iterator[sqlite3.Connection]:
    conn = db.connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def require_admin(
    credentials: HTTPBasicCredentials = Depends(_security),
    settings: Settings = Depends(get_settings),
) -> str:
    expected_user = "admin"
    expected_pw = (settings.gmail_app_password or "")  # placeholder, overridden below
    # We read the admin password from a dedicated env var so it doesn't share
    # storage with the SMTP password.
    import os
    expected_pw = os.environ.get("ADMIN_PASSWORD", "")
    if not expected_pw:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_PASSWORD not configured on server",
        )
    user_ok = secrets.compare_digest(credentials.username.encode(), expected_user.encode())
    pw_ok = secrets.compare_digest(credentials.password.encode(), expected_pw.encode())
    if not (user_ok and pw_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
