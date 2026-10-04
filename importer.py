from __future__ import annotations

import csv
import io
import json
from typing import Any
from urllib.parse import urlparse


ALIASES = {
    "import_title": ["competition_name", "name", "title", "competition", "event"],
    "raw_text": ["caption", "description", "text", "raw_text", "notes", "content"],
    "instagram_url": ["instagram_url", "instagram", "post_url", "permalink", "url", "link"],
    "imported_official_url": ["official_url", "website", "source_url", "competition_url"],
}


def _fix_mojibake(value: Any) -> str:
    """Repair common UTF-8-as-Latin-1 mojibake found in Meta JSON exports."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""

    suspicious = ("Ã", "Â", "â", "ð", "ï")
    if not any(marker in text for marker in suspicious):
        return text

    try:
        repaired = text.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text

    before = sum(text.count(marker) for marker in suspicious)
    after = sum(repaired.count(marker) for marker in suspicious)
    return repaired if after < before else text


def _pick(record: dict[str, Any], aliases: list[str]) -> str:
    lowered = {str(k).strip().lower(): v for k, v in record.items()}
    for key in aliases:
        value = lowered.get(key)
        if value is not None and str(value).strip():
            return _fix_mojibake(value)
    return ""


def _is_instagram_post_url(url: str) -> bool:
    """Only accept actual post/reel/media URLs, not profiles, hashtags, etc."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower().removeprefix("www.")
        path = parsed.path.lower()
    except Exception:
        return False

    if host not in {"instagram.com", "m.instagram.com"}:
        return False

    return (
        path.startswith("/p/")
        or path.startswith("/reel/")
        or path.startswith("/reels/")
        or path.startswith("/tv/")
    )


def normalize_record(record: dict[str, Any]) -> dict[str, str]:
    out = {key: _pick(record, aliases) for key, aliases in ALIASES.items()}

    url = out["instagram_url"]
    if url and not _is_instagram_post_url(url):
        if "instagram.com" not in url.lower() and not out["imported_official_url"]:
            out["imported_official_url"] = url
        out["instagram_url"] = ""
    return out


def _owner_from_label_values(label_values: list[Any]) -> tuple[str, str, str]:
    owner_name = ""
    owner_username = ""
    owner_url = ""

    for item in label_values:
        if not isinstance(item, dict) or str(item.get("title") or "").strip().lower() != "owner":
            continue

        for outer in item.get("dict") or []:
            if not isinstance(outer, dict):
                continue
            for field in outer.get("dict") or []:
                if not isinstance(field, dict):
                    continue
                label = str(field.get("label") or "").strip().lower()
                value = _fix_mojibake(field.get("value"))
                if label == "name" and value and not owner_name:
                    owner_name = value
                elif label == "username" and value and not owner_username:
                    owner_username = value
                elif label == "url" and value and not owner_url:
                    owner_url = value

    return owner_name, owner_username, owner_url


def _label_values_export_record(record: dict[str, Any]) -> dict[str, str] | None:
    """
    Parse the saved_posts.json format used by newer Meta exports:

    {
      "timestamp": ...,
      "label_values": [
        {"label": "URL", "value": "...", "href": "..."},
        {"label": "Caption", "value": "..."},
        {"label": "Title", "value": "..."},
        {"title": "Owner", "dict": [...]}
      ]
    }
    """
    label_values = record.get("label_values")
    if not isinstance(label_values, list):
        return None

    url = ""
    captions: list[str] = []
    titles: list[str] = []

    for item in label_values:
        if not isinstance(item, dict):
            continue

        label = str(item.get("label") or "").strip().lower()
        if label == "url":
            candidate = str(item.get("href") or item.get("value") or "").strip()
            if _is_instagram_post_url(candidate):
                url = candidate
        elif label == "caption":
            caption = _fix_mojibake(item.get("value"))
            if caption and caption not in captions:
                captions.append(caption)
        elif label == "title":
            title = _fix_mojibake(item.get("value"))
            if title and title not in titles:
                titles.append(title)

    if not url:
        return None

    owner_name, owner_username, owner_url = _owner_from_label_values(label_values)

    title = titles[0] if titles else (owner_name or owner_username or "Instagram saved post")
    raw_parts: list[str] = []
    if captions:
        raw_parts.append("\n\n".join(captions))

    owner_label = owner_name
    if owner_username:
        owner_label = f"{owner_label} (@{owner_username})" if owner_label else f"@{owner_username}"
    if owner_label:
        raw_parts.append(f"Instagram owner: {owner_label}")
    if owner_url:
        raw_parts.append(f"Owner website: {owner_url}")

    return {
        "import_title": title,
        "raw_text": "\n\n".join(raw_parts),
        "instagram_url": url,
        "imported_official_url": "",
    }


def _instagram_href_from_map(record: dict[str, Any]) -> str:
    smd = record.get("string_map_data")
    if not isinstance(smd, dict):
        return ""

    for value in smd.values():
        if isinstance(value, dict):
            href = str(value.get("href") or "").strip()
            if _is_instagram_post_url(href):
                return href
    return ""


def _instagram_href_from_list(record: dict[str, Any]) -> str:
    sld = record.get("string_list_data")
    if not isinstance(sld, list):
        return ""
    for item in sld:
        if isinstance(item, dict):
            href = str(item.get("href") or "").strip()
            if _is_instagram_post_url(href):
                return href
    return ""


def _instagram_value_from_list(record: dict[str, Any]) -> str:
    sld = record.get("string_list_data")
    if not isinstance(sld, list):
        return ""
    for item in sld:
        if isinstance(item, dict):
            value = _fix_mojibake(item.get("value"))
            if value:
                return value
    return ""


def _instagram_export_record(record: dict[str, Any]) -> dict[str, str] | None:
    href = _instagram_href_from_map(record) or _instagram_href_from_list(record)
    if not href:
        return None

    title = _fix_mojibake(record.get("title"))
    value = _instagram_value_from_list(record)

    raw_bits = []
    if title:
        raw_bits.append(f"Instagram export title/collection: {title}")
    if value and value != title:
        raw_bits.append(f"Instagram export value/account: {value}")

    return {
        "import_title": title or value or "Instagram saved post",
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

    # Newer Meta saved_posts.json format.
    label_values_row = _label_values_export_record(value)
    if label_values_row:
        records.append(label_values_row)
        return records

    # Older Meta export variants.
    instagram_row = _instagram_export_record(value)
    if instagram_row:
        records.append(instagram_row)
        return records

    # Generic JSON records.
    keys = {str(k).lower() for k in value}
    hints = {alias for group in ALIASES.values() for alias in group}
    if keys & hints:
        normalized = normalize_record(value)
        if (
            normalized["raw_text"]
            or normalized["imported_official_url"]
            or normalized["instagram_url"]
        ):
            records.append(normalized)
            return records

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
