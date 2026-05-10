# Deploy guide — Vercel + Turso + GitHub Actions

Free-tier deployment of the reader UI on Vercel, with all heavy work running on GitHub Actions and the database hosted on Turso.

```
[ Vercel: Reader UI ]              ← free
        ↓ "Generate" button
        ↓ POST /admin/generate
        ↓ HTTP → GitHub API repository_dispatch
[ GitHub Actions: ingest+synthesize ]  ← free for public repos / 2,000 min/mo private
        ↓ writes
[ Turso: SQLite-compatible DB ]    ← free 9 GB / 1B reads / 25M writes
        ↑ reads
[ Vercel: Reader UI ]
```

---

## 1. Provision Turso

1. Sign up at <https://turso.tech>.
2. Create a database:
   ```bash
   turso auth signup
   turso db create clinical-news
   turso db show clinical-news --url
   turso db tokens create clinical-news
   ```
3. Save the **URL** (looks like `libsql://clinical-news-USERNAME.turso.io`) and the **auth token**.

You'll set both as secrets in two places: GitHub (so Actions can write) and Vercel (so the UI can read).

---

## 2. Push to GitHub

```bash
gh repo create ClinicalNewsBot --public --source . --push
```

(or use the GitHub UI). Public repo gets unlimited free Actions minutes; private gets 2,000/month, which is plenty.

---

## 3. Configure GitHub Actions secrets

In your repo: **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Value |
|---|---|
| `TURSO_DATABASE_URL` | The `libsql://...` URL from step 1 |
| `TURSO_AUTH_TOKEN` | The token from step 1 |
| `GEMINI_API_KEYS` | Comma-separated list of Gemini keys |
| `CLAUDE_API_KEY` | *(optional)* Anthropic key for the polish step |
| `GMAIL_FROM`, `GMAIL_TO`, `GMAIL_APP_PASSWORD` | *(optional)* email delivery |

Trigger the workflow once manually to populate the DB:

1. Go to the **Actions** tab.
2. Pick **Run pipeline** → **Run workflow** → `stage: all`.
3. Wait ~5–10 minutes.

You should see rows appear in Turso (`turso db shell clinical-news` then `SELECT COUNT(*) FROM articles;`).

---

## 4. Mint a fine-grained PAT for the "Generate" button

The button on Vercel calls `repository_dispatch` against your repo. That needs a token with **Actions: Write** permission.

1. <https://github.com/settings/personal-access-tokens/new>.
2. Resource owner: your account / org.
3. Repository access: **Only select repositories** → pick `ClinicalNewsBot`.
4. Permissions → **Repository permissions** → **Actions**: Read and write.
5. Generate, copy the `github_pat_...` token.

You'll add this as a Vercel env var in the next step (`GH_DISPATCH_TOKEN`).

---

## 5. Deploy to Vercel

1. <https://vercel.com/new> → **Import** the GitHub repo.
2. Framework preset: **Other** (Vercel auto-detects `vercel.json`).
3. **Environment Variables** (Project Settings → Environment Variables, set for **Production** and **Preview**):

| Name | Value |
|---|---|
| `TURSO_DATABASE_URL` | Same as step 1 |
| `TURSO_AUTH_TOKEN` | Same as step 1 |
| `ADMIN_PASSWORD` | A strong password — gates the "Generate" button |
| `GH_REPO` | `your-username/ClinicalNewsBot` |
| `GH_DISPATCH_TOKEN` | The PAT from step 4 |

4. Hit **Deploy**.

Vercel will build using `vercel.json` + `requirements.txt`, expose a URL like `https://clinical-news-bot.vercel.app/`, and you should see the library populated from Turso.

---

## 6. Test the "Generate new brief" button

1. Open the `/library` page.
2. Click **Generate new brief**.
3. Browser prompts for HTTP Basic credentials → enter `admin` / your `ADMIN_PASSWORD`.
4. The button changes to **Generating…** and shows the status message.
5. Behind the scenes, the request hits `/admin/generate`, which fires GitHub's `repository_dispatch` API → starts the workflow.
6. The page polls `/admin/status` every 20 s. When a new `synthesis_runs` row appears, the page reloads automatically — typically 5–10 minutes later.
7. The new brief is at the top of the library and at `/briefs/{new-id}`.

If it doesn't fire:

- Check the **Actions** tab for a triggered run.
- Look at the Vercel function logs for the request to `/admin/generate` — it will tell you what GitHub returned.

---

## 7. What's different on Vercel vs. local

| | Local (laptop) | Vercel deploy |
|---|---|---|
| DB | SQLite file at `app_data/articles.db` | Turso (libsql) — same SQL, same FTS5 |
| Briefs | `app_data/synthesis/*.md` files + DB column | DB column only (filesystem ephemeral) |
| PDF download | Was WeasyPrint, removed | Browser print → Save as PDF |
| Cron | Linux cron | GitHub Actions, button-triggered |
| Heavy deps | All in venv | Only the web subset (no sklearn, trafilatura, feedparser, google-generativeai) — those run on Actions |

The repo is dual-stack: it works on either path. Deploy whichever fits.

---

## 8. Costs

| Service | Limit | Cost |
|---|---|---|
| Vercel Hobby | 100 GB bandwidth, ~10 s function timeout (we never approach the latter) | $0 |
| Turso Starter | 9 GB, 1 B reads, 25 M writes / month | $0 |
| GitHub Actions | Unlimited (public repo) or 2,000 min/mo (private) | $0 |
| Gemini API | Free tier, multi-key rotation | $0 |

Total: **$0/mo**.

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Vercel build fails: "Failed to resolve module" | Missing dep in `requirements.txt` | Add it; redeploy |
| `/admin/generate` → 503 "GH_REPO not set" | Vercel env vars missing | Add them in project settings, redeploy |
| `/admin/generate` → 502 from GitHub | PAT lacks Actions: Write, or wrong `GH_REPO` | Fix the PAT scope or env var |
| Button click does nothing | Browser cached old JS | Hard refresh (Ctrl-Shift-R) |
| Library shows 0 briefs | First workflow run hasn't completed yet | Trigger workflow manually from Actions tab |
| Cold-start slow | Vercel function cold start (Python ~1–2 s); Turso first connection (~200 ms) | Acceptable for this app; subsequent requests are fast |
