# Clinical Trial News Aggregator & Synthesizer
## System Design Document

**Version:** 1.0
**Status:** Design — pre-implementation
**Stack:** n8n (self-hosted) · JavaScript (Code nodes) · Gemini Flash + Claude Pro · Jina Reader · SQLite

---

## 1. Purpose & Scope

### 1.1 Problem statement
Manually monitoring 15 newspaper websites for clinical trial and clinical research coverage is slow, inconsistent, and produces no reusable artifact. The goal is an automated pipeline that:

1. Continuously monitors 15 newspaper sources.
2. Identifies articles relevant to clinical trials and clinical research.
3. Extracts the full article content.
4. Synthesizes a referenced article from the relevant material on a defined cadence (e.g. weekly).
5. Delivers the draft for human review and final polish.

### 1.2 Out of scope (v1)
- Publishing the final article to a CMS or website.
- Multilingual sources (assume English-language papers in v1).
- Bypassing paywalls. Paywalled sources will be handled with snippet-only fallback.
- Real-time alerting (cadence is daily ingest, weekly synthesis).
- Image extraction or media handling.

### 1.3 Success criteria
- **Recall:** ≥90% of clearly relevant articles from the 15 sources are surfaced within 24 hours of publication.
- **Precision:** ≥80% of articles flagged "relevant" are actually about clinical trials/research (false positive rate <20%).
- **Synthesis quality:** weekly draft article is factually accurate against sources, cites every claim, and requires <30 minutes of human polish.
- **Cost:** ≤$0/month at steady state (free tiers only), excluding optional Claude API spend.
- **Reliability:** pipeline runs unattended for 7+ days without intervention.

---

## 2. High-Level Architecture

### 2.1 System diagram (logical)

```
┌─────────────────────────────────────────────────────────────────┐
│                       INGEST LAYER                              │
│  15× RSS feeds  ──►  Schedule Trigger (n8n, daily)              │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FILTERING LAYER                              │
│  Keyword filter (rule-based) ──► LLM relevance check (Gemini)   │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    EXTRACTION LAYER                             │
│  Jina Reader (full text)  ──►  fallback: snippet from RSS       │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                   PERSISTENCE LAYER                             │
│  SQLite (article store + dedup index)                           │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                   SYNTHESIS LAYER (weekly)                      │
│  Pull last-7-day articles  ──►  Gemini synthesis (draft)        │
│                            ──►  optional: Claude Pro (polish)   │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    DELIVERY LAYER                               │
│  Email (Gmail node) + Google Doc + audit log                    │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Two scheduled workflows
The system runs as **two independent n8n workflows**, deliberately decoupled:

| Workflow | Cadence | Purpose |
|---|---|---|
| **W1: Ingest** | Daily, 06:00 local | Pull RSS, filter, extract, persist |
| **W2: Synthesize** | Weekly, Sunday 18:00 local | Read last-7-day articles, draft, deliver |

Decoupling matters because (a) ingest is high-frequency and idempotent, (b) synthesis is expensive and infrequent, (c) failures in one must not block the other.

---

## 3. Detailed Component Design

### 3.1 Source registry

A versioned config file (`sources.json`) is the single source of truth for the 15 newspapers. Treat it as code — committed to git, never hand-edited in n8n.

```json
[
  {
    "id": "nyt-health",
    "name": "New York Times — Health",
    "rss_url": "https://rss.nytimes.com/services/xml/rss/nyt/Health.xml",
    "paywalled": true,
    "extraction_strategy": "rss_snippet_only",
    "active": true
  },
  {
    "id": "guardian-health",
    "name": "The Guardian — Health",
    "rss_url": "https://www.theguardian.com/society/health/rss",
    "paywalled": false,
    "extraction_strategy": "jina_reader",
    "active": true
  }
]
```

Fields:
- `extraction_strategy`: `jina_reader` (default), `rss_snippet_only` (paywalled), `custom_selector` (specific CSS selector defined in code).
- `active`: kill switch; flip to `false` to disable a source without deleting it.

### 3.2 Ingest workflow (W1)

**Step-by-step:**

1. **Schedule Trigger** — daily at 06:00.
2. **Read sources.json** — Code node, returns array of active sources.
3. **Split In Batches** — process one source at a time to control rate.
4. **RSS Read node** — fetch feed for current source.
5. **Code node: normalize** — coerce all feeds to a common shape:
   ```js
   {
     source_id: string,
     external_id: string,    // GUID from RSS, fallback to URL hash
     url: string,
     title: string,
     summary: string,        // RSS description/summary
     published_at: ISO8601,
     fetched_at: ISO8601
   }
   ```
6. **SQLite SELECT** — drop items whose `external_id` already exists. Cheap dedup before any expensive call.
7. **Code node: keyword filter** — see §3.4. Drops obviously irrelevant items.
8. **Gemini relevance check** — see §3.5. Binary classification: relevant / not relevant.
9. **Branch on classification:**
   - Not relevant → write to `articles` with `status='rejected'`, stop.
   - Relevant → continue.
10. **HTTP Request: Jina Reader** — `GET https://r.jina.ai/<url>` returns clean Markdown article text. No API key needed. See §3.6 for fallback logic.
11. **Code node: clean & truncate** — strip boilerplate, cap at 8,000 tokens to keep synthesis context manageable.
12. **SQLite INSERT** — full article record with `status='ingested'`.
13. **Error branch** — any failure logs to `ingest_errors` table and continues with next source. **No exception bubbles up to fail the whole run.**

