"""Cheap rule-based filter on title + summary. Generous — favors recall."""
from __future__ import annotations

POSITIVE_TERMS = (
    "clinical trial", "clinical research", "clinical study",
    "phase i", "phase ii", "phase iii", "phase 1", "phase 2", "phase 3",
    "randomized", "randomised", "double-blind", "placebo-controlled",
    "fda approval", "ema approval", "investigational",
    "cohort study", "efficacy", "enrollment", "enrolment",
    "principal investigator", "irb", "ethics committee",
    "pivotal trial", "first-in-human", "open-label",
    "interim results", "primary endpoint", "drug approval",
    "biologics license application", "marketing authorization",
)

NEGATIVE_TERMS = (
    "clinical trial lawyer",
    "mock trial",
    "trial subscription",
)


def is_candidate(title: str, summary: str) -> bool:
    text = f"{title} {summary}".lower()
    if any(t in text for t in NEGATIVE_TERMS):
        return False
    return any(t in text for t in POSITIVE_TERMS)
