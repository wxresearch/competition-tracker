from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from google import genai

from models import CompetitionExtraction, CompetitionVerification

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_FALLBACK_MODELS = os.getenv(
    "GEMINI_FALLBACK_MODELS",
    "gemini-3.7-flash,gemini-3.6-flash",
)
GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"


def _gemini_client() -> genai.Client:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Create a free Gemini API key in Google AI Studio and add it to .env."
        )
    return genai.Client(api_key=key)


def _record_context(record: dict[str, Any]) -> str:
    return f"""
Imported title: {record.get('import_title') or ''}
Imported text/caption/notes:
{record.get('raw_text') or ''}
Instagram source: {record.get('instagram_url') or ''}
Imported official URL: {record.get('imported_official_url') or ''}
Current stored opportunity name: {record.get('competition_name') or ''}
Source account: {record.get('owner_name') or record.get('owner_username') or ''}
""".strip()


def _gemini_models() -> list[str]:
    models: list[str] = []
    for model in [GEMINI_MODEL, *GEMINI_FALLBACK_MODELS.split(",")]:
        clean = model.strip()
        if clean and clean not in models:
            models.append(clean)
    return models


def _is_transient_gemini_error(exc: Exception) -> bool:
    message = str(exc).lower()
    transient_markers = (
        "503",
        "unavailable",
        "high demand",
        "temporarily",
        "service unavailable",
        "429",
        "resource_exhausted",
        "rate limit",
    )
    return any(marker in message for marker in transient_markers)


def _groq_key() -> str:
    return (os.getenv("GROQ_API_KEY") or "").strip()


def _groq_structured(prompt: str, schema: type[Any]) -> Any:
    key = _groq_key()
    if not key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return only JSON matching the supplied JSON schema. "
                    "Do not include markdown or commentary outside the JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "opportunity_result",
                "strict": False,
                "schema": schema.model_json_schema(),
            },
        },
    }

    with httpx.Client(timeout=45.0, follow_redirects=True) as client:
        response = client.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    try:
        text = data["choices"][0]["message"]["content"]
        return schema.model_validate_json(text)
    except Exception as exc:
        raise RuntimeError(f"Could not parse Groq structured result: {exc}") from exc


def _gemini_structured(prompt: str, schema: type[Any]) -> Any:
    errors: list[str] = []
    gemini_key = (os.getenv("GEMINI_API_KEY") or "").strip()

    if gemini_key:
        client = _gemini_client()
        for model in _gemini_models():
            try:
                response = client.models.generate_content(
                model=model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": schema.model_json_schema(),
                },
            )
                if not response.text:
                    raise RuntimeError(f"{model} returned no structured result.")
                try:
                    return schema.model_validate_json(response.text)
                except Exception as exc:
                    raise RuntimeError(
                        f"Could not parse structured result from {model}: {exc}"
                    ) from exc
            except Exception as exc:
                errors.append(f"{model}: {exc}")
                if not _is_transient_gemini_error(exc):
                    raise
                # The Google SDK already retries transient failures internally.
                # If that model is still overloaded after its retries, move on
                # to the next stable Flash model.
                continue
    else:
        errors.append("Gemini: not configured")

    # Google can occasionally be saturated across multiple Flash variants.
    # If the user configured a free Groq key, use Groq as a second provider
    # rather than making them retry the same overloaded backend.
    if _groq_key():
        try:
            return _groq_structured(prompt, schema)
        except Exception as exc:
            errors.append(f"{GROQ_MODEL}: {exc}")

    suffix = (
        " Configure GROQ_API_KEY to enable the free Groq backup provider."
        if not _groq_key()
        else ""
    )
    raise RuntimeError(
        "All configured free AI providers were unavailable or rate-limited. "
        + " | ".join(errors)
        + suffix
    )


def extract_competition(record: dict[str, Any]) -> CompetitionExtraction:
    prompt = f"""
You extract structured information about a SINGLE named student opportunity.

Use ONLY the imported material below. The record has already been split from an
Instagram source post, so focus only on the named opportunity in
"Current stored opportunity name". Do not merge facts from other opportunities
mentioned elsewhere in the caption.

Do not invent missing facts. If a date is ambiguous, keep deadline null and put
the wording in deadline_text. Normalize a precise deadline to YYYY-MM-DD only when
the year is supported by the source. Preserve currencies and fee conditions.
If the extracted child record is clearly not a real opportunity, set
is_competition=false.

{_record_context(record)}
"""
    return _gemini_structured(prompt, CompetitionExtraction)


def _tavily_key() -> str:
    return (os.getenv("TAVILY_API_KEY") or "").strip()


def _tavily_headers(use_keyless: bool = False) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = _tavily_key()

    if use_keyless or not key:
        headers["X-Tavily-Access-Mode"] = "keyless"
        return headers

    if not key.startswith("tvly-"):
        raise RuntimeError(
            "TAVILY_API_KEY does not look like a Tavily API key. "
            "It should begin with 'tvly-'. Check your .env file."
        )

    headers["Authorization"] = f"Bearer {key}"
    return headers