### 3.3 Synthesis workflow (W2)

1. **Schedule Trigger** — Sunday 18:00.
2. **SQLite SELECT** — `WHERE status='ingested' AND published_at >= now() - 7 days`.
3. **Guard:** if fewer than 3 articles, send "nothing to synthesize this week" email and exit.
4. **Code node: cluster & rank** — group articles by topic similarity (see §3.7), rank by recency × source weight.
5. **Code node: build synthesis prompt** — see §3.8 for the prompt template.
6. **Gemini synthesis call** — produces draft with inline `[ref:source_id]` markers.
7. **Code node: validate citations** — every `[ref:X]` must map to an article in the input set. Failures flagged for human review.
8. **(Optional) Claude API polish** — if user has the Anthropic API key configured, send draft + sources to Claude for a quality pass.
9. **Code node: render output** — convert to Markdown with proper citation footnotes.
10. **Gmail node** — email draft to user.
11. **Google Docs node** — create timestamped doc in a designated folder.
12. **SQLite UPDATE** — mark articles as `status='synthesized'`, log run to `synthesis_runs`.

### 3.4 Keyword filter (rule layer)

Cheap, deterministic, runs against title + RSS summary. Goal: cut 95% of irrelevant items so the LLM only sees plausible candidates.

```js
const POSITIVE_TERMS = [
  'clinical trial', 'clinical research', 'clinical study',
  'phase i', 'phase ii', 'phase iii', 'phase 1', 'phase 2', 'phase 3',
  'randomized', 'randomised', 'double-blind', 'placebo-controlled',
  'fda approval', 'ema approval', 'investigational',
  'cohort study', 'efficacy', 'enrollment', 'enrolment',
  'principal investigator', 'irb', 'ethics committee',
  'pivotal trial', 'first-in-human', 'open-label'
];

const NEGATIVE_TERMS = [
  'clinical trial lawyer',  // legal ads
  'mock trial',
  'trial subscription'
];

function isCandidate(item) {
  const text = `${item.title} ${item.summary}`.toLowerCase();
  if (NEGATIVE_TERMS.some(t => text.includes(t))) return false;
  return POSITIVE_TERMS.some(t => text.includes(t));
}
```

This is intentionally **generous** (favor recall over precision); the LLM check downstream tightens precision.

### 3.5 LLM relevance check

Gemini Flash, single call per candidate, structured JSON output. Prompt:

```
You are a classifier for clinical trials and clinical research news.

Article title: {title}
Article summary: {summary}

Question: Is this article primarily about a clinical trial, clinical study,
or clinical research finding? "Primarily about" means the trial/study is
the main subject, not a passing mention.

Respond ONLY with JSON: {"relevant": true|false, "confidence": 0.0-1.0, "reason": "..."}
```

Threshold: accept if `relevant=true AND confidence >= 0.6`. Borderline cases (0.4–0.6) go to a `needs_review` queue for the user to spot-check.

### 3.6 Article extraction

**Primary path: Jina Reader.** Free, no auth, handles JavaScript-rendered pages, returns clean Markdown.

```
GET https://r.jina.ai/https://example.com/article-url
```

