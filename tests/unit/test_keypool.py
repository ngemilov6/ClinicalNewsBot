import time
from pathlib import Path

import pytest

from clinical_news.llm import keypool


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(keypool, "STATE_PATH_DEFAULT", tmp_path / "state.json")
    keypool.reset_for_tests()
    yield
    keypool.reset_for_tests()


def _pool(*keys: str, state_path: Path | None = None) -> keypool.KeyPool:
    return keypool.KeyPool(list(keys), state_path=state_path or keypool.STATE_PATH_DEFAULT)


def test_returns_first_available_key():
    p = _pool("AAAA1111", "BBBB2222")
    assert p.next_key() == "AAAA1111"


def test_rotates_after_429(tmp_path: Path):
    p = _pool("AAAA1111", "BBBB2222", state_path=tmp_path / "s.json")
    p.report_failure("AAAA1111", RuntimeError("429 retry_delay { seconds: 30 }"))
    assert p.next_key() == "BBBB2222"


def test_auth_error_marks_key_permanently_bad():
    p = _pool("AAAA1111", "BBBB2222")
    p.report_failure("AAAA1111", RuntimeError("403 API_KEY_INVALID"))
    assert p.next_key() == "BBBB2222"
    # cooldown elapsing won't bring it back
    p._states["AAAA1111"].cooldown_until = 0
    assert p.next_key() == "BBBB2222"


def test_all_exhausted_raises():
    p = _pool("AAAA1111")
    p.report_failure("AAAA1111", RuntimeError("429 retry_delay { seconds: 60 }"))
    with pytest.raises(keypool.AllKeysExhausted):
        p.next_key()


def test_cooldown_elapses():
    p = _pool("AAAA1111")
    p._states["AAAA1111"].cooldown_until = time.time() - 1
    assert p.next_key() == "AAAA1111"


def test_state_persists_across_instances(tmp_path: Path):
    sp = tmp_path / "state.json"
    p1 = _pool("AAAA1111", "BBBB2222", state_path=sp)
    p1.report_failure("AAAA1111", RuntimeError("429 retry_delay { seconds: 600 }"))
    assert sp.exists()
    # New instance with same keys: cooldown survives
    p2 = _pool("AAAA1111", "BBBB2222", state_path=sp)
    assert p2.next_key() == "BBBB2222"


def test_daily_cap_cools_until_pst_midnight():
    p = _pool("AAAA1111", "BBBB2222")
    msg = (
        "429 quota exceeded "
        'quota_id: "GenerateRequestsPerDayPerProjectPerModel-FreeTier" '
        "retry_delay { seconds: 5 }"
    )
    p.report_failure("AAAA1111", RuntimeError(msg))
    cool = p._states["AAAA1111"].cooldown_until
    # Daily cap should cool for hours, not seconds.
    assert cool - time.time() > 3600


def test_success_resets_failure_counter():
    p = _pool("AAAA1111")
    p.report_failure("AAAA1111", RuntimeError("429 retry_delay { seconds: 1 }"))
    assert p._states["AAAA1111"].consecutive_failures == 1
    p.report_success("AAAA1111")
    assert p._states["AAAA1111"].consecutive_failures == 0


def test_empty_pool_raises():
    with pytest.raises(RuntimeError, match="no Gemini keys"):
        keypool.KeyPool([])


def test_get_reads_GEMINI_API_KEYS(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", "alpha_key1, beta_key2 ,gamma_key3")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    keypool.reset_for_tests()
    pool = keypool.get()
    assert [s["fingerprint"] for s in pool.status()] == ["***key1", "***key2", "***key3"]


def test_get_falls_back_to_GEMINI_API_KEY(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "solo_key_value")
    keypool.reset_for_tests()
    pool = keypool.get()
    assert len(pool.status()) == 1
