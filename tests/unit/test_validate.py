from clinical_news.synthesize.validate import validate


def _synth(body: str, citations: list[str]) -> dict:
    return {"body_markdown": body, "citations_used": citations}


def test_valid_synthesis_passes():
    body = (
        "First paragraph reports trial results [ref:art_001].\n\n"
        "Second paragraph notes regulatory action [ref:art_002].\n\n"
        "Third paragraph contextualizes [ref:art_001]."
    )
    result = validate(_synth(body, ["art_001", "art_002"]), {"art_001", "art_002"})
    assert result.ok
    assert result.citation_coverage == 1.0


def test_unresolved_ref_fails():
    body = "Claim about something [ref:art_999]."
    result = validate(_synth(body, ["art_999"]), {"art_001"})
    assert not result.ok
    assert result.unresolved == ["art_999"]


def test_low_coverage_fails():
    body = (
        "Cited [ref:art_001].\n\nUncited.\n\nUncited.\n\nUncited.\n\nUncited."
    )
    result = validate(_synth(body, ["art_001"]), {"art_001"})
    assert not result.ok
    assert result.citation_coverage < 0.5


def test_long_quote_fails():
    body = (
        'Trial result reported [ref:art_001]. The investigators said '
        '"this is an extremely long quote that goes well past the fifteen-word '
        'limit and should fail validation" [ref:art_001].'
    )
    result = validate(_synth(body, ["art_001"]), {"art_001"})
    assert not result.ok
    assert result.quote_violations
