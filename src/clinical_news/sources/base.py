"""Source adapter interface and the normalized-item shape."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from clinical_news.config import Source


class NormalizedItem(BaseModel):
    source_id: str
    external_id: str
    url: str
    title: str
    summary: str = ""
    published_at: str  # ISO8601
    fetched_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    body: str | None = None  # set by api_native adapters that bring full text inline


class SourceAdapter(ABC):
    def __init__(self, source: Source) -> None:
        self.source = source

    @abstractmethod
    def fetch(self) -> list[NormalizedItem]:
        ...
