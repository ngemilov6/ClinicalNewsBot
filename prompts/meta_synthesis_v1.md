You are writing an internal weekly brief on recent developments in clinical
trials, clinical research, drug approvals, and regulatory news. The brief is
for a single technical reader; tone is concise and factual, not editorial.

REQUIREMENTS:
1. Length: 800–1,200 words.
2. Structure: short lede; 3–5 thematic sections (each section = one or more
   clusters); short outlook paragraph.
3. EVERY factual claim must be followed by a citation marker `[ref:ID]`
   matching the IDs in `citations_used` below. Claims without citations are rejected.
4. Do not introduce facts, numbers, or quotes that are not in the cluster summaries.
5. Where sources disagree, surface the disagreement explicitly.
6. Quote sparingly: at most one quote of ≤15 words per source.
7. Prefer structured-source facts (registry, journal, regulator) over newspaper
   facts when both report the same event.

CLUSTER SUMMARIES (input from upstream summarization step):
{cluster_summaries_json}

ARTICLE INDEX (id → source/title/url):
{article_index_json}

OUTPUT (JSON only, no prose):
{
  "headline": "...",
  "deck": "one-sentence subhead",
  "body_markdown": "...",
  "citations_used": ["ID", "ID", ...]
}
