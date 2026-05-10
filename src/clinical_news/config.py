"""Configuration: settings from .env and source registry from sources.yaml."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

SourceType = Literal["newspaper", "registry", "journal", "regulator", "retraction"]
ExtractionStrategy = Literal["jina_reader", "rss_snippet_only", "trafilatura", "api_native"]


class Source(BaseModel):
    id: str
    name: str
    source_type: SourceType
    rss_url: str | None = None
    api_url: str | None = None
    paywalled: bool = False
    extraction_strategy: ExtractionStrategy = "jina_reader"
    bypass_filtering: bool = False
    active: bool = True


class Settings(BaseModel):
    gemini_api_key: str | None = None
    claude_api_key: str | None = None
    gmail_from: str | None = None
    gmail_to: str | None = None
    gmail_app_password: str | None = None
    db_path: Path = Field(default_factory=lambda: PROJECT_ROOT / "app_data" / "articles.db")
    log_level: str = "INFO"

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        return cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
            claude_api_key=os.getenv("CLAUDE_API_KEY") or None,
            gmail_from=os.getenv("GMAIL_FROM") or None,
            gmail_to=os.getenv("GMAIL_TO") or None,
            gmail_app_password=os.getenv("GMAIL_APP_PASSWORD") or None,
            db_path=Path(os.getenv("DB_PATH") or (PROJECT_ROOT / "app_data" / "articles.db")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )


def load_sources(path: Path | str | None = None) -> list[Source]:
    path = Path(path) if path else PROJECT_ROOT / "sources.yaml"
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text()) or []
    return [Source(**item) for item in raw]


def active_sources(path: Path | str | None = None) -> list[Source]:
    return [s for s in load_sources(path) if s.active]
