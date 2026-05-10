"""RSS/Atom feed adapter."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser

from clinical_news.sources.base import NormalizedItem, SourceAdapter

log = logging.getLogger(__name__)


def _to_iso(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat()


def _external_id(entry: feedparser.FeedParserDict, url: str) -> str:
    raw = entry.get("id") or entry.get("guid") or url
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class RSSAdapter(SourceAdapter):
    def fetch(self) -> list[NormalizedItem]:
        if not self.source.rss_url:
            raise ValueError(f"source {self.source.id} has no rss_url")
        parsed = feedparser.parse(self.source.rss_url)
        if parsed.bozo and not parsed.entries:
            raise RuntimeError(f"feed parse failed: {parsed.bozo_exception!r}")
        items: list[NormalizedItem] = []
        for entry in parsed.entries:
            url = entry.get("link") or ""
            if not url:
                continue
            published = entry.get("published") or entry.get("updated")
            items.append(NormalizedItem(
                source_id=self.source.id,
                external_id=_external_id(entry, url),
                url=url,
                title=(entry.get("title") or "").strip(),
                summary=(entry.get("summary") or entry.get("description") or "").strip(),
                published_at=_to_iso(published),
            ))
        log.info("rss fetched", extra={"source_id": self.source.id, "count": len(items)})
        return items
