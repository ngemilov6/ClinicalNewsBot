"""Pool of Gemini API keys with persistent cooldowns.

When a key returns 429, we mark it cool for the duration extracted from the
error's `retry_delay`. If the error mentions the daily-cap exhaustion, we cool
until the next midnight Pacific (Google free-tier resets there).

State persists to ``app_data/keypool_state.json`` so cooldowns outlive the
process — if one run burns through a daily cap, the next won't waste retries
on the same key.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

PACIFIC_OFFSET = timedelta(hours=-8)  # Pacific Standard Time; quotas reset at PST midnight
STATE_PATH_DEFAULT = Path("app_data/keypool_state.json")


class AllKeysExhausted(RuntimeError):
    pass


@dataclass
class _KeyState:
    fingerprint: str  # last 4 chars, for log lines that don't leak the key
    cooldown_until: float = 0.0  # unix epoch seconds
    consecutive_failures: int = 0
    permanently_bad: bool = False

    def to_dict(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "cooldown_until": self.cooldown_until,
            "consecutive_failures": self.consecutive_failures,
            "permanently_bad": self.permanently_bad,
        }


class KeyPool:
    """Thread-safe pool. One process, one pool — created lazily by ``get()``."""

    def __init__(self, keys: list[str], state_path: Path = STATE_PATH_DEFAULT) -> None:
        if not keys:
            raise RuntimeError(
                "no Gemini keys configured; set GEMINI_API_KEY or GEMINI_API_KEYS"
            )
        self._keys = keys
        self._state_path = state_path
        self._lock = threading.Lock()
        self._states: dict[str, _KeyState] = {
            k: _KeyState(fingerprint=_fp(k)) for k in keys
        }
        self._load_state()

    # ---- public API --------------------------------------------------------

    def next_key(self) -> str:
        with self._lock:
            now = time.time()
            for k in self._keys:
                st = self._states[k]
                if st.permanently_bad:
                    continue
                if st.cooldown_until <= now:
                    return k
            # nothing usable; raise with the soonest-available time.
            soonest = min(
                (s.cooldown_until for s in self._states.values() if not s.permanently_bad),
                default=0.0,
            )
            wait = max(0.0, soonest - now)
            raise AllKeysExhausted(
                f"all {len(self._keys)} keys cooling down; next available in {wait:.0f}s"
            )

    def report_success(self, key: str) -> None:
        with self._lock:
            st = self._states.get(key)
            if st is None:
                return
            st.consecutive_failures = 0
            self._save_state_locked()

    def report_failure(self, key: str, exc: BaseException) -> None:
        msg = str(exc)
        cool_for = _parse_cooldown_seconds(msg)
        is_daily = "PerDay" in msg or "PerDayPerProjectPerModel" in msg
        is_auth = "401" in msg[:80] or "403" in msg[:80] or "API_KEY_INVALID" in msg

        with self._lock:
            st = self._states.get(key)
            if st is None:
                return
            st.consecutive_failures += 1
            now = time.time()
            if is_auth:
                st.permanently_bad = True
                log.warning("keypool: marking key permanently bad",
                            extra={"key_fp": st.fingerprint, "reason": "auth"})
            elif is_daily:
                st.cooldown_until = max(st.cooldown_until, _next_pst_midnight_epoch(now))
                log.warning("keypool: key hit daily cap",
                            extra={"key_fp": st.fingerprint,
                                   "cool_until": _fmt_epoch(st.cooldown_until)})
            elif cool_for > 0:
                st.cooldown_until = max(st.cooldown_until, now + cool_for)
                log.info("keypool: key cooling down",
                         extra={"key_fp": st.fingerprint, "seconds": cool_for})
            else:
                # unknown error; short cool to break tight loops
                st.cooldown_until = max(st.cooldown_until, now + 30)
                log.info("keypool: key cooling 30s on unknown error",
                         extra={"key_fp": st.fingerprint})
            self._save_state_locked()

    def status(self) -> list[dict]:
        with self._lock:
            now = time.time()
            return [
                {
                    "fingerprint": s.fingerprint,
                    "cooldown_remaining_s": max(0, int(s.cooldown_until - now)),
                    "consecutive_failures": s.consecutive_failures,
                    "permanently_bad": s.permanently_bad,
                }
                for s in self._states.values()
            ]

    # ---- persistence -------------------------------------------------------

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("keypool: failed to load state", extra={"err": str(exc)})
            return
        # keyed by fingerprint so re-ordering keys in env doesn't lose state
        by_fp = {entry["fingerprint"]: entry for entry in data.get("keys", [])}
        for k, st in self._states.items():
            saved = by_fp.get(st.fingerprint)
            if not saved:
                continue
            st.cooldown_until = saved.get("cooldown_until", 0.0)
            st.consecutive_failures = saved.get("consecutive_failures", 0)
            st.permanently_bad = saved.get("permanently_bad", False)

    def _save_state_locked(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            payload = {"keys": [s.to_dict() for s in self._states.values()]}
            tmp.write_text(json.dumps(payload, indent=2))
            tmp.replace(self._state_path)
        except OSError as exc:
            log.warning("keypool: state write failed", extra={"err": str(exc)})


# --- module-level singleton --------------------------------------------------

_pool: KeyPool | None = None
_pool_lock = threading.Lock()


def _read_keys_from_env() -> list[str]:
    multi = os.environ.get("GEMINI_API_KEYS", "")
    keys = [k.strip() for k in multi.split(",") if k.strip()]
    if keys:
        return keys
    single = os.environ.get("GEMINI_API_KEY", "").strip()
    return [single] if single else []


def get() -> KeyPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = KeyPool(_read_keys_from_env())
        return _pool


def reset_for_tests() -> None:
    """Test hook — drops the singleton so the next ``get()`` rereads env."""
    global _pool
    with _pool_lock:
        _pool = None


# --- helpers -----------------------------------------------------------------

_RETRY_DELAY_RE = re.compile(r"retry_delay\s*\{[^}]*seconds:\s*(\d+)", re.IGNORECASE)


def _parse_cooldown_seconds(msg: str) -> float:
    m = _RETRY_DELAY_RE.search(msg)
    if m:
        return float(m.group(1))
    # also catch "Please retry in 23.15s" form
    m2 = re.search(r"retry in\s+([0-9]+(?:\.[0-9]+)?)\s*s", msg, re.IGNORECASE)
    if m2:
        return float(m2.group(1))
    return 0.0


def _next_pst_midnight_epoch(now: float) -> float:
    now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
    pst = now_dt + PACIFIC_OFFSET  # naive PST
    midnight_pst_naive = (pst + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    midnight_utc = midnight_pst_naive - PACIFIC_OFFSET
    return midnight_utc.replace(tzinfo=timezone.utc).timestamp()


def _fp(key: str) -> str:
    return f"***{key[-4:]}" if len(key) >= 4 else "***"


def _fmt_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
