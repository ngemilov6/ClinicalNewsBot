# Setup & Operating Guide

End-to-end instructions to run, deploy, and operate Clinical News Bot. The repo supports two deployment paths — pick one:

- **Path A — Local + Cloudflare Tunnel** (laptop or always-on home server). $0/mo.
- **Path B — Vercel + Turso + GitHub Actions** (proper cloud deploy, button-triggered pipeline). $0/mo.

Both paths share the same codebase, same DB schema, same source registry, same prompts. Only deployment plumbing differs.

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Get an API key (Gemini)](#2-get-an-api-key-gemini)
3. [Optional credentials (Gmail, Claude)](#3-optional-credentials-gmail-claude)
4. [Path A — Local + Cloudflare Tunnel](#4-path-a--local--cloudflare-tunnel)
5. [Path B — Vercel + Turso + GitHub Actions](#5-path-b--vercel--turso--github-actions)
6. [Source registry](#6-source-registry)
7. [Verify the relevance classifier](#7-verify-the-relevance-classifier)
8. [How a brief is generated](#8-how-a-brief-is-generated)
9. [Where things are stored](#9-where-things-are-stored)
10. [Reader UI reference](#10-reader-ui-reference)
11. [Common operations](#11-common-operations)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Prerequisites

- **Python 3.11+** (`python3 --version`)
- **git**
- **A Gemini API key** (free tier; multiple keys recommended). Required for embeddings, relevance classification, and synthesis.
- *Optional:* Gmail with an app password (emailed brief delivery)
- *Optional:* Anthropic API key (Claude polish step)

For Path A you'll also want an always-on machine (home server, Raspberry Pi 4+, old laptop, free Oracle Cloud VM, etc.).

For Path B you'll need a GitHub account (public repo gets unlimited free Actions minutes).

---

## 2. Get an API key (Gemini)

1. Visit <https://aistudio.google.com/apikey>
2. Click **Create API key** → copy the value
3. Save it for the deployment step. Two forms supported:
   - Single key: `GEMINI_API_KEY=AIza...`
   - **Multiple keys** (recommended): `GEMINI_API_KEYS=AIza1,AIza2,AIza3`

When more than one key is set, the pipeline rotates between them automatically. If a key returns 429, it goes into cooldown using `retry_delay` from the error (or until the next PST midnight if it's a daily-cap error). State persists to `app_data/keypool_state.json` so cooldowns survive across runs.

The free tier covers thousands of embeddings and hundreds of generation calls per day **per key** — multiple keys multiply that headroom.

---

## 3. Optional credentials (Gmail, Claude)

### Gmail app password (emailed brief)

1. Enable 2-Step Verification on your Google account
2. Visit <https://myaccount.google.com/apppasswords>
3. Generate a password labelled "Clinical News Bot"
4. Save:
   ```
   GMAIL_FROM=you@gmail.com
   GMAIL_TO=you@gmail.com
   GMAIL_APP_PASSWORD=<the 16-char password>
   ```

### Anthropic API key (Claude polish)

Adds a Claude polish call at the end of synthesis. Skip if you're happy with the Gemini draft.

```
CLAUDE_API_KEY=sk-ant-...
```

---

## 4. Path A — Local + Cloudflare Tunnel

Best for: a laptop or always-on home server. Real SQLite file on disk. The pipeline runs on demand from the **Generate new brief** button or the CLI — no scheduler, no cron. Cloudflare Tunnel exposes the UI publicly with free HTTPS.

### 4.1 Install

```bash
git clone https://github.com/YOUR-USER/ClinicalNewsBot.git
cd ClinicalNewsBot

python3 -m venv .venv
.venv/bin/pip install -e .

cp .env.example .env
$EDITOR .env       # at minimum: GEMINI_API_KEY (or GEMINI_API_KEYS), ADMIN_PASSWORD
```

### 4.2 Initialize

```bash
.venv/bin/clinical-news migrate
```

`migrate` creates `app_data/articles.db` with the full schema (idempotent — safe to re-run).

### 4.3 Start the reader UI

```bash
.venv/bin/clinical-news web --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/>. The library is empty until your first run.

### 4.4 Generate your first brief

Click **Generate new brief** on `/library`. Browser prompts for HTTP Basic credentials (`admin` / your `ADMIN_PASSWORD`).

Behind the scenes the full pipeline runs as a FastAPI background task **in the same process** as the UI:

1. Ingest — fetches every active source, dedupes against the DB, runs the filter chain, extracts article bodies via Jina Reader (with a trafilatura fallback), persists rows.
2. Synthesize — clusters the last 7 days of `status='ingested'` articles, summarizes each cluster, runs the meta-synthesis call, validates citations, writes the brief.

The page polls `/admin/status` every 20 s and auto-reloads when the new `synthesis_runs` row lands. Typical wall time: **5–10 min** depending on the Gemini throttle and how much new material is out there.

If validation fails (e.g. citation coverage <80%), the run is logged with `status='needs_review'` rather than `ok` — the brief still appears in the library so you can read it.

#### CLI alternative

If you'd rather run from the terminal (e.g. for debugging):

```bash
.venv/bin/clinical-news ingest
.venv/bin/clinical-news healthcheck
.venv/bin/clinical-news synthesize --dry-run    # writes to disk + DB but skips the email send
```

Same effect, easier to inspect logs as they stream.

### 4.5 Run the web UI as a systemd service

So the UI survives reboots. `infra/clinical-news-web.service` is a starter unit:

```bash
sudo cp infra/clinical-news-web.service /etc/systemd/system/
# edit User= / WorkingDirectory= / ExecStart= for your paths
sudo systemctl daemon-reload
sudo systemctl enable --now clinical-news-web
```

### 4.6 Public hosting via Cloudflare Tunnel

```bash
# 1. Install cloudflared (Linux apt)
sudo curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  -o /usr/share/keyrings/cloudflare-main.gpg
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main' | \
  sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install cloudflared

# 2. Login (browser opens to authorize a Cloudflare zone you own)
cloudflared tunnel login

# 3. Create the tunnel and route a hostname
cloudflared tunnel create clinical-news
cloudflared tunnel route dns clinical-news clinical-news.example.com

# 4. Copy the example config and fill in tunnel ID + username
cp infra/cloudflared.example.yml ~/.cloudflared/config.yml
$EDITOR ~/.cloudflared/config.yml

# 5. Run
cloudflared tunnel run clinical-news
# or as a system service:
sudo cloudflared service install
```

Your UI is now at `https://clinical-news.example.com` over Cloudflare's free HTTPS. Skip to [§6 Source registry](#6-source-registry).

---

## 5. Path B — Vercel + Turso + GitHub Actions

Best for: a real cloud deploy with no machine to keep on. The reader UI runs serverless on Vercel, the database lives on Turso (libsql, SQLite-compatible — same FTS5, same SQL), and GitHub Actions runs the heavy ingest+synthesis work when you click the **Generate new brief** button.

```
[ Vercel: Reader UI ]
        ↓ button click → POST /admin/generate
        ↓ HTTP → GitHub repository_dispatch
[ GitHub Actions: ingest + synthesize ]
        ↓ writes
[ Turso DB ]
        ↑ reads
[ Vercel: Reader UI ]
```

### 5.1 Provision Turso

```bash
# install the CLI: https://docs.turso.tech/cli/installation
curl -sSfL https://get.tur.so/install.sh | bash

turso auth signup
turso db create clinical-news

# capture both for later — you'll paste them into GitHub + Vercel
turso db show clinical-news --url     # libsql://clinical-news-USERNAME.turso.io
turso db tokens create clinical-news  # eyJh...
```

### 5.2 Push to GitHub

If you haven't already:

```bash
cd /path/to/ClinicalNewsBot
git init -b main
git add .
git status   # confirm no .env, no app_data/, no .venv/ are staged
git commit -m "Initial commit: Clinical News Bot"

# easiest with gh CLI:
gh repo create ClinicalNewsBot --public --source . --push

# or manual: create the repo at https://github.com/new (no README/.gitignore), then:
git remote add origin https://github.com/YOUR-USER/ClinicalNewsBot.git
git push -u origin main
```

Use **public** unless you have a reason not to — public repos get unlimited free GitHub Actions minutes; private ones cap at 2,000/month (still plenty, just capped). The repo never contains secrets — those live in `.env` (gitignored) or platform secret stores.

### 5.3 Add GitHub Actions secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `TURSO_DATABASE_URL` | The `libsql://...` URL from §5.1 |
| `TURSO_AUTH_TOKEN` | The token from §5.1 |
| `GEMINI_API_KEYS` | Comma-separated list of Gemini keys |
| `CLAUDE_API_KEY` | *(optional)* |
| `GMAIL_FROM` / `GMAIL_TO` / `GMAIL_APP_PASSWORD` | *(optional)* |

### 5.4 Run the workflow once to populate the DB

1. Repo → **Actions** tab → **Run pipeline** workflow
2. Click **Run workflow** → `stage: all` → **Run**
3. Wait ~5–10 min

Verify rows landed:

```bash
turso db shell clinical-news
> SELECT COUNT(*) FROM articles;
> SELECT id, ran_at, headline FROM synthesis_runs;
```

### 5.5 Mint a fine-grained PAT for the "Generate" button

The button on Vercel calls GitHub's `repository_dispatch` API. That needs a token with **Actions: Write** on this repo only.

1. <https://github.com/settings/personal-access-tokens/new>
2. **Resource owner**: your account (or org)
3. **Repository access**: *Only select repositories* → `ClinicalNewsBot`
4. **Repository permissions** → **Actions**: Read and write
5. **Generate token**, copy the `github_pat_...` value

### 5.6 Deploy to Vercel

1. <https://vercel.com/new> → **Import Git Repository** → pick `ClinicalNewsBot`
2. **Framework Preset**: Other (Vercel auto-uses `vercel.json`)
3. Set environment variables (Project Settings → Environment Variables, **Production** + **Preview**):

| Name | Value |
|---|---|
| `TURSO_DATABASE_URL` | from §5.1 |
| `TURSO_AUTH_TOKEN` | from §5.1 |
| `ADMIN_PASSWORD` | a strong password — gates the Generate button |
| `GH_REPO` | `your-username/ClinicalNewsBot` |
| `GH_DISPATCH_TOKEN` | the PAT from §5.5 |

4. **Deploy**

Vercel builds from `vercel.json` + `requirements.txt` (lean web-only deps; the heavy pipeline deps stay on GitHub Actions). After deploy you get a URL like `https://clinical-news-bot.vercel.app/`.

### 5.7 Test the "Generate new brief" button

1. Open `/library`
2. Click **Generate new brief**
3. Browser prompts for HTTP Basic credentials → `admin` / your `ADMIN_PASSWORD`
4. Button changes to **Generating…**, status text appears
5. The page polls `/admin/status` every 20 s
6. ~5–10 min later, when a new `synthesis_runs` row lands in Turso, the page auto-reloads showing the new brief at the top of the library

If nothing fires:
- Repo → **Actions** tab — should show a triggered run
- Vercel → **Logs** for the `/admin/generate` request — shows what GitHub returned
- Common causes: `GH_REPO` typo, PAT lacks Actions: Write, `ADMIN_PASSWORD` empty (returns 503)

---

## 6. Source registry

`sources.yaml` is the single source of truth for what gets ingested. Ships with sensible defaults: 5 structured authoritative feeds (ClinicalTrials.gov, PubMed, FDA, EMA, RetractionWatch), 2 trade pubs (Endpoints, Clinical Trials Arena), and ~10 newspapers.

### Add a source

```yaml
- id: my-new-feed
  name: My New Feed
  source_type: newspaper           # or registry | journal | regulator | retraction
  rss_url: https://example.com/feed.xml
  paywalled: false
  extraction_strategy: jina_reader  # or rss_snippet_only | trafilatura | api_native
  active: true
```

Next ingest run picks it up automatically. To disable a source without deleting it, flip `active: false` (kill switch).

### What the fields mean

| Field | Meaning |
|---|---|
| `source_type` | Used in synthesis to weight authoritative facts (`registry` > `journal` > `regulator` > `newspaper`) |
| `extraction_strategy` | `jina_reader` (default) → `r.jina.ai` for clean Markdown. `rss_snippet_only` for paywalled sources. `trafilatura` for sites where Jina fails. `api_native` for adapters that fetch full text directly (CT.gov, PubMed). |
| `bypass_filtering` | `true` skips the keyword/embedding/LLM gate. Reserved for authoritative structured sources where every entry is by definition relevant. |

---

## 7. Verify the relevance classifier

```bash
.venv/bin/clinical-news eval relevance
```

Runs the live filter chain against `eval/relevance.jsonl` (102 hand-labelled examples) and reports precision, recall, F1, and a list of misclassifications.

**Targets:** precision ≥ 0.80, recall ≥ 0.90.

If you fall short, tune thresholds in `src/clinical_news/pipeline.py`:

| Constant | Default | Effect |
|---|---|---|
| `EMBEDDING_LOWER` | 0.45 | Below: drop without LLM call |
| `EMBEDDING_UPPER` | 0.65 | Above: accept without LLM call |
| `LLM_ACCEPT` | 0.7 | LLM confidence ≥ this → accept |
| `LLM_REVIEW` | 0.4 | LLM in [0.4, 0.7) → `needs_review` |
| `MAX_BRIEF_REFS` | 10 | Hard cap on distinct citations per brief |

Lowering thresholds raises recall at the cost of precision; raising them does the opposite. Re-run `eval relevance` after every change.

> **Eval gold set & v2 scope:** the bundled `eval/relevance.jsonl` was hand-labelled under the *broader* v1 definition (which accepted standalone drug approvals and biotech earnings tied to trials). Under the stricter v2 prompt, some of those items shift from "relevant" to "not relevant", so `eval relevance` will report lower precision until the gold labels are revised. Use it to track *change* rather than as an absolute pass/fail.

### Use your manual feedback as additional truth

Articles you label thumbs-up/down via the UI get stored in `articles.user_label`. Include them in eval:

```bash
.venv/bin/clinical-news eval relevance --include-user-labels
```

---

## 8. How a brief is generated

When `synthesize` runs (button or `clinical-news synthesize`):

1. Pull every article with `status='ingested'` published in the last 7 days
2. Bail out (and log `skipped_insufficient`) if fewer than 3 articles
3. Cluster by embedding cosine similarity (agglomerative, distance threshold 0.30)
4. **Per cluster:** one Gemini call → `{theme, key_facts, disagreements}` with citations restricted to that cluster's article IDs
5. **Meta-synthesis:** one Gemini call weaves cluster summaries into a 2,400–3,000-word brief
6. *(Optional)* Claude polish call if `CLAUDE_API_KEY` is set
7. Validate every `[ref:X]` resolves to an input article; coverage ≥ 80%; quotes ≤ 15 words
8. Render Markdown with footnote citations and a Sources appendix
9. Mark articles `status='synthesized'`; record run metadata in `synthesis_runs`

If validation fails the run is recorded with `status='needs_review'` — the file is still written so you can inspect what went wrong.

---

## 9. Where things are stored

### Path A (local SQLite)

| What | Where |
|---|---|
| Source articles | `app_data/articles.db` → `articles` table |
| Generated briefs (canonical) | `synthesis_runs.body_md` column |
| Generated briefs (mirror copy) | `app_data/synthesis/brief_<timestamp>.md` |
| Synthesis run metadata | `synthesis_runs` table |
| Ingest errors | `ingest_errors` table |
| Keypool cooldowns | `app_data/keypool_state.json` |
| Logs | stderr (JSON) — redirect with `clinical-news web 2>> /tmp/web.log` if you want a file |

### Path B (Turso)

Same tables, but on Turso instead of a local file. **No filesystem state at all** — briefs live in `synthesis_runs.body_md`. There's no MD mirror on disk because Vercel + Actions filesystems are ephemeral.

### Inspecting the DB

```bash
# Path A
sqlite3 app_data/articles.db
# Path B
turso db shell clinical-news

# either way:
> SELECT status, COUNT(*) FROM articles GROUP BY status;
> SELECT id, ran_at, headline, citation_coverage, status
    FROM synthesis_runs ORDER BY ran_at DESC LIMIT 10;
> SELECT title, url FROM articles
    WHERE status = 'ingested' ORDER BY published_at DESC LIMIT 10;
```

---

## 10. Reader UI reference

Same UI on both deploy paths.

### Routes

| URL | What |
|---|---|
| `/` | Redirects to the latest brief |
| `/library` | Paginated list of past briefs + **Generate new brief** button |
| `/briefs/{id}` | Rendered brief with footnote citations + Sources appendix |
| `/briefs/{id}.md` | Markdown download |
| `/search?q=...` | FTS5 search over briefs + source articles. Filters: `&from=YYYY-MM-DD&to=YYYY-MM-DD&source=<source_id>` |
| `POST /admin/feedback/{article_id}` | Mark `relevant` / `not_relevant` (form: `label=...`) |
| `POST /admin/generate` | Run the full ingest+synthesis pipeline. Auto-detects mode: GitHub Actions if `GH_REPO`/`GH_DISPATCH_TOKEN` set, otherwise in-process background task. |
| `GET /admin/status` | JSON: latest synthesis run (used by polling UI) |

PDFs are produced via the browser's native print → "Save as PDF" on the brief page (clean print CSS is built in). There is no server-side PDF route.

### Admin password

`/admin/*` routes are protected by HTTP Basic. Username is always `admin`. Set:

```
ADMIN_PASSWORD=pick-a-strong-password
```

Empty `ADMIN_PASSWORD` returns 503 from those routes — disabled until set.

---

## 11. Common operations

### Re-run a synthesis manually

Click **Generate new brief** in the UI, or run from the CLI:
```bash
.venv/bin/clinical-news synthesize --dry-run    # writes to disk + DB but skips email
```

To replay a previous week, flip the relevant rows back to `status='ingested'` first:
```sql
UPDATE articles SET status = 'ingested'
WHERE status = 'synthesized' AND published_at >= '<some-date>';
```

### Reset failed/borderline rows for re-classification

```bash
sqlite3 app_data/articles.db \
  "DELETE FROM articles WHERE status IN ('error','needs_review');"
.venv/bin/clinical-news ingest    # re-fetches and re-classifies
```

(For Path B, do the same against Turso via `turso db shell`.)

### Update a prompt

Never edit a `_vN.md` file in place after a synthesis run.

1. Copy `prompts/meta_synthesis_v2.md` → `prompts/meta_synthesis_v3.md`
2. Edit `_v3`
3. Update `PROMPT_PATH` and `PROMPT_VERSION` in the corresponding module (e.g. `src/clinical_news/synthesize/meta.py`)
4. Re-run `eval relevance` (and any synthesis golds) to confirm no regression

`synthesis_runs.prompt_version` records which version produced each brief.

### Inspect a brief that failed validation

```bash
sqlite3 app_data/articles.db \
  "SELECT id, ran_at, status, citation_coverage FROM synthesis_runs WHERE status = 'needs_review';"

sqlite3 app_data/articles.db \
  "SELECT body_md FROM synthesis_runs WHERE id = <id>;" | less
```

The structured logs from the synthesis run record which `[ref:X]` markers were unresolved or which paragraphs lacked citations.

### Back up the DB (Path A)

There's no automated backup — the project deliberately has no scheduler. If you want one, just copy the file before doing anything risky:

```bash
cp app_data/articles.db app_data/articles.db.bak-$(date +%F)
```

Restoring is symmetric:

```bash
cp app_data/articles.db.bak-2026-05-10 app_data/articles.db
```

The DB is plain SQLite — no migration needed.

(For Path B, Turso has its own snapshot/restore tools — `turso db restore`. Free tier includes point-in-time restore.)

### Pull Turso DB locally for inspection (Path B)

```bash
turso db shell clinical-news .dump > /tmp/turso.sql
sqlite3 /tmp/local-from-turso.db < /tmp/turso.sql
sqlite3 /tmp/local-from-turso.db
```

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `GEMINI_API_KEY not set` | `.env` missing or unloaded | Confirm `.env` is in repo root and has the key (Path A); confirm Vercel env var (Path B) |
| 429 storms in logs | Single-key throttling | Set `GEMINI_API_KEYS` with 2–3 keys; rotation kicks in automatically |
| Source has zero ingests for 7+ days | Feed URL changed | `curl <rss_url>` to check; update `sources.yaml` |
| All Jina fetches time out | Rate-limited or service down | trafilatura fallback handles it; check `ingest_errors` |
| Synthesis says `skipped_insufficient` | <3 articles in last 7 days | Inspect `articles WHERE status='ingested'`; loosen filter thresholds |
| Citation coverage < 80% | LLM dropped citations | Inspect the brief; consider rerunning, then bump prompt version |
| Email not sent | Gmail app password wrong or 2FA off | Regenerate at <https://myaccount.google.com/apppasswords> |
| `pytest` fails | Dev deps missing | `.venv/bin/pip install -e ".[dev]"` |
| Vercel build fails: "Failed to resolve module" | Missing dep in `requirements.txt` | Add it; redeploy |
| `/admin/generate` → 502 (Path B) | PAT lacks Actions: Write, or wrong `GH_REPO` | Fix scope or env var |
| Generate button on Vercel runs the pipeline locally | `GH_REPO`/`GH_DISPATCH_TOKEN` missing — the route fell back to local mode but Vercel functions can't run that long, so it 504s after 10 s | Add both env vars in Vercel Project Settings, redeploy |
| Generate button does nothing | Browser cached old JS | Hard refresh (Ctrl-Shift-R) |
| Library shows 0 briefs (Path B) | First workflow hasn't completed | Trigger workflow from Actions tab manually |
| Generate button "Generating…" forever (Path A) | Background task crashed silently | Inspect uvicorn stderr logs; check `ingest_errors` table; rerun from CLI to surface the error |
| Cold-start slow on Vercel | Function cold start (~1–2 s) + Turso first connection (~200 ms) | Acceptable; subsequent requests are fast |

For deeper debugging, set `LOG_LEVEL=DEBUG` in your env — logs include source ID, URL, and stage for every operation.

---

## Costs

| Path | Service | Notes | Cost |
|---|---|---|---|
| A | Cloudflare Tunnel | Free unlimited HTTPS | $0 |
| A | Compute | Your machine | $0 (electricity) |
| B | Vercel Hobby | 100 GB bandwidth, 10 s function timeout (we never approach it) | $0 |
| B | Turso Starter | 9 GB, 1 B reads, 25 M writes / month | $0 |
| B | GitHub Actions | Unlimited (public) or 2,000 min/mo (private) | $0 |
| Both | Gemini API | Free tier with multi-key rotation | $0 |
| Both | Anthropic *(opt)* | ~$0.30/mo at typical volume | $0–$0.50 |

Total: **$0/mo** on either path with optional services off; at most a dollar with Claude polish on.
