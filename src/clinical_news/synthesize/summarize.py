"""Per-cluster LLM summarization."""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from clinical_news.llm import gemini

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent.parent.parent / "prompts" / "cluster_summary_v1.md"
PROMPT_VERSION = "cluster_summary_v1"

MAX_ARTICLES_PER_CLUSTER = 8  # sample most-recent N when cluster is bigger
TOTAL_BODY_BUDGET_CHARS = 24_000  # keep one prompt under ~6k input tokens


def _article_payload(row: sqlite3.Row, art_id: str, body_cap: int) -> dict:
    body = (row["body"] or row["summary"] or "")[:body_cap]
    return {
        "id": art_id,
        "source_id": row["source_id"],
        "title": row["title"],
        "published_at": row["published_at"],
        "url": row["url"],
        "body": body,
    }


def summarize_cluster(rows: list[sqlite3.Row], id_for: dict[int, str]) -> dict:
    """Call Gemini for a single cluster. Returns parsed JSON + prompt_version."""
    sampled = sorted(rows, key=lambda r: r["published_at"], reverse=True)[:MAX_ARTICLES_PER_CLUSTER]
    body_cap = max(1_500, TOTAL_BODY_BUDGET_CHARS // max(len(sampled), 1))
    sources = [_article_payload(r, id_for[r["id"]], body_cap) for r in sampled]
    log.info("cluster summarize call", extra={
        "cluster_size": len(rows), "sampled": len(sampled),
        "body_cap": body_cap, "approx_input_chars": sum(len(s["body"]) for s in sources),
    })
    template = PROMPT_PATH.read_text()
    prompt = template.replace("{sources_json}", json.dumps(sources, indent=2))
    raw = gemini.generate_json(prompt)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("cluster summary parse failed", extra={"err": str(exc), "raw": raw[:200]})
        data = {"theme": "(parse error)", "key_facts": [], "disagreements": [], "primary_sources": []}
    data["_prompt_version"] = PROMPT_VERSION
    return data
