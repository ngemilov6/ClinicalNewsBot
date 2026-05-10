"""RetractionWatch RSS adapter (delegates to RSS).

The RetractionWatch blog publishes an RSS feed; the open-access database
itself is queryable via Crossref's REST API but their feed is the simplest
ingest surface for this scale.
"""
from __future__ import annotations

from clinical_news.sources.rss import RSSAdapter


class RetractionWatchAdapter(RSSAdapter):
    """Default URL: https://retractionwatch.com/feed/"""
