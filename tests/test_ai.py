from ai import _best_search_results, _search_query, _validated_official_url


def test_search_query_uses_name_and_current_cycle():
    record = {"competition_name": "Conrad Challenge"}
    query = _search_query(record)
    assert '"Conrad Challenge"' in query
    assert "official rules" in query
    assert "current cycle" in query


def test_source_ranking_demotes_social_media():
    record = {"competition_name": "Conrad Challenge"}
    results = [
        {
            "title": "Instagram post",
            "url": "https://www.instagram.com/p/example/",
            "score": 0.99,
        },
        {
            "title": "Conrad Challenge",
            "url": "https://www.conradchallenge.org/rules/",
            "score": 0.80,
        },
    ]
    ranked = _best_search_results(results, record)
    assert ranked[0]["url"].startswith("https://www.conradchallenge.org")


def test_official_url_must_match_retrieved_domain():
    sources = [
        {"title": "Official", "url": "https://example.org/competition/rules"},
        {"title": "Other", "url": "https://another.example/page"},
    ]

    assert (
        _validated_official_url("https://example.org/competition", sources)
        == "https://example.org/competition/rules"
    )
    assert _validated_official_url("https://invented.example/rules", sources) is None
