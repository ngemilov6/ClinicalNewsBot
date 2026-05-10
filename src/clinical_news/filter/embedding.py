"""Anchor-based embedding gate. Ranks candidates by max cosine similarity
to a small set of anchor sentences describing what 'relevant' looks like.
"""
from __future__ import annotations

import logging

import numpy as np

from clinical_news.llm import gemini

log = logging.getLogger(__name__)

ANCHORS = (
    "A clinical trial reported new efficacy or safety results.",
    "An FDA or EMA drug approval was announced.",
    "A peer-reviewed clinical study or randomized controlled trial was published.",
    "A pharmaceutical company released interim trial data or primary endpoint results.",
    "A new investigational therapy entered or completed a phase II or phase III trial.",
)

DEFAULT_THRESHOLD = 0.55

_anchor_cache: np.ndarray | None = None


def _anchor_matrix() -> np.ndarray:
    global _anchor_cache
    if _anchor_cache is None:
        embs = [gemini.embed(a, task_type="RETRIEVAL_QUERY") for a in ANCHORS]
        m = np.vstack(embs)
        m /= np.linalg.norm(m, axis=1, keepdims=True)
        _anchor_cache = m
    return _anchor_cache


def score(text: str) -> tuple[float, np.ndarray]:
    """Return (max cosine similarity to anchors, the embedding itself)."""
    emb = gemini.embed(text, task_type="RETRIEVAL_DOCUMENT")
    norm = emb / (np.linalg.norm(emb) + 1e-9)
    sims = _anchor_matrix() @ norm
    return float(sims.max()), emb


def is_relevant(text: str, threshold: float = DEFAULT_THRESHOLD) -> tuple[bool, float, np.ndarray]:
    s, emb = score(text)
    return (s >= threshold, s, emb)


def serialize(emb: np.ndarray) -> bytes:
    return emb.astype(np.float32).tobytes()


def deserialize(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
