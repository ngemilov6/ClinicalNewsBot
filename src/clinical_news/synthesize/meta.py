"""Meta-synthesis: weave cluster summaries into a single brief."""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from clinical_news.llm import gemini

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent.parent.parent / "prompts" / "meta_synthesis_v3.md"
PROMPT_VERSION = "meta_synthesis_v3"


def _trim_index(article_index: dict[str, dict]) -> dict[str, dict]:
    # Drop URL/published noise from the giant index — keep title + source for citation only.
    return {
        aid: {"source_id": v["source_id"], "title": v["title"][:200]}
        for aid, v in article_index.items()
    }


def synthesize(cluster_summaries: list[dict], article_index: dict[str, dict]) -> dict:
    template = PROMPT_PATH.read_text()
    prompt = (
        template
        .replace("{cluster_summaries_json}", json.dumps(cluster_summaries, indent=2))
        .replace("{article_index_json}", json.dumps(_trim_index(article_index), indent=2))
    )
    log.info("meta-synthesis call", extra={
        "clusters": len(cluster_summaries),
        "article_index_size": len(article_index),
        "approx_prompt_chars": len(prompt),
    })
    raw = gemini.generate_json(prompt)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error("meta synthesis parse failed", extra={"err": str(exc), "raw": raw[:200]})
        data = {"headline": "", "deck": "", "body_markdown": raw, "citations_used": []}
    data["_prompt_version"] = PROMPT_VERSION
    return data
