"""EMA news/press releases — RSS adapter (delegates to RSS)."""
from __future__ import annotations

from clinical_news.sources.rss import RSSAdapter


class EMAAdapter(RSSAdapter):
    """European Medicines Agency news feed.

    Default URL: https://www.ema.europa.eu/en/rss.xml
    """
