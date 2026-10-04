from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


SPLITTER_VERSION = 1

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

PLURAL_KINDS = r"(?:competitions|contests|scholarships|awards|honors|programs|opportunities|challenges|internships|fellowships)"
LIST_HINT_RE = re.compile(
    rf"(?i)(?:\b(?:\d+|five|six|seven|eight|nine|ten|twelve|fifteen)\s+[^\n.]{{0,35}}{PLURAL_KINDS}\b|"
    rf"\b(?:list|guide|directory|roundup)\s+(?:of|to)\b|"
    rf"\b(?:full|complete)\s+guide\b|"
    rf"\bwe(?:'ve| have)\s+(?:reviewed|put together)\b|"
    rf"\b{PLURAL_KINDS}\s+(?:you should know|available to|to apply for|with [a-z]+ deadlines)\b)"
)

DATE_FIRST_RE = re.compile(
    r"(?i)\b("
    + "|".join(sorted(MONTHS, key=len, reverse=True))
    + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*[:\-–—]\s*(.+)$"
)

DATE_ANY_RE = re.compile(
    r"(?i)\b("
    + "|".join(sorted(MONTHS, key=len, reverse=True))
    + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b"
)

DATE_DAY_MONTH_RE = re.compile(
    r"(?i)\b(\d{1,2})(?:st|nd|rd|th)?\s+("
    + "|".join(sorted(MONTHS, key=len, reverse=True))
    + r")\b"
)

NAME_SUFFIX_RE = re.compile(
    r"(?<![\w@])("
    r"(?:[A-Z][A-Za-z0-9&.'’\-]*|[A-Z]{2,}|(?:of|in|for|the|and|Global|Young|Student|Students|National|University|School|Math|Mathematics|Essay|Case|Computing))"
    r"(?:\s+(?:[A-Z][A-Za-z0-9&.'’\-]*|[A-Z]{2,}|of|in|for|the|and|Global|Young|Student|Students|National|University|School|Math|Mathematics|Essay|Case|Computing)){0,8}"
    r"\s+(?:Competition|Contest|Challenge|Award|Scholarship|Fellowship|Olympiad|Program)"
    r")\b",
)

