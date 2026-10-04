from __future__ import annotations

import csv
import io
import json
from typing import Any


ALIASES = {
    "import_title": ["competition_name", "name", "title", "competition", "event"],
    "raw_text": ["caption", "description", "text", "raw_text", "notes", "content"],
    "instagram_url": ["instagram_url", "instagram", "post_url", "permalink", "url", "link"],
    "imported_official_url": ["official_url", "website", "source_url", "competition_url"],
}


def _pick(record: dict[str, Any], aliases: list[str]) -> str:
    lowered = {str(k).strip().lower(): v for k, v in record.items()}
    for key in aliases:
        value = lowered.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def normalize_record(record: dict[str, Any]) -> dict[str, str]:
    out = {key: _pick(record, aliases) for key, aliases in ALIASES.items()}

    url = out["instagram_url"]
    if url and "instagram.com" not in url.lower():
        if not out["imported_official_url"]:
            out["imported_official_url"] = url
        out["instagram_url"] = ""
    return out


def parse_csv(data: bytes) -> list[dict[str, str]]:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV must have a header row.")
    return [normalize_record(dict(row)) for row in reader]


def _find_record_lists(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                records.append(item)
            else:
                records.extend(_find_record_lists(item))
    elif isinstance(value, dict):
        keys = {str(k).lower() for k in value}
        hints = {alias for group in ALIASES.values() for alias in group}
        if keys & hints:
            records.append(value)
        else:
            for child in value.values():
                records.extend(_find_record_lists(child))
    return records


def parse_json(data: bytes) -> list[dict[str, str]]:
    obj = json.loads(data.decode("utf-8-sig", errors="replace"))
    records = _find_record_lists(obj)
    return [normalize_record(r) for r in records]


def parse_upload(filename: str, data: bytes) -> list[dict[str, str]]:
    lower = filename.lower()
    if lower.endswith(".csv"):
        return parse_csv(data)
    if lower.endswith(".json"):
        return parse_json(data)
    raise ValueError("Please upload a .csv or .json file.")
