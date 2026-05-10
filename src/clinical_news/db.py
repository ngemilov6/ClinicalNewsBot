"""Database layer.

Three modes, picked by env:

- ``TURSO_DATABASE_URL`` + ``TURSO_EMBEDDED_PATH`` set → **embedded replica**:
  libsql opens a local SQLite file (path from ``TURSO_EMBEDDED_PATH``) that
  syncs to Turso. Reads + writes are local; ``sync()`` pushes/pulls. This is
  what the GitHub Actions worker uses: long-running pipeline writes against
  the local file, then a single sync at exit ships everything to Turso. No
  Hrana stream timeouts because nothing streams.
- ``TURSO_DATABASE_URL`` alone → **HTTP mode**: each call hits Turso directly.
  Used by the Vercel reader: short-lived single-shot queries that never
  outlive a stream.
- neither set → **local SQLite** at ``Settings.db_path``. Used for local dev.

All three speak SQLite SQL — same schema, same queries, same FTS5.

The libsql client doesn't support ``Connection.row_factory``, so we wrap any
cursor returned to the caller with one that yields ``Row`` objects supporting
``row["col"]`` and ``row[idx]`` access (matching sqlite3.Row).
"""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "sql" / "schema.sql"

ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("articles", "user_label", "TEXT"),
    ("articles", "user_labeled_at", "TEXT"),
    ("synthesis_runs", "headline", "TEXT"),
    ("synthesis_runs", "deck", "TEXT"),
    ("synthesis_runs", "word_count", "INTEGER"),
    ("synthesis_runs", "body_md", "TEXT"),         # rendered markdown with [^N] footnotes
    ("synthesis_runs", "body_md_raw", "TEXT"),     # LLM output with [ref:X] markers
    ("synthesis_runs", "article_index_json", "TEXT"),  # JSON {ID: {source_id, title, url, published_at}}
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


def _is_stream_expired(exc: BaseException) -> bool:
    """Detect Turso/libsql Hrana 'stream not found' errors that mean the
    connection's stream lease lapsed and we just need to reconnect."""
    msg = str(exc)
    return "stream not found" in msg or "stream expired" in msg


class _ConnectionWrapper:
    """Thin wrapper that yields ``Row``-style cursors and transparently
    reconnects when the underlying libsql stream expires.

    When ``sync_on_exit`` is set (embedded-replica mode), ``__exit__`` calls
    ``conn.sync()`` to push local changes to Turso before closing.
    """

    def __init__(self, factory: Callable[[], Any], sync_on_exit: bool = False) -> None:
        self._factory = factory
        self._sync_on_exit = sync_on_exit
        self._conn = factory()

    def _reopen(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
        log.info("db: reopening connection (stream expired)")
        self._conn = self._factory()

    def _retry_on_stream(self, fn):
        try:
            return fn()
        except (ValueError, RuntimeError) as exc:
            if not _is_stream_expired(exc):
                raise
            self._reopen()
            return fn()

    def cursor(self):
        # Cursors returned to callers don't auto-recover (callers hold them
        # past method boundaries); use ``execute`` for resilient one-shots.
        return _CursorWrapper(self._conn.cursor())

    def execute(self, sql, params=()):
        return self._retry_on_stream(
            lambda: _CursorWrapper(self._conn.cursor().execute(sql, params))
        )

    def executescript(self, script: str):
        return self._retry_on_stream(lambda: self._conn.executescript(script))

    def commit(self):
        return self._retry_on_stream(lambda: self._conn.commit())

    def rollback(self):
        try:
            return self._conn.rollback()
        except Exception:
            return None

    def close(self):
        return self._conn.close()

    def sync(self) -> None:
        """Embedded-replica only: push local changes to Turso."""
        if hasattr(self._conn, "sync"):
            try:
                self._conn.sync()
                log.info("turso: synced changes to remote")
            except Exception as exc:
                log.warning("turso sync failed", extra={"err": str(exc)})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            try:
                self.commit()
            except Exception:
                pass
            if self._sync_on_exit:
                self.sync()
        else:
            self.rollback()
        self.close()
        return False


# --------------------------------------------------------------------------
# Connect
# --------------------------------------------------------------------------

def _is_turso() -> bool:
    return bool(os.environ.get("TURSO_DATABASE_URL"))


def connect(db_path: str | Path | None = None):
    """Return a connection-like object whose cursors yield Row objects.

    Mode picked by environment (see module docstring).
    """
    if _is_turso():
        import libsql_experimental as libsql
        url = os.environ["TURSO_DATABASE_URL"]
        token = os.environ.get("TURSO_AUTH_TOKEN", "")
        embedded = os.environ.get("TURSO_EMBEDDED_PATH", "").strip()

        if embedded:
            log.info("db mode: turso embedded-replica", extra={"path": embedded})
            # Embedded-replica: local file synced to Turso. Used by the worker.
            embedded_path = Path(embedded)
            embedded_path.parent.mkdir(parents=True, exist_ok=True)

            def _make_embedded():
                c = libsql.connect(str(embedded_path), sync_url=url, auth_token=token)
                # Pull latest state from Turso so we operate on a current copy.
                try:
                    c.sync()
                    log.info("turso: pulled latest from remote",
                             extra={"path": str(embedded_path)})
                except Exception as exc:
                    log.warning("initial turso sync failed", extra={"err": str(exc)})
                return c

            return _ConnectionWrapper(_make_embedded, sync_on_exit=True)

        log.info("db mode: turso HTTP (no TURSO_EMBEDDED_PATH set)")
        # Pure HTTP mode: short-lived reads from the Vercel reader.
        return _ConnectionWrapper(lambda: libsql.connect(url, auth_token=token))

    # local sqlite
    assert db_path is not None, "db_path required when TURSO_DATABASE_URL unset"
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def _make():
        c = sqlite3.connect(path, isolation_level=None, detect_types=sqlite3.PARSE_DECLTYPES)
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("PRAGMA journal_mode = WAL")
        return c

    return _ConnectionWrapper(_make)


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