LOWER_SUFFIX_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&.'’\-]*(?:['’]s)?(?:\s+(?:[A-Za-z][A-Za-z0-9&.'’\-]*)){0,6}\s+"
    r"(?:competition|contest|challenge|award|scholarship|fellowship|olympiad|program))\b"
)

SPECIAL_NAME_RE = re.compile(
    r"\b("
    r"Profile in Courage Essay|Yale Young Global Scholars|YoungArts|Voice of Democracy|"
    r"College Board National Recognition|Most Valuable Student"
    r")\b",
    re.I,
)

GENERIC_BAD_NAMES = {
    "research competition", "science competition", "science competitions",
    "math competition", "math competitions", "essay competition",
    "essay competitions", "case competition", "case competitions",
    "summer program", "summer programs", "research program", "research programs",
    "scholarship", "scholarships", "competition", "competitions",
    "award", "awards", "opportunity", "opportunities",
}

LEADING_NOISE = (
    "from ", "and ", "a ", "an ", "alongside ", "including ",
    "like ", "try ", "enter ", "join ", "apply to ", "apply for ",
)


def clean_source_text(raw_text: str) -> str:
    lines: list[str] = []
    for line in (raw_text or "").splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("instagram owner:") or low.startswith("owner website:"):
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def looks_like_list_source(text: str) -> bool:
    return bool(LIST_HINT_RE.search(text or ""))


def _cleanup_name(value: str) -> str:
    name = re.sub(r"^[^A-Za-z0-9]+", "", value.strip())
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"\s+(?:and more|or more)$", "", name, flags=re.I)
    name = re.sub(r"\s*[.!;,]+$", "", name)
    low = name.lower()
    for prefix in LEADING_NOISE:
        if low.startswith(prefix):
            name = name[len(prefix):].strip()
            low = name.lower()
            break
    return name.strip(" -–—:;,.()[]{}")


def infer_kind(name: str) -> str:
    low = name.lower()
    if "scholar" in low:
        return "scholarship"
    if "fellow" in low:
        return "fellowship"
    if "intern" in low:
        return "internship"
    if "award" in low or "honor" in low:
        return "award"
    if "program" in low or "young global scholars" in low or "youngarts" in low:
        return "program"
    return "competition"


def _valid_name(name: str) -> bool:
    if not name or len(name) < 4 or len(name) > 120:
        return False
    low = name.lower().strip()
    if low in GENERIC_BAD_NAMES:
        return False
    if low.startswith(("these ", "this ", "best ", "top ", "every ", "many ", "some ")):
        return False
    # At least one proper-looking token or well-known acronym.
    return bool(re.search(r"\b(?:[A-Z]{2,}|[A-Z][a-z]{2,})\b", name))


def _date_candidates(text: str) -> list[tuple[int, int, str, int, int]]:
    value = text or ""
    found: list[tuple[int, int, str, int, int]] = []

    for match in DATE_ANY_RE.finditer(value):
        found.append(
            (
                MONTHS[match.group(1).lower()],
                int(match.group(2)),
                match.group(0),
                match.start(),
                match.end(),
            )
        )

    for match in DATE_DAY_MONTH_RE.finditer(value):
        found.append(
            (
                MONTHS[match.group(2).lower()],
                int(match.group(1)),
                match.group(0),
                match.start(),
                match.end(),
            )
        )

    # Prevent duplicate spans if a future regex change ever overlaps.
    unique: dict[tuple[int, int], tuple[int, int, str, int, int]] = {}
    for item in found:
        unique[(item[3], item[4])] = item
    return sorted(unique.values(), key=lambda item: item[3])


def _date_parts(text: str, near_name: str = "") -> tuple[int, int, str] | None:
    candidates = _date_candidates(text)
    if not candidates:
        return None

    if near_name:
        value_lower = (text or "").lower()
        name_pos = value_lower.find(near_name.lower())
        if name_pos >= 0:
            name_end = name_pos + len(near_name)

            # In list captions the timing usually follows the item it belongs
            # to: "Diamond Challenge opens 16 September". Prefer the first
            # date after the name, rather than whichever date is geometrically
            # closest in a sentence containing several opportunities.
            following = [item for item in candidates if item[3] >= name_end]
            if following:
                chosen = min(following, key=lambda item: item[3] - name_end)
                return chosen[0], chosen[1], chosen[2]

            preceding = [item for item in candidates if item[4] <= name_pos]
            if preceding:
                chosen = min(preceding, key=lambda item: name_pos - item[4])
                return chosen[0], chosen[1], chosen[2]

    chosen = candidates[0]
    return chosen[0], chosen[1], chosen[2]


def _normalized_date(month: int, day: int, source_timestamp: int | None) -> str | None:
    if not source_timestamp:
        return None
    try:
        source_dt = datetime.fromtimestamp(int(source_timestamp), tz=timezone.utc)
        year = source_dt.year
        # A Dec post pointing to Jan/Feb normally means the next cycle.
        if source_dt.month >= 10 and month <= 3:
            year += 1
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, OSError, TypeError):
        return None


def _timing_from_context(
    context: str,
    source_timestamp: int | None,
    near_name: str = "",
) -> dict[str, str | None]:
    parts = _date_parts(context, near_name=near_name)
    if not parts:
        return {"deadline": None, "deadline_text": None, "source_timing": None}
    month, day, date_text = parts
    low = context.lower()

    if any(word in low for word in ("open ", "opens ", "opening ", "follows on")):
        return {
            "deadline": None,
            "deadline_text": None,
            "source_timing": f"Opens {date_text}" if date_text else "Opening date mentioned in source",
        }

    return {
        "deadline": _normalized_date(month, day, source_timestamp),
        "deadline_text": date_text,
        "source_timing": f"Deadline {date_text}" if date_text else None,
    }


def _add_candidate(
    out: list[dict[str, Any]],
    seen: set[str],
    name: str,
    context: str,
    source_timestamp: int | None,
) -> None:
    name = _cleanup_name(name)
    if not _valid_name(name):
        return
    key = re.sub(r"[^a-z0-9]+", "", name.lower())
    if not key or key in seen:
        return
    seen.add(key)
    timing = _timing_from_context(context, source_timestamp, near_name=name)
    out.append(
        {
            "name": name,
            "kind": infer_kind(name),
            "deadline": timing["deadline"],
            "deadline_text": timing["deadline_text"],
            "source_timing": timing["source_timing"],
            "source_excerpt": context.strip(),
        }
    )


