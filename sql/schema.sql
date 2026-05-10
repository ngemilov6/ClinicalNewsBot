-- Clinical Trial News Aggregator schema
-- Idempotent: every CREATE uses IF NOT EXISTS. Additive columns are added by
-- the migration helper in db.py since SQLite has no `ADD COLUMN IF NOT EXISTS`.

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  rss_url TEXT,
  api_url TEXT,
  source_type TEXT NOT NULL,
    -- newspaper | registry | journal | regulator | retraction
  paywalled INTEGER NOT NULL DEFAULT 0,
  extraction_strategy TEXT NOT NULL,
    -- jina_reader | rss_snippet_only | trafilatura | api_native
  bypass_filtering INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL REFERENCES sources(id),
  external_id TEXT NOT NULL,
  url TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT,
  body TEXT,
  published_at TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  status TEXT NOT NULL,
    -- candidate | ingested | rejected | needs_review | synthesized | error
  relevance_confidence REAL,
  relevance_reason TEXT,
  extraction_quality TEXT,
    -- full | snippet_only | failed
  embedding BLOB,
  cluster_id TEXT,
  prompt_version TEXT,
  UNIQUE(source_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_articles_status_published ON articles(status, published_at);
CREATE INDEX IF NOT EXISTS idx_articles_cluster ON articles(cluster_id);

CREATE TABLE IF NOT EXISTS ingest_errors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT,
  url TEXT,
  stage TEXT,
    -- fetch | extract | filter | persist
  error_message TEXT,
  occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ingest_errors_when ON ingest_errors(occurred_at);

CREATE TABLE IF NOT EXISTS synthesis_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ran_at TEXT NOT NULL,
  article_count INTEGER NOT NULL,
  cluster_count INTEGER,
  output_path TEXT,
  citation_coverage REAL,
  prompt_version TEXT,
  model TEXT,
  status TEXT NOT NULL
    -- ok | needs_review | failed
);

CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  called_at TEXT NOT NULL,
  purpose TEXT NOT NULL,
    -- relevance | embedding | cluster_summary | meta_synthesis | polish
  model TEXT NOT NULL,
  prompt_version TEXT,
  tokens_in INTEGER,
  tokens_out INTEGER,
  latency_ms INTEGER,
  error TEXT
);

-- ---------------------------------------------------------------------------
-- FTS5 full-text search over article bodies and brief contents.
-- Triggers keep the article FTS in sync. Brief FTS is populated by the
-- synthesis pipeline at run time.
-- ---------------------------------------------------------------------------

CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
  title, summary, body,
  content='articles', content_rowid='id', tokenize='porter'
);

CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
  INSERT INTO articles_fts(rowid, title, summary, body)
  VALUES (new.id, new.title, new.summary, new.body);
END;

CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
  INSERT INTO articles_fts(articles_fts, rowid, title, summary, body)
  VALUES ('delete', old.id, old.title, old.summary, old.body);
END;

CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
  INSERT INTO articles_fts(articles_fts, rowid, title, summary, body)
  VALUES ('delete', old.id, old.title, old.summary, old.body);
  INSERT INTO articles_fts(rowid, title, summary, body)
  VALUES (new.id, new.title, new.summary, new.body);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS briefs_fts USING fts5(
  headline, deck, body, tokenize='porter'
);
