# Clinical News Bot

Automated pipeline that monitors clinical-trial coverage across 22 sources — newspapers, trade publications, ClinicalTrials.gov, PubMed, FDA, EMA, RetractionWatch — clusters related coverage, and synthesizes a ~10-minute-read weekly brief with cited references. Ships with a FastAPI reader UI for browsing the library, full-text search, and a one-click "Generate new brief" button.

## What it does

```
22 sources (RSS / API)            ← ingest
   ↓ keyword → embedding → LLM relevance filter
   ↓ Jina Reader / trafilatura extraction
   ↓
SQLite (or Turso) + FTS5
   ↓
Embedding-based clustering        ← synthesize (weekly)
   ↓ per-cluster summarization (Gemini)
   ↓ meta-synthesis → 2,400-3,000 word brief
   ↓ optional Claude polish
   ↓ citation validation
   ↓
Reader UI (FastAPI + Jinja + HTMX)
   • /library  — paginated brief history
   • /briefs/{id}  — rendered brief with footnote citations
   • /search?q=…  — FTS5 across briefs and source articles
   • /admin/generate  — fires GitHub Actions to run a fresh pipeline
```

## Two ways to run it

| | Local / Cloudflare Tunnel | Vercel + Turso + GitHub Actions |
|---|---|---|
| Cost | $0 | $0 |
| DB | SQLite file | Turso (libsql, SQLite-compatible) |
| Pipeline run | Button → in-process background task | Button → GitHub Actions runner |
| Web | `clinical-news web` on your machine | Serverless functions |
| Setup time | 5 min | ~15 min |
| Always-on | Only when your machine is on | Yes |
| See | [`SETUP.md`](SETUP.md) §4 | [`SETUP.md`](SETUP.md) §5 |

## Local quickstart

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env       # fill in GEMINI_API_KEY (or GEMINI_API_KEYS for rotation)
.venv/bin/clinical-news migrate
.venv/bin/clinical-news ingest
.venv/bin/clinical-news synthesize --dry-run
.venv/bin/clinical-news web      # http://127.0.0.1:8000
```

Public hosting via Cloudflare Tunnel: see [`SETUP.md`](SETUP.md) §12.

## Vercel quickstart

Detailed step-by-step in [`SETUP.md`](SETUP.md) §5. Outline:

1. `turso db create clinical-news` → grab URL + token
2. `gh repo create ClinicalNewsBot --public --source . --push`
3. Add Actions secrets (`TURSO_*`, `GEMINI_API_KEYS`, …)
4. Mint a fine-grained PAT with `Actions: Write`
5. Import the repo on Vercel; set `TURSO_*`, `ADMIN_PASSWORD`, `GH_REPO`, `GH_DISPATCH_TOKEN`
6. Open `/library` → click **Generate new brief**

## CLI reference

| Command | What it does |
|---|---|
| `clinical-news migrate` | Apply DB schema (idempotent) |
| `clinical-news ingest` | Pull all active sources → filter → extract → persist |
| `clinical-news synthesize [--dry-run]` | Cluster last 7 days, produce a brief, email it |
| `clinical-news web [--host --port]` | Run the reader UI |
| `clinical-news healthcheck` | JSON: zero-ingest sources, error counts, last synthesis |
| `clinical-news eval relevance [--include-user-labels]` | Run filter chain against `eval/relevance.jsonl` |

## Architecture highlights

- **Multi-key Gemini rotation** — set `GEMINI_API_KEYS=k1,k2,k3` and the pipeline rotates on 429, persisting cooldowns to disk.
- **Hybrid filter chain** — keyword (drops ~85%) → anchor-embedding similarity (drops most of the rest) → LLM only on borderline items. Keeps API spend near zero.
- **Embedding-based clustering** — agglomerative clustering on stored embeddings catches "same study, different framing" across sources.
- **Two-pass synthesis** — per-cluster summarization keeps citation validation tractable; the meta call weaves clusters into a single brief.
- **Citation discipline** — every `[ref:X]` is validated against the input source set; coverage <80% flags `needs_review`; quotes >15 words are rejected.
- **FTS5 search** — full-text across both brief bodies and source article bodies.
- **User feedback** — `👍/👎` on search results writes `user_label`; `eval relevance --include-user-labels` joins those into the eval set.

## Key configuration

```env
# Gemini — single key or comma-separated list (rotation kicks in for lists)
GEMINI_API_KEYS=AIza1,AIza2,AIza3

