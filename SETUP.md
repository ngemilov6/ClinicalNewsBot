# Setup & Operating Guide

Step-by-step instructions for getting Clinical News Bot running on a Linux or macOS machine, and what to do once it is running.

---

## 1. Prerequisites

- **Python 3.11+** (`python3 --version`)
- **An always-on machine** to host cron (home server, Raspberry Pi 4+, old laptop, or a free Oracle Cloud VM)
- **A Gemini API key** (free tier is sufficient) — required for embeddings, relevance classification, and synthesis
- *Optional:* a Gmail account with an app password — for emailed delivery of the weekly brief
- *Optional:* an Anthropic API key — for the Claude polish step

---

## 2. First-time setup

```bash
cd /path/to/ClinicalNewsBot

python3 -m venv .venv
.venv/bin/pip install -e .

cp .env.example .env
$EDITOR .env       # fill in at least GEMINI_API_KEY

.venv/bin/clinical-news migrate
```

`migrate` creates `app_data/articles.db` with the full schema. It is idempotent — safe to run again any time.

### Get a Gemini API key (one or many)

1. Visit <https://aistudio.google.com/apikey>
2. Click **Create API key** → copy the value
3. Paste it into `.env`. Two forms supported:
   - Single key: `GEMINI_API_KEY=AIza...`
   - **Multiple keys** (recommended for unattended operation):
     `GEMINI_API_KEYS=AIza1,AIza2,AIza3`

