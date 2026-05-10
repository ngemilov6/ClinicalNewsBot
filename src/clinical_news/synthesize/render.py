"""Render structured synthesis output as Markdown with footnote citations."""
from __future__ import annotations

import re

REF_RE = re.compile(r"\[ref:([A-Za-z0-9_\-]+)\]")


def render(synthesis: dict, article_index: dict[str, dict]) -> str:
    headline = synthesis.get("headline", "Weekly clinical-trial brief")
    deck = synthesis.get("deck", "")
    body = synthesis.get("body_markdown", "")

    used_ids: list[str] = []
    seen: set[str] = set()
    for art_id in REF_RE.findall(body):
        if art_id not in seen and art_id in article_index:
            seen.add(art_id)
            used_ids.append(art_id)

    footnote_for = {art_id: i + 1 for i, art_id in enumerate(used_ids)}
    body_with_footnotes = REF_RE.sub(
        lambda m: f"[^{footnote_for[m.group(1)]}]" if m.group(1) in footnote_for else m.group(0),
        body,
    )

    parts = [f"# {headline}"]
    if deck:
        parts.append(f"_{deck}_")
    parts.append(body_with_footnotes)

    if used_ids:
        parts.append("\n## Sources\n")
        for art_id in used_ids:
            art = article_index[art_id]
            n = footnote_for[art_id]
            parts.append(
                f"[^{n}]: **{art['source_id']}** — [{art['title']}]({art['url']}) "
                f"({art['published_at'][:10]})"
            )

    return "\n\n".join(parts)