# Optional Claude polish step (Anthropic API)
CLAUDE_API_KEY=

# Email delivery (optional)
GMAIL_FROM=
GMAIL_TO=
GMAIL_APP_PASSWORD=

# Web UI admin password (HTTP Basic) — gates the Generate button
ADMIN_PASSWORD=

# Vercel deploys also need:
TURSO_DATABASE_URL=libsql://…
TURSO_AUTH_TOKEN=
GH_REPO=user/repo
GH_DISPATCH_TOKEN=github_pat_…

# Tuning knobs (rarely changed)
GEMINI_MIN_INTERVAL_S=5.0
GEMINI_GEN_MODEL=gemini-flash-latest
GEMINI_MAX_OUTPUT_TOKENS=8000
LOG_LEVEL=INFO
```

## Adding a source

Edit `sources.yaml`. Required keys: `id`, `name`, `source_type` (`newspaper` / `registry` / `journal` / `regulator` / `retraction`), plus either `rss_url` or `api_url`. Set `bypass_filtering: true` only for authoritative structured sources where every item is by definition relevant. Next ingest run picks it up.

## Tuning the relevance classifier

Thresholds live in `src/clinical_news/pipeline.py`:

| Constant | Default | Effect |
|---|---|---|
| `EMBEDDING_LOWER` | 0.45 | Below: drop without LLM call |
| `EMBEDDING_UPPER` | 0.65 | Above: accept without LLM call |
| `LLM_ACCEPT` | 0.7 | LLM confidence ≥ this → accept |
| `LLM_REVIEW` | 0.4 | LLM in [0.4, 0.7) → `needs_review` |
| `MAX_BRIEF_REFS` | 10 | Hard cap on distinct citations per brief |

Run `clinical-news eval relevance` after any change. Targets: precision ≥ 0.80, recall ≥ 0.90 against the 102-item gold set.

## Versioning prompts

Never edit a `_vN.md` prompt file in place after a synthesis run. Bump to `_vN+1.md` and update the `PROMPT_PATH` / `PROMPT_VERSION` constants. Every brief records the version used in `synthesis_runs.prompt_version`.

## Tests

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/unit -q
```

## Repo layout

```
.
├── api/index.py                  # Vercel serverless entry
├── vercel.json                   # Vercel routing
├── requirements.txt              # lean web-only deps for Vercel
├── pyproject.toml                # full deps (incl. pipeline)
├── sql/schema.sql                # SQLite/libsql schema (FTS5 + triggers)
├── prompts/                      # versioned LLM prompts
├── sources.yaml                  # source registry
├── eval/relevance.jsonl          # gold set (102 items)
├── .github/workflows/
│   └── run-pipeline.yml          # repository_dispatch worker
├── infra/
│   ├── cloudflared.example.yml   # Cloudflare Tunnel config
│   └── clinical-news-web.service # systemd unit for the web UI
├── src/clinical_news/
│   ├── cli.py                    # click entrypoint
│   ├── config.py                 # settings + sources.yaml loader
│   ├── db.py                     # sqlite3 + libsql backend
│   ├── pipeline.py               # ingest + synthesis orchestration
│   ├── sources/                  # one adapter per source type
│   ├── filter/                   # keyword + embedding + llm relevance
│   ├── extract/                  # jina + trafilatura + paywall heuristics
│   ├── synthesize/               # cluster + summarize + meta + validate
│   ├── llm/                      # gemini client + key pool + claude polish
│   ├── deliver/                  # gmail SMTP
│   ├── obs/                      # logging + healthcheck
│   └── web/                      # FastAPI app, Jinja templates, static
├── tests/unit/                   # pytest unit tests
├── README.md                     # ← you are here
├── SETUP.md                      # full deploy + operations guide (both paths)
└── clinical_trial_aggregator_design.md   # original design doc
```

## Stack

- **Python 3.11+**, FastAPI, Jinja2, HTMX, click, pydantic
- **SQLite** locally / **Turso** (libsql) for serverless
- **Gemini** (`gemini-flash-latest` + `gemini-embedding-001`)
- **Anthropic Claude** for the optional polish pass
- **Jina Reader** + **trafilatura** for article extraction
- **scikit-learn** for clustering
- **GitHub Actions** as the worker on Vercel deploys

## License

MIT — see `LICENSE` if present.
