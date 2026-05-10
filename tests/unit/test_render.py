from clinical_news.synthesize.render import render


def test_render_replaces_refs_with_footnotes():
    synthesis = {
        "headline": "Weekly brief",
        "deck": "subhead",
        "body_markdown": "Trial results [ref:art_001]. Approval news [ref:art_002].",
    }
    article_index = {
        "art_001": {"source_id": "guardian-health", "title": "Headline 1",
                    "url": "https://example.com/1", "published_at": "2026-05-01T00:00:00Z"},
        "art_002": {"source_id": "stat-news", "title": "Headline 2",
                    "url": "https://example.com/2", "published_at": "2026-05-02T00:00:00Z"},
    }
    md = render(synthesis, article_index)
    assert "[^1]" in md
    assert "[^2]" in md
    assert "Headline 1" in md
    assert "Headline 2" in md
    assert "[ref:art_001]" not in md


def test_render_skips_unknown_refs():
    synthesis = {
        "headline": "x", "deck": "",
        "body_markdown": "Known [ref:art_001]. Unknown [ref:art_999].",
    }
    article_index = {
        "art_001": {"source_id": "x", "title": "t",
                    "url": "https://example.com/1", "published_at": "2026-01-01T00:00:00Z"},
    }
    md = render(synthesis, article_index)
    assert "[^1]" in md
    assert "[ref:art_999]" in md  # left unchanged because not in index
