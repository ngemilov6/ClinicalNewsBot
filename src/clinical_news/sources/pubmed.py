"""PubMed E-utilities adapter — esearch + efetch.

Docs: https://www.ncbi.nlm.nih.gov/books/NBK25500/
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import httpx

from clinical_news.sources.base import NormalizedItem, SourceAdapter

log = logging.getLogger(__name__)

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
HEADERS = {"User-Agent": "ClinicalNewsBot/0.1 (+research; contact via repo)"}
DEFAULT_TERM = (
    '("clinical trial"[Publication Type] OR "randomized controlled trial"[Publication Type])'
    ' AND ("last 7 days"[edat])'
)


class PubMedAdapter(SourceAdapter):
    def fetch(self) -> list[NormalizedItem]:
        # esearch → list of PMIDs
        esearch = httpx.get(
            f"{EUTILS}/esearch.fcgi",
            params={
                "db": "pubmed",
                "term": DEFAULT_TERM,
                "retmax": 50,
                "retmode": "json",
                "sort": "pub_date",  # newest first; default would be relevance
            },
            timeout=TIMEOUT,
            headers=HEADERS,
        )
        esearch.raise_for_status()
        ids = esearch.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []

        # efetch → article metadata + abstracts
        efetch = httpx.get(
            f"{EUTILS}/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
            timeout=TIMEOUT,
            headers=HEADERS,
        )
        efetch.raise_for_status()

        root = ET.fromstring(efetch.text)
        items: list[NormalizedItem] = []
        for article in root.findall(".//PubmedArticle"):
            pmid_el = article.find(".//PMID")
            title_el = article.find(".//ArticleTitle")
            if pmid_el is None or title_el is None:
                continue
            pmid = pmid_el.text or ""
            title = "".join(title_el.itertext()).strip()
            abstract_parts = [
                "".join(t.itertext()).strip()
                for t in article.findall(".//Abstract/AbstractText")
            ]
            abstract = "\n\n".join(p for p in abstract_parts if p)
            journal = article.findtext(".//Journal/Title", default="")
            year = article.findtext(".//PubDate/Year", default="")
            month = article.findtext(".//PubDate/Month", default="01")
            day = article.findtext(".//PubDate/Day", default="01")
            published = _parse_pubmed_date(year, month, day)

            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            body = f"# {title}\n\n**Journal:** {journal}\n\n## Abstract\n\n{abstract or '(no abstract)'}"

            items.append(NormalizedItem(
                source_id=self.source.id,
                external_id=pmid,
                url=url,
                title=title,
                summary=(abstract or "")[:500],
                published_at=published,
                body=body,
            ))

        log.info("pubmed fetched", extra={"count": len(items)})
        return items


_MONTHS = {m: f"{i:02d}" for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1
)}


def _parse_pubmed_date(year: str, month: str, day: str) -> str:
    if not year:
        return datetime.now(timezone.utc).isoformat()
    mm = _MONTHS.get(month, month if month.isdigit() else "01")
    dd = day if day.isdigit() else "01"
    try:
        return datetime(int(year), int(mm), int(dd), tzinfo=timezone.utc).isoformat()
    except ValueError:
        return datetime(int(year), 1, 1, tzinfo=timezone.utc).isoformat()