**Fallback chain:**
1. Jina Reader returns <500 chars or HTTP error → try direct fetch + Mozilla Readability (npm `@mozilla/readability` in a Code node).
2. Direct fetch fails or returns paywall page → fall back to RSS summary only and tag article `extraction_quality='snippet_only'`.
3. All failures are logged but never block the pipeline.

**Paywall detection heuristic:** if extracted text contains phrases like "subscribe to continue," "create a free account to read," or is suspiciously short relative to typical article length, mark as paywalled and use snippet.

### 3.7 Deduplication & clustering

Three layers, each catching what the previous misses:

**Layer 1 — exact dedup (URL/GUID).** SQLite UNIQUE constraint on `external_id` per source. Free, prevents reprocessing.

**Layer 2 — near-duplicate detection (cross-source).** Same story across multiple papers. Approach: compute SimHash or MinHash of article body, compare against last 14 days of articles. Threshold tuned empirically — start at Hamming distance ≤4 for SimHash 64-bit.

**Layer 3 — topic clustering (synthesis-time).** Before sending to the LLM, group articles into topic clusters so the synthesis can say "three sources reported on X" rather than treating each as independent. Simple approach: ask the LLM itself to cluster by topic in a preliminary call, returning a JSON structure of `{cluster_label: [article_ids]}`. More sophisticated: sentence embeddings via a free embedding API + k-means, but unnecessary at v1 scale.

### 3.8 Synthesis prompt design

This is the highest-leverage component. The prompt template:

```
You are writing a synthesis article on recent developments in clinical
trials and clinical research, drawing on the source articles below.

REQUIREMENTS:
1. Length: 800–1,200 words.
2. Structure: lede, 3–5 thematic sections, brief outlook.
3. EVERY factual claim must be followed by a citation marker [ref:SOURCE_ID]
   matching the IDs below. Claims without citations will be rejected.
4. Do not introduce facts, numbers, or quotes that are not in the sources.
5. If sources disagree, surface the disagreement explicitly.
6. Neutral, journalistic tone. No editorializing.
7. Quote sparingly: max one short quote (under 15 words) per source.

SOURCES:
[
  {
    "id": "art_001",
    "source": "The Guardian",
    "title": "...",
    "published_at": "...",
    "url": "...",
    "body": "..."
  },
  ...
]

OUTPUT FORMAT:
{
  "headline": "...",
  "deck": "...",
  "body_markdown": "...",
  "citations_used": ["art_001", "art_003", ...]
}
```

Post-call validation:
- Parse JSON; reject if malformed (one retry).
- Extract every `[ref:X]` from `body_markdown`; verify each X is in `citations_used` and in input source IDs.
- Verify `citations_used ⊆ input_ids`.
- Compute citation coverage: % of paragraphs with at least one citation. Below 80% → flag for review.

### 3.9 Persistence schema (SQLite)

```sql
CREATE TABLE sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  rss_url TEXT NOT NULL,
  paywalled INTEGER NOT NULL DEFAULT 0,
  extraction_strategy TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE articles (
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
    -- ingested | rejected | needs_review | synthesized | error
  relevance_confidence REAL,
  extraction_quality TEXT,
    -- full | snippet_only | failed
  simhash TEXT,
  UNIQUE(source_id, external_id)
);
CREATE INDEX idx_articles_status_published ON articles(status, published_at);
CREATE INDEX idx_articles_simhash ON articles(simhash);

CREATE TABLE ingest_errors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT,
  url TEXT,
  error_message TEXT,
  occurred_at TEXT NOT NULL
);

CREATE TABLE synthesis_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ran_at TEXT NOT NULL,
  article_count INTEGER NOT NULL,
  output_path TEXT,
  citation_coverage REAL,
  status TEXT NOT NULL
);
```

### 3.10 Error handling philosophy

**Principle:** *Ingest must never fail loudly.* A single broken RSS feed cannot stop the other 14. Every external call is wrapped:

- Per-source try/catch in W1.
- Errors logged to `ingest_errors`, surfaced in a weekly health email.
- LLM calls have one automatic retry with exponential backoff (1s, 4s).
- Network calls have a 30-second hard timeout.
- Rate-limit errors (HTTP 429) trigger a 60-second wait and one retry.

**Synthesis is allowed to fail loudly** — if it fails, the user gets an email saying so, and the workflow can be re-run manually.

