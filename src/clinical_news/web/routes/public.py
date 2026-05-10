"""Public read-only routes."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from clinical_news.web.deps import get_db
from clinical_news.web.render import render_brief_html

router = APIRouter()


def get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


@router.get("/", response_class=HTMLResponse)
def home(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    latest = conn.execute(
        "SELECT id FROM synthesis_runs WHERE output_path IS NOT NULL "
        "ORDER BY ran_at DESC LIMIT 1"
    ).fetchone()
    if latest is None:
        return RedirectResponse("/library", status_code=302)
    return RedirectResponse(f"/briefs/{latest['id']}", status_code=302)


@router.get("/library", response_class=HTMLResponse)
def library(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    conn: sqlite3.Connection = Depends(get_db),
):
    offset = (page - 1) * per_page
    rows = conn.execute(
        "SELECT id, ran_at, headline, deck, article_count, cluster_count, "
        "       word_count, citation_coverage, status, output_path "
        "FROM synthesis_runs ORDER BY ran_at DESC LIMIT ? OFFSET ?",
        (per_page, offset),
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM synthesis_runs").fetchone()[0]
    templates = get_templates(request)
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "rows": rows,
            "page": page,
            "per_page": per_page,
            "total": total,
            "has_more": offset + len(rows) < total,
        },
    )


@router.get("/briefs/{brief_id}", response_class=HTMLResponse)
def brief(brief_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    row = conn.execute(
        "SELECT id, ran_at, headline, deck, output_path, citation_coverage, "
        "       word_count, status, article_count, cluster_count, body_md, "
        "       body_md_raw, article_index_json "
        "FROM synthesis_runs WHERE id = ?",
        (brief_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="brief not found")

    body_html = _render_brief_body(row)

    templates = get_templates(request)
    return templates.TemplateResponse(
        request, "brief.html", {"row": row, "body_html": body_html}
    )


def _render_brief_body(row) -> str:
    """Pick the best rendering path for this brief.

    Path A (new briefs): raw markdown + per-brief article index were saved
    at synthesis time. Render fresh HTML with sup-style footnotes that link
    to the correct sources.

    Path B (legacy briefs): only the rendered ``[^N]``-footnoted markdown is
    available. Convert it via the standard markdown library's footnote
    extension — the Sources block is already inline.
    """
    raw = row["body_md_raw"] or ""
    index_json = row["article_index_json"] or ""
    if raw and index_json:
        try:
            article_index = json.loads(index_json)
        except json.JSONDecodeError:
            article_index = {}
        return render_brief_html(
            headline=row["headline"] or "",
            deck=row["deck"] or "",
            body_markdown=raw,
            article_index=article_index,
        )

    # Legacy path: render the full markdown (already has [^N] footnote markers
    # and an inline ## Sources block — Markdown's footnotes extension wires
    # them up automatically).
    content = row["body_md"] or ""
    if not content and row["output_path"]:
        try:
            content = Path(row["output_path"]).read_text()
        except OSError:
            content = ""
    if not content:
        return "<p>(no content)</p>"

    import markdown as md_lib
    html = md_lib.markdown(
        content,
        extensions=["extra", "sane_lists", "footnotes"],
    )
    # Match the wrapper used by render_brief_html so CSS scoped to .brief-body applies.
    return f'<div class="brief-body">{html}</div>'


_FTS_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def _sanitize_fts_query(q: str) -> str:
    """Convert free-form user input into an FTS5 MATCH expression that can't
    blow up the parser. We extract alphanumeric tokens and AND them together.
    """
    tokens = _FTS_TOKEN_RE.findall(q.lower())[:8]
    if not tokens:
        return ""
    return " AND ".join(tokens)


@router.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    q: str = Query("", max_length=200),
    date_from: str = Query("", alias="from"),
    date_to: str = Query("", alias="to"),
    source_id: str = Query("", alias="source"),
    conn: sqlite3.Connection = Depends(get_db),
):
    templates = get_templates(request)
    sources = list(conn.execute(
        "SELECT id, name FROM sources WHERE active = 1 ORDER BY name"
    ))

    if not q.strip():
        return templates.TemplateResponse(
            request, "search.html",
            {"q": "", "sources": sources, "date_from": date_from,
             "date_to": date_to, "source_id": source_id,
             "brief_results": [], "article_results": [], "ran": False},
        )

    fts_q = _sanitize_fts_query(q)
    brief_results = []
    article_results = []
    if fts_q:
        brief_results = list(conn.execute(
            "SELECT s.id, s.ran_at, s.headline, s.deck, "
            "       snippet(briefs_fts, 2, '<mark>', '</mark>', '…', 24) AS excerpt "
            "FROM briefs_fts JOIN synthesis_runs s ON s.id = briefs_fts.rowid "
            "WHERE briefs_fts MATCH ? "
            "ORDER BY s.ran_at DESC LIMIT 25",
            (fts_q,),
        ))

        sql = (
            "SELECT a.id, a.title, a.url, a.source_id, a.published_at, a.status, "
            "       snippet(articles_fts, 2, '<mark>', '</mark>', '…', 24) AS excerpt "
            "FROM articles_fts JOIN articles a ON a.id = articles_fts.rowid "
            "WHERE articles_fts MATCH ?"
        )
        params: list = [fts_q]
        if date_from:
            sql += " AND a.published_at >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND a.published_at <= ?"
            params.append(date_to)
        if source_id:
            sql += " AND a.source_id = ?"
            params.append(source_id)
        sql += " ORDER BY a.published_at DESC LIMIT 50"
        article_results = list(conn.execute(sql, params))

    return templates.TemplateResponse(
        request, "search.html",
        {"q": q, "sources": sources, "date_from": date_from,
         "date_to": date_to, "source_id": source_id,
         "brief_results": brief_results, "article_results": article_results,
         "ran": True},
    )


def _strip_md_header(md: str) -> str:
    """The on-disk MD has '# headline' / '_deck_' / body / '## Sources'. We
    want just the body so render_brief_html can rebuild a consistent view."""
    if not md:
        return ""
    lines = md.splitlines()
    # Drop everything from "## Sources" onward.
    sources_idx = next(
        (i for i, l in enumerate(lines) if l.strip().startswith("## Sources")), None
    )
    if sources_idx is not None:
        lines = lines[:sources_idx]
    # Skip leading "# headline" and "_deck_" lines and surrounding blanks.
    start = 0
    skipped_h1 = False
    skipped_deck = False
    for i, l in enumerate(lines):
        s = l.strip()
        if not s:
            continue
        if not skipped_h1 and s.startswith("# "):
            skipped_h1 = True
            start = i + 1
            continue
        if skipped_h1 and not skipped_deck and s.startswith("_") and s.endswith("_"):
            skipped_deck = True
            start = i + 1
            continue
        break
    return "\n".join(lines[start:]).strip()
