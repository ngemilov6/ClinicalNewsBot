# Clinical News Bot

Automated pipeline that monitors clinical-trial coverage across 15 newspapers plus structured authoritative sources (ClinicalTrials.gov, PubMed, FDA, EMA, RetractionWatch) and synthesizes a weekly internal brief with citations.

See `clinical_trial_aggregator_design.md` for the original design and `/home/nikola-emilov/.claude/plans/elegant-crafting-lobster.md` for the implementation plan.

## Quickstart

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env  # fill in GEMINI_API_KEY etc.
.venv/bin/clinical-news migrate
.venv/bin/clinical-news ingest
.venv/bin/clinical-news healthcheck
.venv/bin/clinical-news synthesize --dry-run   # writes Markdown to app_data/synthesis/
```

## Commands

| Command | What it does |
|---|---|
| `clinical-news migrate` | Apply DB migrations (idempotent) |
| `clinical-news ingest` | Fetch → filter → extract → persist |
| `clinical-news synthesize [--dry-run]` | Cluster last 7 days, produce + email brief |
| `clinical-news healthcheck` | Print zero-ingest sources, error counts, last synthesis |
| `clinical-news eval relevance` | Run filter chain against `eval/relevance.jsonl` |

## Cron

```cron
# daily ingest
0 6  * * *   /path/to/.venv/bin/clinical-news ingest >> /var/log/clinical-news/ingest.log 2>&1

# weekly synthesis (Sunday 18:00)
0 18 * * 0   /path/to/.venv/bin/clinical-news synthesize >> /var/log/clinical-news/synth.log 2>&1

# nightly DB backup, retain 14 days
0 2  * * *   /path/to/scripts/backup.sh
```

`scripts/backup.sh` is at the repo root; copy it where convenient.

## Adding a source

Edit `sources.yaml`. Required keys: `id`, `name`, `source_type`, plus either `rss_url` or `api_url`. Set `bypass_filtering: true` only for authoritative structured sources where every item is by definition relevant.

## Tuning thresholds

- `EMBEDDING_LOWER` / `EMBEDDING_UPPER` / `LLM_ACCEPT` live in `src/clinical_news/pipeline.py`.
- After tuning, run `clinical-news eval relevance` to confirm precision ≥0.80, recall ≥0.90.

## Versioning prompts

Never edit a `_v1` prompt file in place after the first synthesis run. Bump to `_v2` and update `PROMPT_VERSION` constants in the corresponding modules. Each `synthesis_runs` row records the version used.

## Environment

```env
GEMINI_API_KEY=         # required for embeddings, relevance, synthesis
CLAUDE_API_KEY=         # optional polish step
GMAIL_FROM=             # sender for the weekly brief
GMAIL_TO=               # recipient
GMAIL_APP_PASSWORD=     # https://myaccount.google.com/apppasswords
DB_PATH=app_data/articles.db
LOG_LEVEL=INFO
```
