from clinical_news.extract.paywall import is_paywalled


def test_subscribe_phrase_detected():
    assert is_paywalled("Read more by subscribing. Subscribe to continue reading.")


def test_clean_article_not_flagged():
    assert not is_paywalled(
        "The trial enrolled 1,500 patients across multiple centers and met its primary endpoint."
    )


def test_account_creation_phrase_detected():
    assert is_paywalled("Create a free account to read this article.")
