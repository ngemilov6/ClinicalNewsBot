"""LLM relevance check via Gemini. Used only on borderline candidates."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from clinical_news.llm import gemini

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent.parent.parent / "prompts" / "relevance_v2.md"
PROMPT_VERSION = "relevance_v2"


def classify(title: str, summary: str) -> dict:
    """Return {relevant: bool, confidence: float, reason: str, prompt_version: str}."""
    template = PROMPT_PATH.read_text()
    prompt = template.replace("{title}", title).replace("{summary}", summary or "(no summary)")
    raw = gemini.generate_json(prompt)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("relevance LLM returned non-JSON", extra={"raw": raw[:200]})
        return {"relevant": False, "confidence": 0.0, "reason": "parse_error",
                "prompt_version": PROMPT_VERSION}
    return {
        "relevant": bool(data.get("relevant", False)),
        "confidence": float(data.get("confidence", 0.0)),
        "reason": str(data.get("reason", ""))[:500],
        "prompt_version": PROMPT_VERSION,
    }
