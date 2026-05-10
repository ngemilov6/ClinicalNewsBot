"""Trafilatura fallback for article extraction."""
from __future__ import annotations

import logging

import httpx
import trafilatura

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MIN_BODY_CHARS = 500


def fetch(url: str) -> tuple[str, str]:
    try:
        resp = httpx.get(url, timeout=TIMEOUT, follow_redirects=True,
                         headers={"User-Agent": "ClinicalNewsBot/0.1 (+research)"})
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("trafilatura direct fetch failed", extra={"url": url, "err": str(exc)})
        return ("", "failed")
    body = trafilatura.extract(resp.text, include_comments=False, include_tables=False) or ""
    if len(body) < MIN_BODY_CHARS:
        return (body, "failed")
    return (body, "full")