---

## 4. Technology Choices: Justifications

### 4.1 Why n8n
- Open-source, self-hostable, free forever.
- Visual workflow makes the pipeline structure legible (and modifiable by future-you).
- Native nodes for RSS, HTTP, SQLite, Gmail, Google Docs, Gemini.
- Code nodes give an escape hatch to JavaScript when nodes aren't enough.
- Mature error-handling and retry primitives.

### 4.2 Why JavaScript (not Python)
- n8n runs on Node.js; Code nodes are JS-native — no service boundary, no deployment.
- Required libraries (`@mozilla/readability`, `cheerio`, `simhash-js`) all available via npm.
- The LLM does the heavy NLP; we don't need spaCy or transformers locally.
- Single language across the system reduces operational surface area.
- Python would require a separate service, adding deployment, networking, and failure modes for no functional gain.

### 4.3 Why Gemini for ingest, Claude Pro for review
- Gemini 2.0 Flash has a generous free tier and is more than capable for binary classification and synthesis drafting.
- Claude Pro is a chat product, not an API; using it manually for the final polish leverages your existing subscription without adding paid API spend.
- This split keeps the automated pipeline at $0/month while putting the strongest model where it matters most: final quality.

### 4.4 Why SQLite (not Postgres, not Airtable)
- Single-user, single-machine deployment — Postgres is overkill.
- SQLite file lives next to n8n; trivial backup (copy one file).
- Airtable would work but adds a network dependency, rate limits, and vendor lock-in for a system that is fundamentally local.
- SQL gives proper deduplication via UNIQUE constraints and fast queries on indexed columns.

### 4.5 Why Jina Reader
- Free, no API key, handles JS-rendered pages, returns clean Markdown.
- Single failure point, but cheap to swap out — every newspaper extractor lives behind a strategy field in the source registry, so an alternative can be slotted in without touching the pipeline structure.

---

## 5. Deployment

### 5.1 Hosting options (ranked by recommendation)
1. **Local Docker on a always-on machine** (home server, Raspberry Pi 4+, old laptop). Zero cost, full control, no external dependencies.
2. **Oracle Cloud Free Tier VM** — genuinely free indefinitely, ARM Ampere instance handles n8n comfortably.
3. **n8n Cloud** — paid; only consider if local hosting is impossible.

### 5.2 docker-compose.yml (sketch)

```yaml
version: "3.8"
services:
  n8n:
    image: n8nio/n8n:latest
    restart: unless-stopped
    ports:
      - "5678:5678"
    environment:
      - N8N_BASIC_AUTH_ACTIVE=true
      - N8N_BASIC_AUTH_USER=${N8N_USER}
      - N8N_BASIC_AUTH_PASSWORD=${N8N_PASSWORD}
      - GENERIC_TIMEZONE=Europe/Amsterdam
      - DB_TYPE=sqlite
    volumes:
      - ./n8n_data:/home/node/.n8n
      - ./app_data:/data            # our SQLite DB lives here
      - ./sources.json:/data/sources.json:ro
```

### 5.3 Secrets management
- All API keys (Gemini, optional Claude, Gmail OAuth) stored in n8n's built-in credentials store (encrypted at rest).
- No secrets in workflow JSON exports.
- `.env` file for docker-compose, gitignored.

### 5.4 Backups
- Nightly cron: `cp /data/articles.db /backups/articles-$(date +%F).db`
- Keep 14 days of daily backups, 12 weeks of weekly.
- Workflow JSON exported weekly to git.

---

## 6. Observability

### 6.1 What gets logged
- Every ingest run: source, items fetched, items kept, errors. One row per source per run.
- Every LLM call: model, tokens in/out, latency, cost (if applicable).
- Every synthesis run: input article count, output length, citation coverage, model used.

### 6.2 Health email
Weekly summary email (sent with synthesis output):
- Articles ingested this week, by source.
- Sources with zero ingests in 7 days (likely broken feed).
- Total LLM tokens used.
- Errors encountered.

### 6.3 Manual inspection
n8n's execution history UI shows every run, every node's input/output. Sufficient for debugging at this scale — no need for external observability tooling.

---

## 7. Security & Compliance

