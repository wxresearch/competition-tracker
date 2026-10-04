from __future__ import annotations

import os
from datetime import date
from typing import Any

from openai import OpenAI

from models import CompetitionExtraction, CompetitionVerification

EXTRACT_MODEL = os.getenv("OPENAI_EXTRACT_MODEL", "gpt-6-luna")
VERIFY_MODEL = os.getenv("OPENAI_VERIFY_MODEL", "gpt-6.1-sol")


def _client() -> OpenAI:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is missing. Add it to your .env file.")
    return OpenAI()


def _record_context(record: dict[str, Any]) -> str:
    return f"""
Imported title: {record.get('import_title') or ''}
Imported text/caption/notes:
{record.get('raw_text') or ''}
Instagram URL: {record.get('instagram_url') or ''}
Imported official URL: {record.get('imported_official_url') or ''}
Current stored name: {record.get('competition_name') or ''}
""".strip()


def extract_competition(record: dict[str, Any]) -> CompetitionExtraction:
    prompt = f"""
You extract structured information about competitions, contests, essay competitions,
scholarships, olympiads, research challenges, hackathons, art contests, and similar
student opportunities.

Use ONLY the imported material below. Do not invent missing facts. If a date is
ambiguous, keep deadline null and put the wording in deadline_text. Normalize a
precise deadline to YYYY-MM-DD. Preserve currencies and fee conditions in plain text.
For prize, summarize the most important prize information. If this is not actually a
competition/opportunity, set is_competition=false.

{_record_context(record)}
"""
    client = _client()
    response = client.responses.parse(
        model=EXTRACT_MODEL,
        input=prompt,
        text_format=CompetitionExtraction,
    )
    parsed = response.output_parsed
    if parsed is None:
        raise RuntimeError("The model did not return a structured extraction.")
    return parsed


def _json_schema() -> dict[str, Any]:
    schema = CompetitionVerification.model_json_schema()
    return {
        "type": "json_schema",
        "name": "competition_verification",
        "schema": schema,
        "strict": False,
    }


def _collect_citations(response: Any) -> list[dict[str, str]]:
    dumped = response.model_dump() if hasattr(response, "model_dump") else {}
    seen: set[str] = set()
    sources: list[dict[str, str]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "url_citation":
                url = node.get("url") or (node.get("url_citation") or {}).get("url")
                title = node.get("title") or (node.get("url_citation") or {}).get("title") or url
                if url and url not in seen:
                    seen.add(url)
                    sources.append({"title": str(title), "url": str(url)})
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(dumped)
    return sources


def verify_competition(record: dict[str, Any]) -> tuple[CompetitionVerification, list[dict[str, str]]]:
    today = date.today().isoformat()
    prompt = f"""
Today is {today}. Verify this competition/opportunity using live web search.

Prioritize the organizer's official website, official rules, official application page,
or official announcement. Do not treat aggregator sites as authoritative when an
official source exists.

Find the CURRENT or NEXT relevant cycle. Be especially careful not to copy an old
Instagram deadline into the current cycle. Determine, when available:
- exact competition name and organizer
- category
- submission/application deadline
- entry fee/cost, including free or conditional fees
- prize(s)
- eligibility
- key submission requirements
- official URL
- whether it is open, upcoming, closed, or unclear as of today

If evidence conflicts, explain it briefly in notes and lower confidence. If the event is
annual but the next cycle has not been announced, say so rather than guessing a date.

Imported record:
{_record_context(record)}
"""

    client = _client()
    response = client.responses.create(
        model=VERIFY_MODEL,
        reasoning={"effort": "low"},
        tools=[{"type": "web_search"}],
        tool_choice="auto",
        input=prompt,
        text={"format": _json_schema()},
    )

    if not response.output_text:
        raise RuntimeError("No verification result was returned.")
    try:
        parsed = CompetitionVerification.model_validate_json(response.output_text)
    except Exception as exc:
        raise RuntimeError(f"Could not parse verification result: {exc}") from exc

    return parsed, _collect_citations(response)