def _tavily_post(url: str, payload: dict[str, Any], timeout: float) -> httpx.Response:
    """
    Use the user's Tavily key when available. If Tavily rejects it with 401,
    retry once in Tavily's supported keyless mode so free verification can
    still work under the shared keyless rate limit.
    """
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.post(url, headers=_tavily_headers(), json=payload)

        if response.status_code == 401 and _tavily_key():
            response = client.post(
                url,
                headers=_tavily_headers(use_keyless=True),
                json=payload,
            )

        response.raise_for_status()
        return response


def _search_query(record: dict[str, Any]) -> str:
    name = (record.get("competition_name") or record.get("import_title") or "").strip()
    organizer = (record.get("organizer") or "").strip()
    parts = [f'"{name}"']
    if organizer:
        parts.append(f'"{organizer}"')
    year = date.today().year
    parts.append(
        f"official rules application deadline eligibility entry fee prize current cycle {year} {year + 1}"
    )
    return " ".join(parts)


def _tavily_search_data(
    record: dict[str, Any],
    include_answer: bool | str = False,
    search_depth: str = "basic",
    max_results: int = 5,
) -> dict[str, Any]:
    payload = {
        "query": _search_query(record),
        "topic": "general",
        "search_depth": search_depth,
        "max_results": max_results,
        "include_answer": include_answer,
        "include_raw_content": False,
    }

    response = _tavily_post(TAVILY_SEARCH_URL, payload, timeout=20.0)
    return response.json()


def _tavily_search(record: dict[str, Any]) -> list[dict[str, Any]]:
    data = _tavily_search_data(record, include_answer=False)
    results = data.get("results") or []
    return [r for r in results if isinstance(r, dict) and r.get("url")]


def _source_rank(result: dict[str, Any], record: dict[str, Any]) -> tuple[int, float]:
    url = str(result.get("url") or "")
    host = urlparse(url).netloc.lower().removeprefix("www.")
    path = urlparse(url).path.lower()

    bad_hosts = (
        "instagram.com", "facebook.com", "tiktok.com", "youtube.com",
        "reddit.com", "pinterest.com", "linkedin.com",
    )
    bad_terms = (
        "medium.com", "substack.com", "blogspot.", "scholarshipowl.",
        "scholarships.com", "niche.com",
    )

    penalty = 0
    if any(host.endswith(h) for h in bad_hosts):
        penalty += 100
    if any(term in host for term in bad_terms):
        penalty += 40

    bonus = 0
    if host.endswith(".gov") or host.endswith(".edu"):
        bonus += 15

    name = (record.get("competition_name") or record.get("import_title") or "").lower()
    meaningful = [
        token for token in name.replace("-", " ").split()
        if len(token) >= 5 and token not in {"competition", "contest", "challenge", "scholarship", "award"}
    ]
    haystack = f"{host}{path}"
    bonus += min(20, sum(4 for token in meaningful if token in haystack))

    score = float(result.get("score") or 0)
    return (penalty - bonus, -score)


def _best_search_results(
    results: list[dict[str, Any]],
    record: dict[str, Any],
    limit: int = 6,
) -> list[dict[str, Any]]:
    return sorted(results, key=lambda r: _source_rank(r, record))[:limit]


def _tavily_extract(urls: list[str]) -> dict[str, str]:
    if not urls:
        return {}

    payload = {
        "urls": urls[:5],
        "extract_depth": "basic",
        "include_images": False,
    }

    try:
        response = _tavily_post(TAVILY_EXTRACT_URL, payload, timeout=45.0)
        data = response.json()
    except Exception:
        # Search snippets are still usable if extraction fails on a site.
        return {}

    extracted: dict[str, str] = {}
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        raw = str(item.get("raw_content") or "")
        if url and raw:
            extracted[url] = raw
    return extracted


def _source_bundle(
    record: dict[str, Any],
) -> tuple[str, list[dict[str, str]]]:
    results = _best_search_results(_tavily_search(record), record)
    if not results:
        raise RuntimeError("Tavily did not find any web results for this opportunity.")

    urls = [str(r["url"]) for r in results[:5]]
    extracted = _tavily_extract(urls)

    sources: list[dict[str, str]] = []
    blocks: list[str] = []

    for index, result in enumerate(results, start=1):
        url = str(result.get("url") or "")
        title = str(result.get("title") or url)
        snippet = str(result.get("content") or "")
        raw = extracted.get(url, "")

        # Keep the Gemini context useful but bounded. Prefer extracted page text
        # when available; Tavily's search snippet is the fallback.
        content = raw[:8000] if raw else snippet[:2500]
        if not content:
            continue

        sources.append({"title": title, "url": url})
        blocks.append(
            f"""SOURCE {index}
Title: {title}
URL: {url}
Content:
{content}
"""
        )

    if not blocks:
        raise RuntimeError("Web results were found, but none contained readable text.")

    return "\n\n".join(blocks), sources


