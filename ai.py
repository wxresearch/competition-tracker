from __future__ import annotations

import json
import os
from datetime import date
from typing import Any
from urllib.parse import urlparse

import httpx
from google import genai

from models import CompetitionExtraction, CompetitionVerification

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
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


def _gemini_structured(prompt: str, schema: type[Any]) -> Any:
    client = _gemini_client()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_json_schema": schema.model_json_schema(),
        },
    )
    if not response.text:
        raise RuntimeError("Gemini returned no structured result.")
    try:
        return schema.model_validate_json(response.text)
    except Exception as exc:
        raise RuntimeError(f"Could not parse Gemini result: {exc}") from exc


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


def _tavily_headers() -> dict[str, str]:
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        raise RuntimeError(
            "TAVILY_API_KEY is missing. Create a free Tavily API key and add it to .env."
        )
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


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


def _tavily_search(record: dict[str, Any]) -> list[dict[str, Any]]:
    payload = {
        "query": _search_query(record),
        "topic": "general",
        "search_depth": "advanced",
        "max_results": 8,
        "include_answer": False,
        "include_raw_content": False,
    }

    with httpx.Client(timeout=35.0, follow_redirects=True) as client:
        response = client.post(TAVILY_SEARCH_URL, headers=_tavily_headers(), json=payload)
        response.raise_for_status()
        data = response.json()

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
        with httpx.Client(timeout=45.0, follow_redirects=True) as client:
            response = client.post(TAVILY_EXTRACT_URL, headers=_tavily_headers(), json=payload)
            response.raise_for_status()
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


def verify_competition(
    record: dict[str, Any],
) -> tuple[CompetitionVerification, list[dict[str, str]]]:
    today = date.today().isoformat()
    web_context, sources = _source_bundle(record)

    prompt = f"""
Today is {today}.

Verify the SINGLE opportunity below using ONLY the supplied live web results.
Tavily retrieved these pages immediately before this prompt.

Rules:
1. Prefer the organizer's official website, official rules, official application
   page, or official announcement.
2. Treat blogs, social posts, directories, and aggregators only as discovery
   sources. Do not let them override an official source.
3. Find the CURRENT or NEXT relevant cycle. Never copy an old Instagram deadline
   into the current cycle unless a current official source confirms it.
4. If the annual event exists but the current/next cycle has not been announced,
   say that in notes and leave the deadline unknown rather than guessing.
5. If sources conflict, explain the conflict briefly and lower confidence.
6. official_url should be the best official page among the supplied URLs.
7. status must be one of: open, upcoming, closed, unclear.

Determine when supported:
- exact opportunity name and organizer
- category
- current/next deadline
- entry fee/cost
- prize
- eligibility
- key requirements
- official URL
- open/upcoming/closed/unclear status

OPPORTUNITY RECORD:
{_record_context(record)}

LIVE WEB SOURCES:
{web_context}
"""

    parsed = _gemini_structured(prompt, CompetitionVerification)

    # Only expose URLs that were actually retrieved by Tavily. Put the model's
    # selected official URL first when it matches one of the fetched sources.
    validated_url = _validated_official_url(parsed.official_url, sources)
    if parsed.official_url and not validated_url:
        extra = " The model's proposed official URL was discarded because Tavily did not retrieve that domain."
        parsed.notes = ((parsed.notes or "").rstrip() + extra).strip()
    parsed.official_url = validated_url

    if validated_url:
        sources.sort(key=lambda s: 0 if s["url"] == validated_url else 1)

    return parsed, sources