def _explicit_date_lines(text: str, source_timestamp: int | None, out: list[dict[str, Any]], seen: set[str]) -> None:
    for line in text.splitlines():
        m = DATE_FIRST_RE.search(line.strip())
        if not m:
            continue
        name = m.group(3)
        _add_candidate(out, seen, name, line, source_timestamp)


def _colon_lists(text: str, source_timestamp: int | None, out: list[dict[str, Any]], seen: set[str]) -> None:
    # Example: "Scholarships to apply for: A, B, C, D, and more"
    for line in text.splitlines():
        if ":" not in line:
            continue
        lead, tail = line.split(":", 1)
        if not re.search(r"(?i)scholarships?|competitions?|awards?|programs?|opportunities?", lead):
            continue
        parts = re.split(r",|\s+and\s+", tail)
        for part in parts:
            candidate = _cleanup_name(part)
            if candidate.lower() in {"more", "and more"}:
                continue
            _add_candidate(out, seen, candidate, line, source_timestamp)


def _named_suffixes(text: str, source_timestamp: int | None, out: list[dict[str, Any]], seen: set[str]) -> None:
    # Work sentence by sentence so nearby opening/deadline dates can be retained.
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    for sentence in sentences:
        if not sentence.strip():
            continue
        for pattern in (NAME_SUFFIX_RE, LOWER_SUFFIX_RE, SPECIAL_NAME_RE):
            for m in pattern.finditer(sentence):
                _add_candidate(out, seen, m.group(1), sentence, source_timestamp)


def _numbered_lines(text: str, source_timestamp: int | None, out: list[dict[str, Any]], seen: set[str]) -> None:
    for line in text.splitlines():
        if not re.match(r"^\s*(?:\d{1,2}(?:\ufe0f?\u20e3)?[.)]?|[①-⑳]|[•▪►➗⚖🎥🎤📝✍🚀🏆])\s*", line):
            continue
        body = re.sub(r"^\s*(?:\d{1,2}(?:\ufe0f?\u20e3)?[.)]?|[①-⑳]|[•▪►➗⚖🎥🎤📝✍🚀🏆])\s*", "", line).strip()
        if not body:
            continue

        # "Name: explanation"
        if ":" in body:
            head = body.split(":", 1)[0].strip()
            # Date-first list rows (for example "Oct 2: Contest Name") were
            # already handled above; do not accidentally create an "Oct 2" item.
            if not DATE_ANY_RE.fullmatch(head) and 1 <= len(head.split()) <= 10:
                _add_candidate(out, seen, head, line, source_timestamp)

        # Suffix/special names within action-oriented list lines.
        for pattern in (NAME_SUFFIX_RE, LOWER_SUFFIX_RE, SPECIAL_NAME_RE):
            for m in pattern.finditer(body):
                _add_candidate(out, seen, m.group(1), line, source_timestamp)

        # Last fallback for numbered lines: a compact proper-name phrase after
        # common verbs such as "enter the Elks" or "record your Voice of Democracy".
        action = re.search(
            r"(?i)\b(?:enter|record your|apply (?:to|for)|entry for)\s+(?:the\s+)?(.+?)(?:\s+before\s+|\s+by\s+|\s+on your\s+|\s+tonight\b|$)",
            body,
        )
        if action:
            phrase = _cleanup_name(action.group(1))
            # Avoid turning long instructions into names.
            if len(phrase.split()) <= 8:
                _add_candidate(out, seen, phrase, line, source_timestamp)


def split_source_post(raw_text: str, source_timestamp: int | None = None) -> dict[str, Any]:
    text = clean_source_text(raw_text)
    is_list = looks_like_list_source(text)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    _explicit_date_lines(text, source_timestamp, out, seen)
    _colon_lists(text, source_timestamp, out, seen)
    _numbered_lines(text, source_timestamp, out, seen)
    _named_suffixes(text, source_timestamp, out, seen)

    # If multiple named items are present, the post is a source list even when
    # the caption did not use a numeric "5 competitions" style heading.
    if len(out) >= 2:
        is_list = True

    # A single clearly named opportunity is still useful: make it a child item
    # so the default dashboard contains actual names rather than account names.
    return {
        "is_source_list": is_list,
        "items": out,
        "splitter_version": SPLITTER_VERSION,
    }
