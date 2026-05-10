"""Orchestrates the ingest and synthesis workflows."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from clinical_news import db
from clinical_news.config import Settings, Source, active_sources
from clinical_news.extract import extract_article
from clinical_news.filter import embedding as emb_filter
from clinical_news.filter import keyword as kw_filter
from clinical_news.filter import llm_relevance
from clinical_news.sources.base import NormalizedItem, SourceAdapter

EMBEDDING_LOWER = 0.45  # below: drop
EMBEDDING_UPPER = 0.65  # above: accept
LLM_ACCEPT = 0.6
LLM_REVIEW = 0.4

log = logging.getLogger(__name__)


def _adapter_for(source: Source) -> SourceAdapter:
    """Pick the right adapter for a source by id prefix or source_type."""
    from clinical_news.sources.ctgov import CTGovAdapter
    from clinical_news.sources.ema import EMAAdapter
    from clinical_news.sources.fda import FDAAdapter
    from clinical_news.sources.pubmed import PubMedAdapter
    from clinical_news.sources.retraction_watch import RetractionWatchAdapter
    from clinical_news.sources.rss import RSSAdapter

    if source.id.startswith("ctgov"):
        return CTGovAdapter(source)
    if source.id.startswith("pubmed"):
        return PubMedAdapter(source)
    if source.id.startswith("fda"):
        return FDAAdapter(source)
    if source.id.startswith("ema"):
        return EMAAdapter(source)
    if source.id.startswith("retraction"):
        return RetractionWatchAdapter(source)
    if source.rss_url:
        return RSSAdapter(source)
    raise NotImplementedError(f"no adapter for source {source.id}")


def _upsert_source(conn: sqlite3.Connection, s: Source) -> None:
    conn.execute(
        """
        INSERT INTO sources (id, name, rss_url, api_url, source_type, paywalled,
                             extraction_strategy, bypass_filtering, active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            rss_url=excluded.rss_url,
            api_url=excluded.api_url,
            source_type=excluded.source_type,
            paywalled=excluded.paywalled,
            extraction_strategy=excluded.extraction_strategy,
            bypass_filtering=excluded.bypass_filtering,
            active=excluded.active
        """,
        (s.id, s.name, s.rss_url, s.api_url, s.source_type, int(s.paywalled),
         s.extraction_strategy, int(s.bypass_filtering), int(s.active)),
    )


def _already_seen(conn: sqlite3.Connection, source_id: str, external_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM articles WHERE source_id = ? AND external_id = ?",
        (source_id, external_id),
    ).fetchone()
    return row is not None


def _log_error(conn: sqlite3.Connection, source_id: str | None, url: str | None,
               stage: str, message: str) -> None:
    conn.execute(
        "INSERT INTO ingest_errors (source_id, url, stage, error_message, occurred_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (source_id, url, stage, message, datetime.now(timezone.utc).isoformat()),
    )


def _persist_article(conn: sqlite3.Connection, item: NormalizedItem,
                     body: str, quality: str, status: str,
                     relevance_confidence: float | None = None,
                     relevance_reason: str | None = None,
                     embedding: bytes | None = None,
                     prompt_version: str | None = None) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO articles
            (source_id, external_id, url, title, summary, body,
             published_at, fetched_at, status, extraction_quality,
             relevance_confidence, relevance_reason, embedding, prompt_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (item.source_id, item.external_id, item.url, item.title, item.summary,
         body, item.published_at, item.fetched_at, status, quality,
         relevance_confidence, relevance_reason, embedding, prompt_version),
    )


def _classify(item: NormalizedItem) -> tuple[str, float | None, str | None, bytes | None, str | None]:
    """Run keyword → embedding → LLM gates.

    Returns (status, confidence, reason, embedding_blob, prompt_version).
    Status ∈ {ingested, rejected, needs_review, candidate}.
    """
    if not kw_filter.is_candidate(item.title, item.summary):
        return ("rejected", None, "keyword_drop", None, None)

    text = f"{item.title}. {item.summary}"
    try:
        sim, emb = emb_filter.score(text)
    except Exception as exc:
        log.warning("embedding failed", extra={"err": str(exc)})
        # Fall back to LLM-only on embedding failure.
        sim, emb = (0.5, None)

    blob = emb_filter.serialize(emb) if emb is not None else None

    if sim >= EMBEDDING_UPPER:
        return ("ingested", sim, "embedding_accept", blob, None)
    if sim < EMBEDDING_LOWER:
        return ("rejected", sim, "embedding_drop", blob, None)

    try:
        verdict = llm_relevance.classify(item.title, item.summary)
    except Exception as exc:
        log.warning("llm relevance failed", extra={"err": str(exc)})
        return ("needs_review", sim, f"llm_error: {exc}", blob, None)

    conf = verdict["confidence"]
    pv = verdict["prompt_version"]
    if verdict["relevant"] and conf >= LLM_ACCEPT:
        return ("ingested", conf, verdict["reason"], blob, pv)
    if conf >= LLM_REVIEW:
        return ("needs_review", conf, verdict["reason"], blob, pv)
    return ("rejected", conf, verdict["reason"], blob, pv)


def run_ingest(settings: Settings) -> None:
    db.migrate(settings.db_path)
    sources = active_sources()
    if not sources:
        log.warning("no active sources; populate sources.yaml")
        return

    with db.connect(settings.db_path) as conn:
        for s in sources:
            _upsert_source(conn, s)

        for s in sources:
            try:
                adapter = _adapter_for(s)
                items = adapter.fetch()
            except Exception as exc:
                log.exception("fetch failed", extra={"source_id": s.id})
                _log_error(conn, s.id, s.rss_url or s.api_url, "fetch", str(exc))
                continue

            new_items = [it for it in items if not _already_seen(conn, it.source_id, it.external_id)]
            new_items.sort(key=lambda it: it.published_at, reverse=True)  # newest first
            log.info("ingest progress", extra={
                "source_id": s.id, "fetched": len(items), "new": len(new_items),
            })

            for item in new_items:
                if s.bypass_filtering:
                    status, conf, reason, blob, pv = ("ingested", None, "bypass", None, None)
                else:
                    try:
                        status, conf, reason, blob, pv = _classify(item)
                    except Exception as exc:
                        log.exception("classify failed", extra={"url": item.url})
                        _log_error(conn, s.id, item.url, "filter", str(exc))
                        status, conf, reason, blob, pv = ("needs_review", None, str(exc), None, None)

                if status == "rejected":
                    body, quality = ("", "failed")
                else:
                    try:
                        if item.body:
                            body, quality = (item.body, "full")
                        else:
                            body, quality = extract_article(s, item.url, item.summary)
                    except Exception as exc:
                        log.exception("extract failed", extra={"url": item.url})
                        _log_error(conn, s.id, item.url, "extract", str(exc))
                        body, quality = (item.summary, "failed")
                    if not (body or item.summary):
                        status = "error"

                try:
                    _persist_article(
                        conn, item, body, quality, status,
                        relevance_confidence=conf, relevance_reason=reason,
                        embedding=blob, prompt_version=pv,
                    )
                except Exception as exc:
                    log.exception("persist failed", extra={"url": item.url})
                    _log_error(conn, s.id, item.url, "persist", str(exc))


def run_synthesis(settings: Settings, dry_run: bool = False) -> None:
    from clinical_news.llm import claude
    from clinical_news.synthesize import cluster, meta, render, summarize, validate

    db.migrate(settings.db_path)
    output_dir = settings.db_path.parent / "synthesis"
    output_dir.mkdir(parents=True, exist_ok=True)

    with db.connect(settings.db_path) as conn:
        rows = cluster.load_recent(conn, days=7)
        if len(rows) < 3:
            log.warning("insufficient articles for synthesis", extra={"n": len(rows)})
            _record_run(conn, n=len(rows), n_clusters=0, output_path=None,
                        coverage=None, prompt_version=None, model=None,
                        status="skipped_insufficient")
            return

        # Stable string IDs used inside prompts so the LLM can cite them.
        id_for_db_id = {r["id"]: f"art_{r['id']:05d}" for r in rows}
        article_index = {
            id_for_db_id[r["id"]]: {
                "source_id": r["source_id"],
                "title": r["title"],
                "url": r["url"],
                "published_at": r["published_at"],
            }
            for r in rows
        }

        clusters = cluster.assign_clusters(conn, rows)
        log.info("clustering done", extra={
            "articles": len(rows), "clusters": len(clusters),
        })
        cluster_summaries: list[dict] = []
        for i, (cid, cluster_rows) in enumerate(clusters.items(), start=1):
            log.info("summarizing cluster", extra={
                "i": i, "of": len(clusters), "cluster_size": len(cluster_rows),
            })
            summary = summarize.summarize_cluster(cluster_rows, id_for_db_id)
            summary["cluster_id"] = cid
            summary["article_ids"] = [id_for_db_id[r["id"]] for r in cluster_rows]
            cluster_summaries.append(summary)
        log.info("running meta-synthesis", extra={"clusters": len(cluster_summaries)})

        synthesis = meta.synthesize(cluster_summaries, article_index)
        if claude.is_enabled():
            synthesis = claude.polish(synthesis)

        valid_ids = set(article_index.keys())
        result = validate.validate(synthesis, valid_ids)
        log.info("synthesis validated", extra={
            "ok": result.ok,
            "coverage": result.citation_coverage,
            "unresolved": result.unresolved,
        })

        markdown = render.render(synthesis, article_index)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        # Best-effort file write: useful for local dev. On Vercel/Actions the
        # filesystem is ephemeral, so the canonical store is the DB column.
        path: Path | None = output_dir / f"brief_{ts}.md"
        try:
            path.write_text(markdown)
            log.info("synthesis written to disk", extra={"path": str(path)})
        except OSError as exc:
            log.info("disk write skipped", extra={"err": str(exc)})
            path = None

        if not dry_run:
            try:
                from clinical_news.deliver import email as email_deliver
                email_deliver.send_brief(settings, markdown, f"brief_{ts}.md")
            except Exception as exc:
                log.warning("email delivery failed", extra={"err": str(exc)})

        for r in rows:
            conn.execute("UPDATE articles SET status = 'synthesized' WHERE id = ?", (r["id"],))

        headline = synthesis.get("headline", "") or ""
        deck = synthesis.get("deck", "") or ""
        body_md = synthesis.get("body_markdown", "") or ""
        word_count = len(body_md.split())

        run_id = _record_run(
            conn,
            n=len(rows),
            n_clusters=len(clusters),
            output_path=str(path) if path else None,
            coverage=result.citation_coverage,
            prompt_version=meta.PROMPT_VERSION,
            model="gemini-flash-latest" + ("+claude" if claude.is_enabled() else ""),
            status="ok" if result.ok else "needs_review",
            headline=headline,
            deck=deck,
            word_count=word_count,
            body_md=markdown,
        )

        try:
            conn.execute(
                "INSERT INTO briefs_fts(rowid, headline, deck, body) VALUES (?, ?, ?, ?)",
                (run_id, headline, deck, body_md),
            )
        except sqlite3.OperationalError as exc:
            log.warning("briefs_fts insert failed", extra={"err": str(exc)})


def _record_run(conn: sqlite3.Connection, *, n: int, n_clusters: int | None,
                output_path: str | None, coverage: float | None,
                prompt_version: str | None, model: str | None, status: str,
                headline: str = "", deck: str = "", word_count: int = 0,
                body_md: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO synthesis_runs "
        "(ran_at, article_count, cluster_count, output_path, citation_coverage, "
        " prompt_version, model, status, headline, deck, word_count, body_md) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), n, n_clusters, output_path,
         coverage, prompt_version, model, status, headline, deck, word_count, body_md),
    )
    return cur.lastrowid
