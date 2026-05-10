"""Citation validation. Hard-rejects unresolved refs; flags low coverage."""
from __future__ import annotations

import re
from dataclasses import dataclass

REF_RE = re.compile(r"\[ref:([A-Za-z0-9_\-]+)\]")
COVERAGE_THRESHOLD = 0.80
MAX_DISTINCT_REFS = 10


@dataclass
class ValidationResult:
    ok: bool
    citation_coverage: float
    unresolved: list[str]
    citations_used_mismatch: list[str]
    quote_violations: list[str]
    distinct_refs: int = 0
    over_ref_cap: bool = False


def _paragraphs(md: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", md) if p.strip()]


def _find_quotes(md: str) -> list[str]:
    return re.findall(r'"([^"\n]{1,500})"', md)


def validate(synthesis: dict, valid_ids: set[str]) -> ValidationResult:
    body = synthesis.get("body_markdown", "") or ""
    citations_used = set(synthesis.get("citations_used", []))

    found = set(REF_RE.findall(body))
    unresolved = sorted(found - valid_ids)
    citations_used_mismatch = sorted(citations_used - valid_ids)

    paras = _paragraphs(body)
    cited_paras = [p for p in paras if REF_RE.search(p)]
    coverage = (len(cited_paras) / len(paras)) if paras else 0.0

    # Quote-length check: max 15 words per quote.
    quote_violations: list[str] = []
    for q in _find_quotes(body):
        if len(q.split()) > 15:
            quote_violations.append(q[:80])

    distinct_refs = len(found | citations_used)
    over_ref_cap = distinct_refs > MAX_DISTINCT_REFS

    ok = (
        not unresolved
        and not citations_used_mismatch
        and coverage >= COVERAGE_THRESHOLD
        and not quote_violations
        and not over_ref_cap
    )
    return ValidationResult(
        ok=ok,
        citation_coverage=round(coverage, 3),
        unresolved=unresolved,
        citations_used_mismatch=citations_used_mismatch,
        quote_violations=quote_violations,
        distinct_refs=distinct_refs,
        over_ref_cap=over_ref_cap,
    )
