"""Article extraction with fallback chain: Jina → trafilatura → snippet."""
from __future__ import annotations

import logging

from clinical_news.config import Source
from clinical_news.extract import jina, paywall, readability

log = logging.getLogger(__name__)


def extract_article(source: Source, url: str, snippet: str) -> tuple[str, str]:
    """Return (body, quality) where quality ∈ {full, snippet_only, failed}."""
    if source.extraction_strategy == "rss_snippet_only" or source.paywalled:
        return (snippet, "snippet_only")

    if source.extraction_strategy == "api_native":
        # adapter already supplied full text in `snippet` param convention
        return (snippet, "full" if snippet else "failed")

    body, quality = jina.fetch(url)
    if quality == "full" and not paywall.is_paywalled(body):
        return (body, "full")

    body2, quality2 = readability.fetch(url)
    if quality2 == "full" and not paywall.is_paywalled(body2):
        return (body2, "full")

    if snippet:
        return (snippet, "snippet_only")
    return ("", "failed")
