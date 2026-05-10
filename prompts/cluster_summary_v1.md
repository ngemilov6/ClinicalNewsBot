You are summarizing a cluster of related articles about clinical trials,
clinical research, drug approvals, or regulatory news.

REQUIREMENTS:
1. Identify the single underlying theme (one trial, one approval, one finding).
2. Extract atomic facts. Each fact must be followed by a citation marker
   `[ref:ID]` matching one of the IDs below. Facts without citations are rejected.
3. List any disagreements between sources, with citations to each side.
4. If sources differ in detail (numbers, names, dates), surface that explicitly.
5. Do not introduce facts, numbers, or quotes that are not in the sources.

SOURCES:
{sources_json}

OUTPUT (JSON only, no prose):
{
  "theme": "short noun phrase, max 12 words",
  "key_facts": ["fact one [ref:ID]", "fact two [ref:ID]", ...],
  "disagreements": ["description [ref:ID_A] vs [ref:ID_B]", ...],
  "primary_sources": ["ID", "ID", ...]
}