- **No PII** is processed; all sources are public news.
- **Robots.txt and ToS:** Jina Reader handles its own compliance; for direct fetches, respect `robots.txt` and rate-limit to 1 request per source per 10 seconds.
- **API key hygiene:** rotate Gemini key every 90 days.
- **Output review:** the synthesis is a *draft*, never auto-published. A human is always in the loop before anything goes public — this is both a quality and a legal safeguard against inadvertently reproducing copyrighted content.
- **Citation discipline:** synthesis prompt explicitly limits direct quotes to under 15 words and one quote per source. Rejecting outputs that violate this is a hard rule.

---

## 8. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| RSS feed format changes | Med | Low | Per-source try/catch; alert on zero-ingest sources |
| Jina Reader goes down or rate-limits | Med | High | Fallback to Mozilla Readability + RSS snippet |
| LLM hallucinates citations | High | High | Post-call citation validation; reject and retry |
| Paywalled articles dominate | Med | Med | Mark snippet-only; weight full-text articles higher in synthesis |
| Free LLM tier limits hit | Low | Med | Backoff + retry; degrade to keyword-only filtering temporarily |
| Same story across multiple papers inflates significance | High | Med | SimHash dedup + topic clustering before synthesis |
| n8n container crashes | Low | Low | `restart: unless-stopped`; SQLite persists state |
| Synthesis produces poor draft | Med | Med | Two-stage: Gemini draft → Claude Pro polish; human in loop |

---

## 9. Implementation Roadmap

**Phase 1 — Walking skeleton (1–2 days).** One source (a non-paywalled paper), keyword filter only, no LLM, output to console. Goal: prove the n8n + SQLite + Jina Reader chain works end to end.

**Phase 2 — All 15 sources (1 day).** Populate `sources.json`, add error handling, run for 3 days unattended. Confirm each source produces ingests.

**Phase 3 — LLM relevance filter (1 day).** Add Gemini classification node. Tune threshold by manually labeling 50 articles and measuring precision/recall.

**Phase 4 — Synthesis workflow (2 days).** Build W2, develop and iterate on the prompt, validate citation accuracy on a sample week.

**Phase 5 — Polish & observability (1 day).** Health email, backup cron, docs.

**Total:** roughly one working week. Build in this order; do not skip ahead.

---

## 10. Open Questions for the User

These need answers before Phase 1 begins:

1. **Which 15 newspapers?** Specifically — paywall status of each affects extraction strategy.
2. **Hosting target:** local always-on machine, or cloud VM?
3. **Output destination:** email only, or also a Google Doc / Notion page?
4. **Synthesis cadence:** weekly confirmed, or different (daily digest, monthly long-read)?
5. **Reader audience:** internal note for yourself, or polished piece for a public audience? (Affects tone constraints in the synthesis prompt.)
6. **Claude API usage:** are you willing to add ~$1–5/month for the Claude API to automate the polish step, or strictly Claude.ai Pro chat-only?
7. **Topic scope:** strictly "clinical trials and clinical research," or include adjacent areas (drug approvals, regulatory news, biotech earnings tied to trial results)?

---

## Appendix A — File & Repository Layout

```
clinical-trial-aggregator/
├── docker-compose.yml
├── .env                          # gitignored
├── .gitignore
├── README.md
├── sources.json                  # source registry
├── workflows/
│   ├── W1_ingest.json            # exported n8n workflow
│   └── W2_synthesize.json
├── code/
│   ├── normalize_rss.js
│   ├── keyword_filter.js
│   ├── simhash_dedup.js
│   ├── build_synthesis_prompt.js
│   └── validate_citations.js
├── sql/
│   └── schema.sql
├── docs/
│   ├── DESIGN.md                 # this file
│   ├── PROMPTS.md                # prompt templates, versioned
│   └── RUNBOOK.md                # operational procedures
└── backups/                      # gitignored
```

## Appendix B — Glossary

- **Recall:** of all relevant articles that exist, the share we surface.
- **Precision:** of articles we surface, the share that are actually relevant.
- **SimHash:** a locality-sensitive hash where similar documents produce similar hashes; small Hamming distance ⇒ likely near-duplicates.
- **Idempotent:** running an operation multiple times has the same effect as running it once. The ingest pipeline is idempotent because of the UNIQUE constraint on `(source_id, external_id)`.
- **Walking skeleton:** the smallest possible end-to-end implementation that exercises every layer of the system. Built first, expanded incrementally.