When more than one key is configured, the pipeline rotates between them automatically. If a key returns 429, it goes into cooldown (using the `retry_delay` from the error, or until the next PST midnight if it's a daily-cap error). State is persisted to `app_data/keypool_state.json` so cooldowns survive restarts and cron runs.

The free tier covers thousands of embeddings and hundreds of generation calls per day per key — multiple keys multiply that headroom.

### Get a Gmail app password (optional)

Only needed if you want the weekly brief emailed automatically.

1. Enable 2-Step Verification on your Google account
2. Visit <https://myaccount.google.com/apppasswords>
3. Generate a password labelled "Clinical News Bot"
4. Fill in `.env`:
   ```
   GMAIL_FROM=you@gmail.com
   GMAIL_TO=you@gmail.com
   GMAIL_APP_PASSWORD=<the 16-char password>
   ```

### Anthropic API key (optional)

Adds a Claude polish step at the end of synthesis. Without it the Gemini draft is delivered as-is, which is fine for an internal brief.

```
CLAUDE_API_KEY=sk-ant-...
```

---

## 3. Tune the source registry

`sources.yaml` ships with a working default: 5 structured authoritative feeds (ClinicalTrials.gov, PubMed, FDA, EMA, RetractionWatch) plus 15 newspaper RSS feeds. Edit to taste:

- Set `active: false` to disable a source without deleting it (kill switch)
- For a paywalled paper, leave `extraction_strategy: rss_snippet_only` so the pipeline doesn't waste time fetching login walls
- Add new RSS sources by appending an entry; the pipeline picks them up on the next run

Newspapers go through the full `keyword → embedding → LLM` filter chain. Structured sources have `bypass_filtering: true` because every entry is by definition relevant.

---

## 4. First ingest

```bash
.venv/bin/clinical-news ingest
.venv/bin/clinical-news healthcheck
```

`ingest` walks every active source, dedupes against the DB, runs the filter chain, fetches full article text via Jina Reader (with a Trafilatura fallback), and persists rows into `articles.db`.

`healthcheck` prints, as JSON: how many sources are active, which sources had zero ingests in the last 7 days (likely broken feeds), error counts by source, and the last synthesis run.

Run `ingest` again immediately and confirm it finds zero new items — that proves idempotency.

---

## 5. Verify the relevance classifier

```bash
.venv/bin/clinical-news eval relevance
```

This runs the live filter chain against `eval/relevance.jsonl` (102 hand-labelled examples) and prints precision, recall, F1, and a list of misclassifications.

**Targets:** precision ≥ 0.80, recall ≥ 0.90.

If you fall short, tune the thresholds in `src/clinical_news/pipeline.py`:

| Constant | Default | Effect |
|---|---|---|
| `EMBEDDING_LOWER` | 0.45 | Below: drop without LLM call |
| `EMBEDDING_UPPER` | 0.65 | Above: accept without LLM call |
| `LLM_ACCEPT` | 0.6 | LLM confidence ≥ this → accept |
| `LLM_REVIEW` | 0.4 | LLM confidence in [LOW, ACCEPT) → needs_review |

Lowering `EMBEDDING_LOWER` and `LLM_ACCEPT` raises recall at the cost of precision; raising them does the opposite. Re-run `eval relevance` after each change.

---

## 6. First synthesis

You need at least three days of accumulated articles for synthesis to be useful. Once you do:

```bash
.venv/bin/clinical-news synthesize --dry-run
```

`--dry-run` writes the brief to disk but skips the email send. The output path is logged; it lives at `app_data/synthesis/brief_<UTC-timestamp>.md`.

What this does, step by step:

1. Pull every article with `status='ingested'` published in the last 7 days
2. Bail out (and log "skipped_insufficient") if fewer than 3 articles
3. Cluster them by embedding cosine similarity (agglomerative, distance threshold 0.30)
4. Per cluster: one Gemini call → `{theme, key_facts, disagreements}` with citations restricted to that cluster's article IDs
5. One meta-synthesis call: weave cluster summaries into an 800–1,200 word brief
6. *(Optional)* Claude polish call if `CLAUDE_API_KEY` is set
7. Validate every `[ref:X]` resolves to an input article; check coverage ≥ 80%; reject quotes longer than 15 words
8. Render Markdown with footnote citations and a Sources appendix
9. Mark articles `status='synthesized'`; record the run metadata

If validation fails, the run is logged with `status='needs_review'` rather than `ok` — the file is still written so you can inspect what went wrong.

When you're happy with the output, drop `--dry-run` and run again to send the email.

---

## 7. Where things are stored

| What | Where | Notes |
|---|---|---|
| Source articles (references) | `app_data/articles.db` → `articles` table | full body, embedding, status, cluster_id |
| Freshly written briefs | `app_data/synthesis/brief_<timestamp>.md` | Markdown with numbered footnotes + Sources appendix |
| Synthesis run metadata | `app_data/articles.db` → `synthesis_runs` table | one row per run; `output_path` points at the brief file |
| Ingest errors | `app_data/articles.db` → `ingest_errors` table | per-source failures, queryable for debugging |
| DB backups | `backups/articles-YYYY-MM-DD.db` | nightly via `scripts/backup.sh`, 14-day retention |
| Logs | stderr (JSON) | redirect to a file via cron; nothing is written to disk by default |

The DB is the single source of truth. The Markdown briefs are derived artifacts — every claim in them traces back to an article row.

To inspect the DB directly:

```bash
sqlite3 app_data/articles.db
> SELECT status, COUNT(*) FROM articles GROUP BY status;
> SELECT title, url FROM articles WHERE status = 'ingested' ORDER BY published_at DESC LIMIT 10;
> SELECT ran_at, article_count, citation_coverage, output_path FROM synthesis_runs ORDER BY ran_at DESC;
```

---

## 8. Schedule it

Edit your crontab (`crontab -e`) and add:

```cron
# daily ingest at 06:00 local time
0 6  * * *   cd /path/to/ClinicalNewsBot && .venv/bin/clinical-news ingest >> /var/log/clinical-news/ingest.log 2>&1

# weekly synthesis Sunday 18:00
0 18 * * 0   cd /path/to/ClinicalNewsBot && .venv/bin/clinical-news synthesize >> /var/log/clinical-news/synth.log 2>&1

# nightly DB backup
0 2  * * *   /path/to/ClinicalNewsBot/scripts/backup.sh >> /var/log/clinical-news/backup.log 2>&1
```

Make sure `/var/log/clinical-news/` exists and is writable, or pick a different log destination.

Verify the cron is wired:

```bash
crontab -l
sudo grep CRON /var/log/syslog | tail   # on systems with rsyslog
```

---

## 9. Common operations

### Add a new source

Append to `sources.yaml`:

```yaml
- id: my-new-feed
  name: My New Feed
  source_type: newspaper           # or registry | journal | regulator | retraction
  rss_url: https://example.com/feed.xml
  paywalled: false
  extraction_strategy: jina_reader  # or rss_snippet_only | trafilatura | api_native
  active: true
```

Next ingest run picks it up automatically. To disable, flip `active: false`.

### Re-run a synthesis manually

```bash
.venv/bin/clinical-news synthesize --dry-run
```

This re-clusters and re-synthesizes whatever articles currently have `status='ingested'`. To replay a previous week, you'd need to flip the relevant rows back from `synthesized` to `ingested` first.

### Update prompts

Never edit a `_v1.md` file in place after you've already run synthesis. Bump the version:

1. Copy `prompts/meta_synthesis_v1.md` to `prompts/meta_synthesis_v2.md`
2. Edit the v2 file
3. Update `PROMPT_PATH` and `PROMPT_VERSION` in the corresponding module (e.g. `src/clinical_news/synthesize/meta.py`)
4. Re-run `eval relevance` (and any synthesis golds you've added) to confirm no regression

`synthesis_runs.prompt_version` records which version produced each brief.

### Inspect a brief that failed validation

```bash
sqlite3 app_data/articles.db \
  "SELECT ran_at, status, output_path FROM synthesis_runs WHERE status = 'needs_review';"
cat app_data/synthesis/brief_<the-timestamp>.md
```

Validation failures are logged with which `[ref:X]` markers were unresolved or which paragraphs lacked citations.

### Restore from backup

```bash
cp backups/articles-2026-05-09.db app_data/articles.db
```

The DB is plain SQLite — no migration needed.

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `GEMINI_API_KEY not set` | `.env` missing or unloaded | Confirm `.env` exists in repo root and has the key |
| Source has zero ingests for 7+ days | Feed URL changed or returning HTML | `curl <rss_url>` to inspect; update `sources.yaml` |
| All Jina fetches time out | Rate-limited or service down | Trafilatura fallback kicks in automatically; check `ingest_errors` |
| Synthesis says "skipped_insufficient" | <3 articles in last 7 days | Check `articles WHERE status='ingested'`; loosen filter thresholds |
| Citation coverage < 80% | LLM dropped citations | Inspect the brief; consider rerunning, then bump prompt to v2 |
| Email not sent | Gmail app password wrong or 2FA off | Regenerate at <https://myaccount.google.com/apppasswords> |
| `pytest` fails | Dev deps missing | `.venv/bin/pip install -e ".[dev]"` |

For deeper debugging, set `LOG_LEVEL=DEBUG` in `.env` — the JSON logs include the source ID, URL, and stage for every operation.

---

## 11. Reader UI (web app)

The pipeline ships with a FastAPI reader UI that surfaces past briefs, lets readers download PDF/Markdown, search across all source articles, and (for the admin) mark articles as relevant or not relevant.

### Run it locally

```bash
.venv/bin/clinical-news web --host 127.0.0.1 --port 8000
```

Then open <http://127.0.0.1:8000/>. The home page redirects to the latest brief. `/library` lists all past briefs. `/search?q=...` does full-text search over briefs and source articles via SQLite FTS5.

### Admin password

`/admin/*` routes (relevance feedback, manual synthesis trigger) are protected by HTTP Basic. Set:

```
ADMIN_PASSWORD=pick-a-strong-password
```

Username is always `admin`. A blank `ADMIN_PASSWORD` returns 503 from those routes — they're disabled until set.

### Routes

| URL | What |
|---|---|
| `/` | Redirects to the latest brief |
| `/library` | Paginated list of past briefs |
| `/briefs/{id}` | Rendered brief with footnote citations + Sources |
| `/briefs/{id}.md` | Markdown download |
| `/briefs/{id}.pdf` | PDF download (cached after first hit) |
| `/search?q=...` | FTS5 search over briefs + source articles |
| `POST /admin/feedback/{article_id}` | Mark `relevant` / `not_relevant` (form: `label=...`) |
| `POST /admin/synthesize` | Trigger an out-of-band synthesis run |

### Using the user labels

Articles you label thumbs-up/down get an extra eval signal:

```bash
.venv/bin/clinical-news eval relevance --include-user-labels
```

This joins your DB labels into the `eval/relevance.jsonl` set and reports combined precision/recall.

---

## 12. Public hosting (Cloudflare Tunnel)

The simplest way to expose the UI publicly without opening ports or running a reverse proxy:

### One-time setup

```bash
# 1. Install cloudflared (Linux)
sudo curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  -o /usr/share/keyrings/cloudflare-main.gpg
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main' | \
  sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install cloudflared

# 2. Login (opens a browser to authorize a Cloudflare zone you own)
cloudflared tunnel login

# 3. Create a tunnel and route a hostname
cloudflared tunnel create clinical-news
cloudflared tunnel route dns clinical-news clinical-news.example.com

# 4. Copy the example config and edit the tunnel ID + username
cp infra/cloudflared.example.yml ~/.cloudflared/config.yml
$EDITOR ~/.cloudflared/config.yml
```

### Run it

```bash
# Foreground (for testing)
cloudflared tunnel run clinical-news

# Or as a system service:
sudo cloudflared service install
```

Once both `cloudflared` and `clinical-news web` are running, your reader UI is reachable at `https://clinical-news.example.com` over Cloudflare's free HTTPS.

### Run the web app as a system service

`infra/clinical-news-web.service` is a starting systemd unit. Edit the username/path placeholders and:

```bash
sudo cp infra/clinical-news-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clinical-news-web
```

Now both ingest (cron) and the UI (systemd) survive reboots.
