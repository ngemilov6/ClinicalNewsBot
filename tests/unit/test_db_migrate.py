import sqlite3
from pathlib import Path

from clinical_news import db


def test_migrate_creates_expected_tables(tmp_path: Path):
    p = tmp_path / "test.db"
    db.migrate(p)
    with db.connect(p) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {"sources", "articles", "ingest_errors", "synthesis_runs", "llm_calls"} <= names


def test_migrate_is_idempotent(tmp_path: Path):
    p = tmp_path / "test.db"
    db.migrate(p)
    db.migrate(p)  # second call must not raise
    with db.connect(p) as conn:
        # Insert one row, ensure dedup constraint exists
        conn.execute(
            "INSERT INTO sources (id, name, source_type, extraction_strategy) "
            "VALUES (?, ?, ?, ?)", ("test", "Test", "newspaper", "jina_reader"))
        conn.execute(
            "INSERT INTO articles (source_id, external_id, url, title, "
            "published_at, fetched_at, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test", "ext1", "u", "t", "2026-01-01", "2026-01-01", "ingested"),
        )
        try:
            conn.execute(
                "INSERT INTO articles (source_id, external_id, url, title, "
                "published_at, fetched_at, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("test", "ext1", "u", "t", "2026-01-01", "2026-01-01", "ingested"),
            )
            raised = False
        except sqlite3.IntegrityError:
            raised = True
        assert raised, "expected UNIQUE(source_id, external_id) violation"
