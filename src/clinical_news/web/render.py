"""Render a synthesis JSON record as HTML for in-browser reading.

Footnote refs in the markdown body are converted to numbered sup-links that
jump to the Sources section. The Markdown -> HTML pass uses the standard
``markdown`` library with the ``footnotes``-style numbering we already produce.
"""
from __future__ import annotations

import re

import markdown as md_lib

REF_RE = re.compile(r"\[ref:([A-Za-z0-9_\-]+)\]")


def render_brief_html(headline: str, deck: str, body_markdown: str,
                       article_index: dict[str, dict]) -> str:
    """Returns a single HTML fragment ready to drop into the brief template."""
    used_ids: list[str] = []
    seen: set[str] = set()
    for ref in REF_RE.findall(body_markdown):
        if ref not in seen and ref in article_index:
            seen.add(ref)
            used_ids.append(ref)
    footnote_for = {ref: i + 1 for i, ref in enumerate(used_ids)}

    def _sub(m: re.Match) -> str:
        ref = m.group(1)
        n = footnote_for.get(ref)
        if n is None:
            return m.group(0)
        return f'<sup><a href="#fn{n}" class="cite">[{n}]</a></sup>'

    body_with_sup = REF_RE.sub(_sub, body_markdown)
    body_html = md_lib.markdown(body_with_sup, extensions=["extra", "sane_lists"])

    sources_html_parts = ['<ol class="sources">']
    for ref in used_ids:
        art = article_index[ref]
        n = footnote_for[ref]
        title = art.get("title", "(untitled)")
        url = art.get("url", "#")
        source_id = art.get("source_id", "")
        published = (art.get("published_at") or "")[:10]
        sources_html_parts.append(
            f'<li id="fn{n}"><span class="src">{source_id}</span> '
            f'— <a href="{url}" target="_blank" rel="noopener">{title}</a> '
            f'<span class="date">({published})</span></li>'
        )
    sources_html_parts.append("</ol>")
    sources_html = "\n".join(sources_html_parts)

    parts = [f'<h1 class="brief-headline">{headline}</h1>']
    if deck:
        parts.append(f'<p class="brief-deck">{deck}</p>')
    parts.append(f'<div class="brief-body">{body_html}</div>')
    parts.append('<h2 class="sources-heading">Sources</h2>')
    parts.append(sources_html)
    return "\n".join(parts)


