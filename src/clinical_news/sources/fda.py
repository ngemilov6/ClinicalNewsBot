"""FDA press announcements — RSS adapter (delegates to RSS)."""
from __future__ import annotations

from clinical_news.sources.rss import RSSAdapter


class FDAAdapter(RSSAdapter):
    """FDA press release feed; RSS-shaped, treated as authoritative.

    Default URL: https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml
    """
