import ai
from models import CompetitionVerification
from ai import (
    _best_search_results,
    _gemini_models,
    _is_transient_gemini_error,
    _search_query,
    _tavily_headers,
    _tavily_only_verification,
    _validated_official_url,
)


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


def test_gemini_model_fallback_order(monkeypatch):
    monkeypatch.setattr(ai, "GEMINI_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr(
        ai,
        "GEMINI_FALLBACK_MODELS",
        "gemini-3.7-flash, gemini-3.6-flash,gemini-3.7-flash",
    )
    assert _gemini_models() == [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
    ]


def test_gemini_transient_error_detection():
    assert _is_transient_gemini_error(
        RuntimeError("503 UNAVAILABLE: This model is currently experiencing high demand")
    )
    assert _is_transient_gemini_error(RuntimeError("429 RESOURCE_EXHAUSTED"))
    assert not _is_transient_gemini_error(RuntimeError("400 invalid API key"))


def test_groq_only_fallback_when_gemini_unconfigured(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")

    expected = CompetitionVerification(
        competition_name="Example Contest",
        status="open",
        confidence=0.9,
    )

    monkeypatch.setattr(ai, "_groq_structured", lambda prompt, schema: expected)

    result = ai._gemini_structured("verify this", CompetitionVerification)
    assert result.competition_name == "Example Contest"
    assert result.status == "open"


def test_tavily_only_verification_without_ai(monkeypatch):
    fake = {
        "answer": (
            "The Example Essay Contest is currently open. "
            "The deadline is October 31, 2026. "
            "There is no entry fee. "
            "Eligible applicants are high school students. "
            "Winners receive a $1,000 cash prize."
        ),
        "results": [
            {
                "title": "Official Example Essay Contest",
                "url": "https://examplecontest.org/rules",
                "content": "Official rules and deadline information.",
                "score": 0.95,
            }
        ],
    }
    monkeypatch.setattr(ai, "_tavily_search_data", lambda record, include_answer=False: fake)
    record = {
        "competition_name": "Example Essay Contest",
        "import_title": "Example Essay Contest",
        "local_kind": "competition",
    }

    result, sources = _tavily_only_verification(record)
    assert result.competition_name == "Example Essay Contest"
    assert result.deadline == "2026-10-31"
    assert result.status == "open"
    assert result.entry_fee is not None
    assert result.eligibility is not None
    assert result.prize is not None
    assert result.confidence == 0.45
    assert sources[0]["url"] == "https://examplecontest.org/rules"
