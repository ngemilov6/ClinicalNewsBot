"""ClinicalTrials.gov API v2 adapter.

Docs: https://clinicaltrials.gov/data-api/api
We pull recently updated studies and surface registration / status changes.
The 'body' for an entry is a synthetic markdown render of the study record so
downstream synthesis treats it like any other article.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

import httpx

from clinical_news.sources.base import NormalizedItem, SourceAdapter

log = logging.getLogger(__name__)

API = "https://clinicaltrials.gov/api/v2/studies"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class CTGovAdapter(SourceAdapter):
    def fetch(self) -> list[NormalizedItem]:
        since = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
        params = {
            "filter.advanced": f"AREA[LastUpdatePostDate]RANGE[{since},MAX]",
            "fields": ",".join([
                "NCTId", "BriefTitle", "OfficialTitle", "BriefSummary",
                "OverallStatus", "Phase", "Condition", "InterventionName",
                "LeadSponsorName", "LastUpdatePostDate", "StudyFirstPostDate",
                "PrimaryCompletionDate",
            ]),
            "pageSize": 100,
            "sort": "LastUpdatePostDate:desc",  # newest first
        }
        try:
            resp = httpx.get(API, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"ctgov API failed: {exc}") from exc

        data = resp.json()
        items: list[NormalizedItem] = []
        for study in data.get("studies", []):
            proto = study.get("protocolSection", {})
            ident = proto.get("identificationModule", {})
            status_mod = proto.get("statusModule", {})
            design = proto.get("designModule", {})
            cond_mod = proto.get("conditionsModule", {})
            arms_mod = proto.get("armsInterventionsModule", {})
            sponsor = proto.get("sponsorCollaboratorsModule", {})
            desc = proto.get("descriptionModule", {})

            nct_id = ident.get("nctId")
            if not nct_id:
                continue
            url = f"https://clinicaltrials.gov/study/{nct_id}"
            title = ident.get("briefTitle") or ident.get("officialTitle") or nct_id
            summary = desc.get("briefSummary", "")[:500]
            published = (
                status_mod.get("lastUpdatePostDateStruct", {}).get("date")
                or status_mod.get("studyFirstPostDateStruct", {}).get("date")
                or datetime.now(timezone.utc).date().isoformat()
            )

            body_lines = [
                f"# {title}",
                f"**NCT ID:** {nct_id}",
                f"**Status:** {status_mod.get('overallStatus', 'unknown')}",
                f"**Phases:** {', '.join(design.get('phases', []) or ['N/A'])}",
                f"**Conditions:** {', '.join(cond_mod.get('conditions', []) or ['N/A'])}",
                f"**Interventions:** {', '.join(i.get('name', '') for i in arms_mod.get('interventions', []))}",
                f"**Lead sponsor:** {sponsor.get('leadSponsor', {}).get('name', 'N/A')}",
                "",
                "## Brief summary",
                desc.get("briefSummary", "(no summary)"),
            ]

            items.append(NormalizedItem(
                source_id=self.source.id,
                external_id=nct_id,
                url=url,
                title=title,
                summary=summary,
                published_at=_to_iso_date(published),
                body="\n".join(body_lines),
            ))

        log.info("ctgov fetched", extra={"count": len(items)})
        return items


def _to_iso_date(s: str) -> str:
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return datetime.now(timezone.utc).isoformat()
