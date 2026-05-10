"""Article extraction via Jina Reader (https://r.jina.ai/<url>)."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

JINA_BASE = "https://r.jina.ai/"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MIN_BODY_CHARS = 500


def fetch(url: str) -> tuple[str, str]:
    """Return (text, quality) where quality is 'full' or 'failed'."""
    try:
        resp = httpx.get(JINA_BASE + url, timeout=TIMEOUT, follow_redirects=True,
                         headers={"X-Return-Format": "markdown"})
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("jina fetch failed", extra={"url": url, "err": str(exc)})
        return ("", "failed")
    body = resp.text or ""
    if len(body) < MIN_BODY_CHARS:
        return (body, "failed")
    return (body, "full")
