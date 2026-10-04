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


def _instagram_href_from_map(record: dict[str, Any]) -> str:
    """Extract a saved-post URL from Instagram export string_map_data."""
    smd = record.get("string_map_data")
    if not isinstance(smd, dict):
        return ""

    # Typical saved_posts.json shape:
    # {"title": "...", "string_map_data": {"Saved on": {"href": "...", "timestamp": ...}}}
    for value in smd.values():
        if isinstance(value, dict):
            href = str(value.get("href") or "").strip()
            if "instagram.com" in href.lower():
                return href
    return ""


def _instagram_href_from_list(record: dict[str, Any]) -> str:
    """Extract a URL from Instagram export string_list_data."""
    sld = record.get("string_list_data")
    if not isinstance(sld, list):
        return ""
    for item in sld:
        if isinstance(item, dict):
            href = str(item.get("href") or "").strip()
            if "instagram.com" in href.lower():
                return href
    return ""


def _instagram_value_from_list(record: dict[str, Any]) -> str:
    sld = record.get("string_list_data")
    if not isinstance(sld, list):
        return ""
    for item in sld:
        if isinstance(item, dict):
            value = str(item.get("value") or "").strip()
            if value:
                return value
    return ""


def _instagram_export_record(record: dict[str, Any]) -> dict[str, str] | None:
    """
    Convert an Instagram export entry into our normalized row.

    Supports both common Meta export shapes:
    - string_map_data -> {"Saved on": {"href": ..., "timestamp": ...}}
    - string_list_data -> [{"href": ..., "value": ..., "timestamp": ...}]
    """
    href = _instagram_href_from_map(record) or _instagram_href_from_list(record)
    if not href:
        return None

    title = str(record.get("title") or "").strip()
    value = _instagram_value_from_list(record)

    # Keep whatever descriptive information the export gives us. Instagram JSON
    # often does not include the caption, so title/value may be all we have.
    raw_bits = []
    if title:
        raw_bits.append(f"Instagram export title/collection: {title}")
    if value and value != title:
        raw_bits.append(f"Instagram export value/account: {value}")

    return {
        "import_title": title or value,
        "raw_text": "\n".join(raw_bits),
        "instagram_url": href,
        "imported_official_url": "",
    }


def parse_csv(data: bytes) -> list[dict[str, str]]:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV must have a header row.")
    return [normalize_record(dict(row)) for row in reader]


def _find_records(value: Any) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []

    if isinstance(value, list):
        for item in value:
            records.extend(_find_records(item))
        return records

    if not isinstance(value, dict):
        return records

    # First, recognize Instagram's own export objects.
    instagram_row = _instagram_export_record(value)
    if instagram_row:
        records.append(instagram_row)
        # Do not recurse into the nested string_map/list data or we would
        # duplicate the same saved item.
        return records

    # Then support user-created / generic JSON records.
    keys = {str(k).lower() for k in value}
    hints = {alias for group in ALIASES.values() for alias in group}
    if keys & hints:
        normalized = normalize_record(value)
        if any(normalized.values()):
            records.append(normalized)
            return records

    # Otherwise recurse through wrapper objects such as saved_saved_media.
    for child in value.values():
        records.extend(_find_records(child))

    return records


def parse_json(data: bytes) -> list[dict[str, str]]:
    obj = json.loads(data.decode("utf-8-sig", errors="replace"))
    return _find_records(obj)


def parse_upload(filename: str, data: bytes) -> list[dict[str, str]]:
    lower = filename.lower()
    if lower.endswith(".csv"):
        return parse_csv(data)
    if lower.endswith(".json"):
        return parse_json(data)
    raise ValueError("Please upload a .csv or .json file.")
