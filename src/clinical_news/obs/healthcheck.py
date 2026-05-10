"""Healthcheck queries — read-only."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any


def report(conn: sqlite3.Connection) -> dict[str, Any]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    active = {row["id"]: row["name"] for row in conn.execute(
        "SELECT id, name FROM sources WHERE active = 1"
    )}

    seen = {row["source_id"] for row in conn.execute(
        "SELECT DISTINCT source_id FROM articles WHERE fetched_at >= ?", (cutoff,)
    )}
    zero_ingest = sorted(set(active) - seen)

    err_rows = conn.execute(
        "SELECT source_id, COUNT(*) AS n FROM ingest_errors "
        "WHERE occurred_at >= ? GROUP BY source_id",
        (cutoff,),
    ).fetchall()
    error_counts = {row["source_id"]: row["n"] for row in err_rows}

    last_synth = conn.execute(
        "SELECT ran_at, status, citation_coverage FROM synthesis_runs "
        "ORDER BY ran_at DESC LIMIT 1"
    ).fetchone()

    return {
        "active_sources": len(active),
        "zero_ingest_sources_7d": zero_ingest,
        "ingest_errors_7d": error_counts,
        "last_synthesis": dict(last_synth) if last_synth else None,
    }
