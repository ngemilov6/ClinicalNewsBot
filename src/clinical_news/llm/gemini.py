"""Thin Gemini client: embeddings + generation, multi-key pool, per-key throttle."""
from __future__ import annotations

import logging
import os
import threading
import time
import warnings

# Silence the google.generativeai sunset warning — the SDK still works; the
# migration to google-genai is a separate task.
warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")

import numpy as np

from clinical_news.llm import keypool

log = logging.getLogger(__name__)

# Free tier on flash models is ~15 RPM per project. We keep one global throttle
# (the limit is per-project, not per-key, when keys live in the same project).
# When you run separate Google Cloud projects per key, you can lower this.
_MIN_INTERVAL_S = float(os.environ.get("GEMINI_MIN_INTERVAL_S", "5.0"))
_throttle_lock = threading.Lock()
_last_call_at = 0.0


def _throttle() -> None:
    global _last_call_at
    with _throttle_lock:
        now = time.monotonic()
        wait = _MIN_INTERVAL_S - (now - _last_call_at)
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.monotonic()


GENERATION_MODEL = os.environ.get("GEMINI_GEN_MODEL", "gemini-flash-latest")
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 768  # gemini-embedding-001 supports MRL truncation; we ask for 768
MAX_OUTPUT_TOKENS = int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", "8000"))
MAX_KEY_ATTEMPTS = 5  # safety cap on rotation cycles per single call


def _configure_with(key: str):
    import google.generativeai as genai
    genai.configure(api_key=key)
    return genai


def _is_quota_error(exc: BaseException) -> bool:
    msg = str(exc)
    return "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower()


def _is_auth_error(exc: BaseException) -> bool:
    msg = str(exc)[:200]
    return "401" in msg or "403" in msg or "API_KEY_INVALID" in msg


def _call_with_rotation(fn, *, purpose: str):
    """Run ``fn(genai)`` with a key from the pool, rotating on quota/auth errors."""
    pool = keypool.get()
    last_exc: BaseException | None = None
    for attempt in range(MAX_KEY_ATTEMPTS):
        try:
            key = pool.next_key()
        except keypool.AllKeysExhausted as exc:
            raise exc from last_exc
        _throttle()
        try:
            genai = _configure_with(key)
            result = fn(genai)
            pool.report_success(key)
            return result
        except Exception as exc:
            last_exc = exc
            if _is_quota_error(exc) or _is_auth_error(exc):
                log.warning("keypool: rotating after error",
                            extra={"purpose": purpose, "attempt": attempt + 1,
                                   "err": str(exc)[:200]})
                pool.report_failure(key, exc)
                continue
            raise
    raise RuntimeError(
        f"gemini {purpose}: exhausted {MAX_KEY_ATTEMPTS} key attempts"
    ) from last_exc


def embed(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> np.ndarray:
    def _do(genai):
        resp = genai.embed_content(
            model=f"models/{EMBEDDING_MODEL}",
            content=text,
            task_type=task_type,
            output_dimensionality=EMBEDDING_DIM,
        )
        return np.asarray(resp["embedding"], dtype=np.float32)

    return _call_with_rotation(_do, purpose="embed")


def embed_batch(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[np.ndarray]:
    return [embed(t, task_type=task_type) for t in texts]


def generate_json(prompt: str, model: str = GENERATION_MODEL,
                  max_output_tokens: int | None = None) -> str:
    cfg = {
        "response_mime_type": "application/json",
        "max_output_tokens": max_output_tokens or MAX_OUTPUT_TOKENS,
    }

    def _do(genai):
        m = genai.GenerativeModel(model)
        resp = m.generate_content(prompt, generation_config=cfg)
        return resp.text or ""

    return _call_with_rotation(_do, purpose="generate")