def _canonical_host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _validated_official_url(
    proposed: str | None,
    sources: list[dict[str, str]],
) -> str | None:
    if not proposed:
        return None

    proposed_host = _canonical_host(proposed)
    if not proposed_host:
        return None

    # The exact path may differ because a model can canonicalize a rules URL to
    # the site's main opportunity page. Only accept it when Tavily actually
    # retrieved the same host; then store the retrieved URL, not a generated URL.
    for source in sources:
        if _canonical_host(source.get("url") or "") == proposed_host:
            return source["url"]
    return None


def _sentence_with(text: str, terms: tuple[str, ...]) -> str | None:
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        low = sentence.lower()
        if any(term in low for term in terms):
            cleaned = sentence.strip()
            if cleaned:
                return cleaned
    return None


def _fallback_deadline(text: str) -> tuple[str | None, str | None]:
    iso = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text or "")
    if iso:
        return iso.group(1), iso.group(1)

    month_re = re.compile(
        r"(?i)\b("
        r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
        r"Nov(?:ember)?|Dec(?:ember)?"
        r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b"
    )
    match = month_re.search(text or "")
    if not match:
        return None, None

    deadline_text = match.group(0)
    if not match.group(3):
        return None, deadline_text

    try:
        parsed = datetime.strptime(
            f"{match.group(1)[:3]} {match.group(2)} {match.group(3)}",
            "%b %d %Y",
        )
        return parsed.date().isoformat(), deadline_text
    except ValueError:
        return None, deadline_text


def _tavily_only_verification(
    record: dict[str, Any],
    provider_error: Exception | None = None,
) -> tuple[CompetitionVerification, list[dict[str, str]]]:
    """
    Last-resort verifier that needs no Gemini/Groq key.

    Tavily can synthesize an answer directly from live search results. We keep
    this deliberately conservative: only obvious fields are copied, confidence
    stays low, and the result is explicitly labeled source-only.
    """
    data = _tavily_search_data(
        record,
        include_answer="basic",
        search_depth="basic",
        max_results=5,
    )
    raw_results = [
        r for r in (data.get("results") or [])
        if isinstance(r, dict) and r.get("url")
    ]
    ranked = _best_search_results(raw_results, record)
    if not ranked:
        raise RuntimeError("Tavily source-only verification found no usable web results.")

    answer = str(data.get("answer") or "").strip()
    if not answer:
        answer = " ".join(
            str(r.get("content") or "") for r in ranked[:4]
        ).strip()

    sources = [
        {
            "title": str(r.get("title") or r.get("url")),
            "url": str(r.get("url")),
        }
        for r in ranked[:6]
    ]

    deadline, deadline_text = _fallback_deadline(answer)
    lower = answer.lower()

    status = "unclear"
    if any(term in lower for term in ("currently open", "applications are open", "open now", "registration is open")):
        status = "open"
    elif any(term in lower for term in ("is closed", "applications are closed", "deadline has passed", "submissions are closed")):
        status = "closed"
    elif any(term in lower for term in ("opens on", "will open", "upcoming cycle", "not yet open")):
        status = "upcoming"

    fee = _sentence_with(
        answer,
        ("entry fee", "application fee", "free to enter", "free to apply", "no fee"),
    )
    prize = _sentence_with(
        answer,
        ("prize", "cash award", "cash prize", "winner receives", "winners receive"),
    )
    eligibility = _sentence_with(
        answer,
        ("eligib", "open to students", "open to high school", "applicants must"),
    )
    requirements = _sentence_with(
        answer,
        ("submit ", "submission", "word count", "essay must", "video must", "application requires"),
    )

    note_bits = [
        "Source-only Tavily verification was used because the configured AI providers were unavailable or rate-limited.",
        "Treat extracted fields as lower-confidence until an official page is manually reviewed.",
    ]
    if answer:
        note_bits.append(f"Tavily answer: {answer[:1600]}")
    if provider_error:
        note_bits.append("AI provider fallback was attempted first.")

    best_url = None
    if ranked and _source_rank(ranked[0], record)[0] < 0:
        best_url = str(ranked[0].get("url") or "") or None

    result = CompetitionVerification(
        competition_name=record.get("competition_name") or record.get("import_title"),
        organizer=record.get("organizer"),
        category=record.get("category") or record.get("local_kind"),
        deadline=deadline,
        deadline_text=deadline_text,
        entry_fee=fee,
        prize=prize,
        eligibility=eligibility,
        requirements=requirements,
        official_url=best_url,
        status=status,
        notes=" ".join(note_bits),
        confidence=0.45,
    )
    return result, sources


def verify_competition(
    record: dict[str, Any],
) -> tuple[CompetitionVerification, list[dict[str, str]]]:
    """
    Fast default verification path.

    One Tavily request does the live search and synthesized answer. This avoids
    waiting on Gemini/Groq provider retries for every click. AI providers remain
    available for the separate "AI extract details" action.
    """
    return _tavily_only_verification(record)
