"""Database layer.

Two backends, picked by env:

- ``TURSO_DATABASE_URL`` set → libsql client (Turso / remote SQLite). Used for
  Vercel + GitHub Actions deployments.
- otherwise → local SQLite file at ``Settings.db_path``. Used for local dev
  and Cloudflare-Tunnel-from-laptop deployments.

Both speak SQLite SQL — same schema, same queries, same FTS5.

The libsql client doesn't support ``Connection.row_factory``, so we wrap any
cursor returned to the caller with one that yields ``Row`` objects supporting
``row["col"]`` and ``row[idx]`` access (matching sqlite3.Row).
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "sql" / "schema.sql"

ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("articles", "user_label", "TEXT"),
    ("articles", "user_labeled_at", "TEXT"),
    ("synthesis_runs", "headline", "TEXT"),
    ("synthesis_runs", "deck", "TEXT"),
    ("synthesis_runs", "word_count", "INTEGER"),
    ("synthesis_runs", "body_md", "TEXT"),  # V2: brief stored in DB, not on disk
]


# --------------------------------------------------------------------------
# Row + cursor shim (libsql doesn't support row_factory)
# --------------------------------------------------------------------------

class Row:
    """Sqlite3.Row-compatible wrapper. Supports row["col"] and row[i]."""

    __slots__ = ("_cols", "_values")

    def __init__(self, cols: Sequence[str], values: Sequence[Any]) -> None:
        self._cols = cols
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        try:
            return self._values[self._cols.index(key)]
        except ValueError as exc:
            raise KeyError(key) from exc

    def keys(self) -> Sequence[str]:
        return self._cols

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"Row({dict(zip(self._cols, self._values))})"


class _CursorWrapper:
    def __init__(self, cur) -> None:
        self._cur = cur

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    def execute(self, sql, params=()):
        self._cur.execute(sql, params)
        return self

    def executemany(self, sql, seq):
        self._cur.executemany(sql, seq)
        return self

    def _row(self, raw):
        if raw is None:
            return None
        cols = [d[0] for d in self._cur.description] if self._cur.description else []
        return Row(cols, list(raw))

    def fetchone(self):
        return self._row(self._cur.fetchone())

    def fetchall(self):
        cols = [d[0] for d in self._cur.description] if self._cur.description else []
        return [Row(cols, list(r)) for r in self._cur.fetchall()]

    def __iter__(self):
        return self

    def __next__(self):
        raw = self._cur.fetchone()
        if raw is None:
            raise StopIteration
        return self._row(raw)


class _ConnectionWrapper:
    """Thin wrapper that returns _CursorWrapper-yielding rows for both backends."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def cursor(self):
        return _CursorWrapper(self._conn.cursor())

    def execute(self, sql, params=()):
        return _CursorWrapper(self._conn.cursor().execute(sql, params))

    def executescript(self, script: str):
        # libsql_experimental Connection has executescript; sqlite3 too
        return self._conn.executescript(script)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            try:
                self._conn.commit()
            except Exception:
                pass
        else:
            try:
                self._conn.rollback()
            except Exception:
                pass
        self.close()
        return False


# --------------------------------------------------------------------------
# Connect
# --------------------------------------------------------------------------

def _is_turso() -> bool:
    return bool(os.environ.get("TURSO_DATABASE_URL"))


def connect(db_path: str | Path | None = None):
    """Return a connection-like object whose cursors yield Row objects.

    When ``TURSO_DATABASE_URL`` is set, it's used regardless of ``db_path``.
    """
    if _is_turso():
        import libsql_experimental as libsql
        url = os.environ["TURSO_DATABASE_URL"]
        token = os.environ.get("TURSO_AUTH_TOKEN", "")
        # libsql expects 'libsql://...' for native protocol or '...turso.io' http
        conn = libsql.connect(url, auth_token=token)
        return _ConnectionWrapper(conn)

    # local sqlite
    assert db_path is not None, "db_path required when TURSO_DATABASE_URL unset"
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return _ConnectionWrapper(conn)


# --------------------------------------------------------------------------
# Schema management
# --------------------------------------------------------------------------

def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    if not _table_exists(conn, table):
        return
    if _column_exists(conn, table, column):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _backfill_fts(conn) -> None:
    if not _table_exists(conn, "articles_fts"):
        return
    total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    if total == 0:
        return
    sample_match = conn.execute(
        "SELECT COUNT(*) FROM articles_fts WHERE articles_fts MATCH 'the OR a OR of'"
    ).fetchone()[0]
    if sample_match == 0:
        conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")


def migrate(db_path: str | Path | None = None) -> None:
    schema_sql = SCHEMA_PATH.read_text()
    with connect(db_path) as conn:
        conn.executescript(schema_sql)
        for table, column, ddl in ADDITIVE_COLUMNS:
            _add_column_if_missing(conn, table, column, ddl)
        _backfill_fts(conn)


@contextmanager
def transaction(conn) -> Iterator:
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
