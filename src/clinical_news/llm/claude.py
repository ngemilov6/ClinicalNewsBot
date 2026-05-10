"""Optional Claude polish step. Gated on CLAUDE_API_KEY."""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

POLISH_PROMPT = (
    "You are an editor improving the prose of an internal weekly brief on "
    "clinical trials and clinical research. Improve clarity, flow, and brevity. "
    "Hard rules:\n"
    "- DO NOT add or change any factual claim, number, name, or date.\n"
    "- Preserve every `[ref:ID]` citation marker exactly where it appears.\n"
    "- Do not introduce new quotes; preserve existing ones verbatim.\n"
    "- Output the same JSON schema as the input: "
    "{headline, deck, body_markdown, citations_used}.\n\n"
    "INPUT:\n{input_json}\n"
)


def is_enabled() -> bool:
    return bool(os.environ.get("CLAUDE_API_KEY"))


def polish(synthesis: dict) -> dict:
    if not is_enabled():
        return synthesis
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic SDK not installed; skipping polish")
        return synthesis

    client = anthropic.Anthropic(api_key=os.environ["CLAUDE_API_KEY"])
    prompt = POLISH_PROMPT.replace("{input_json}", json.dumps({
        "headline": synthesis.get("headline", ""),
        "deck": synthesis.get("deck", ""),
        "body_markdown": synthesis.get("body_markdown", ""),
        "citations_used": synthesis.get("citations_used", []),
    }, indent=2))

    resp = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in resp.content if hasattr(block, "text"))
    try:
        polished = json.loads(text)
    except json.JSONDecodeError:
        log.warning("claude polish returned non-JSON; keeping original")
        return synthesis
    polished["_polished_by"] = "claude-opus-4-7"
    return polished
