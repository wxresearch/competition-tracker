from ai import _best_search_results, _search_query, _tavily_headers, _validated_official_url


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


def test_tavily_key_header(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    headers = _tavily_headers()
    assert headers["Authorization"] == "Bearer tvly-test-key"
    assert "X-Tavily-Access-Mode" not in headers


def test_tavily_keyless_headers(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    headers = _tavily_headers()
    assert headers["X-Tavily-Access-Mode"] == "keyless"
    assert "Authorization" not in headers


def test_tavily_rejects_obviously_wrong_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "not-a-tavily-key")
    try:
        _tavily_headers()
    except RuntimeError as exc:
        assert "tvly-" in str(exc)
    else:
        raise AssertionError("Expected invalid Tavily key to be rejected")
