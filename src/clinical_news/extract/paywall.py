"""Heuristic paywall detection."""
from __future__ import annotations

PAYWALL_PHRASES = (
    "subscribe to continue",
    "create a free account to read",
    "subscribe now to read",
    "this article is for subscribers",
    "sign in to read this article",
    "to continue reading, subscribe",
    "you have reached your free article limit",
)


def is_paywalled(text: str) -> bool:
    if not text:
        return False
    lo = text.lower()
    return any(p in lo for p in PAYWALL_PHRASES)
