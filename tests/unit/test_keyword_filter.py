from clinical_news.filter.keyword import is_candidate


def test_phase_3_trial_passes():
    assert is_candidate(
        "Phase 3 trial of new Alzheimer's drug shows benefit",
        "Donanemab slowed decline by 35%.",
    )


def test_negative_term_rejects_subscription_ad():
    assert not is_candidate(
        "Subscribe for unlimited access to STAT Plus",
        "Premium analysis on biotech and pharma. trial subscription benefits included.",
    )


def test_mock_trial_rejected():
    assert not is_candidate(
        "How a mock trial works in a high school debate club",
        "Students argue cases in front of judges.",
    )


def test_unrelated_news_rejected():
    assert not is_candidate(
        "Climbing accident on Mount Snowdon leaves three injured",
        "Mountain rescue teams responded.",
    )


def test_fda_approval_passes():
    assert is_candidate(
        "FDA approves first gene therapy for sickle cell disease",
        "Cleared after pivotal trial.",
    )
