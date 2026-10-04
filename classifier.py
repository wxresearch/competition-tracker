from __future__ import annotations

import re
from typing import Any


CLASSIFIER_VERSION = 2

# Deliberately simple, transparent rules. This stage is free/local and only
# decides what is worth sending to the paid AI steps later.
POSITIVE_WEIGHTS = {
    "competition": 5,
    "competitions": 5,
    "contest": 5,
    "contests": 5,
    "scholarship": 5,
    "scholarships": 5,
    "olympiad": 5,
    "hackathon": 5,
    "fellowship": 5,
    "internship": 5,
    "internships": 5,
    "challenge": 4,
    "award": 4,
    "awards": 4,
    "honor": 3,
    "honors": 3,
    "deadline": 4,
    "deadlines": 4,
    "eligibility": 3,
    "prize": 3,
    "prizes": 3,
    "apply now": 3,
    "apply to": 2,
    "apply for": 2,
    "applications open": 3,
    "open now": 3,
    "free to apply": 3,
    "student opportunities": 3,
    "high school opportunities": 4,
    "research program": 5,
    "research programs": 5,
    "summer program": 5,
    "summer programs": 5,
}

ADVICE_MARKERS = (
    "college admissions",
    "college application",
    "college applications",
    "college essay",
    "college essays",
    "essay advice",
    "common app",
    "supplemental essay",
    "personal statement",
    "application tips",
    "interview answer",
    "college interview",
    "letter of rec",
    "letter of recommendation",
    "recommendation letter",
    "sat prep",
    "sat tips",
    "digital sat",
)

RESOURCE_MARKERS = (
    "free resources",
    "websites",
    "study website",
    "study websites",
    "coding ideas",
    "programming skills",
    "python notes",
    "complete notes",
    "tutorial",
    "shortcuts",
    "certifications",
    "courses",
)


def _contains(text: str, phrase: str) -> bool:
    # Word boundaries where useful, substring behavior for multiword phrases.
    if " " in phrase:
        return phrase in text
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def _opportunity_kind(text: str) -> str | None:
    if _contains(text, "scholarship") or _contains(text, "scholarships"):
        return "scholarship"
    if _contains(text, "internship") or _contains(text, "internships"):
        return "internship"
    if "research program" in text or "research programs" in text:
        return "research_program"
    if "summer program" in text or "summer programs" in text:
        return "summer_program"
    if any(_contains(text, x) for x in ("competition", "competitions", "contest", "contests", "olympiad", "hackathon", "challenge")):
        return "competition"
    if any(_contains(text, x) for x in ("award", "awards", "honor", "honors")):
        return "award"
    if "opportunit" in text:
        return "opportunity"
    return None


def _prepare_text(title: str, raw_text: str) -> str:
    """
    Remove importer metadata and obvious social-media tag dumps before scoring.

    In Meta exports, import_title is often the account/owner name rather than a
    post title. If the same value appears in the "Instagram owner:" metadata,
    do not let words in that account name affect classification.
    """
    raw_lower = raw_text.lower()
    title_for_scoring = title
    if title and f"instagram owner: {title.lower()}" in raw_lower:
        title_for_scoring = ""

    cleaned_lines: list[str] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith("instagram owner:") or lowered.startswith("owner website:"):
            continue
        # Meta captions often finish with a long hashtag/keyword dump. Those
        # words describe reach/SEO and should not decide whether the post is an
        # actual opportunity.
        if stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            continue
        cleaned_lines.append(line)

    return f"{title_for_scoring}\n" + "\n".join(cleaned_lines).lower()


def classify_saved_post(title: str = "", raw_text: str = "") -> dict[str, Any]:
    text = _prepare_text(title, raw_text)
    hits: list[str] = []
    raw_score = 0

    for phrase, weight in POSITIVE_WEIGHTS.items():
        if _contains(text, phrase):
            raw_score += weight
            hits.append(phrase)

    kind = _opportunity_kind(text)

    # If the text has no concrete opportunity signal, identify obvious advice or
    # resource posts instead of making them look like broken competitions.
    advice_hits = [p for p in ADVICE_MARKERS if p in text]
    resource_hits = [p for p in RESOURCE_MARKERS if p in text]

    action_signal = any(
        phrase in text
        for phrase in (
            "apply", "deadline", "open now", "applications open",
            "free to apply", "prize", "eligibility", "winner", "win "
        )
    )

    if kind in {"scholarship", "internship", "research_program", "summer_program", "competition"}:
        is_opportunity = raw_score >= 5 and not (advice_hits and raw_score < 9)
    elif kind == "award":
        special_award_list = any(
            phrase in text
            for phrase in (
                "awards anyone", "award anyone", "awards for high school",
                "awards you can", "last minute awards"
            )
        )
        is_opportunity = raw_score >= 5 and (action_signal or special_award_list)
    elif kind == "opportunity":
        is_opportunity = raw_score >= 5
    else:
        is_opportunity = False

    if is_opportunity:
        score = min(98, 62 + raw_score * 3)
        reason_terms = ", ".join(hits[:5])
        return {
            "local_kind": kind or "opportunity",
            "local_score": score,
            "local_reason": f"Opportunity signals: {reason_terms}" if reason_terms else "Likely opportunity",
            "local_is_opportunity": 1,
            "local_classifier_version": CLASSIFIER_VERSION,
        }

    # Prefer a clear advice/resource label over a weak accidental keyword hit.
    if advice_hits and raw_score < 7:
        return {
            "local_kind": "college_advice",
            "local_score": 10,
            "local_reason": f"Looks like college/application advice: {', '.join(advice_hits[:3])}",
            "local_is_opportunity": 0,
            "local_classifier_version": CLASSIFIER_VERSION,
        }

    if resource_hits and raw_score < 7:
        return {
            "local_kind": "resource",
            "local_score": 10,
            "local_reason": f"Looks like a resource/tutorial post: {', '.join(resource_hits[:3])}",
            "local_is_opportunity": 0,
            "local_classifier_version": CLASSIFIER_VERSION,
        }

    # Borderline captions are kept in Needs Review so they are not lost.
    if raw_score >= 3:
        score = min(65, 35 + raw_score * 4)
        reason_terms = ", ".join(hits[:5])
        return {
            "local_kind": "needs_review",
            "local_score": score,
            "local_reason": f"Some opportunity signals, but not enough to classify confidently: {reason_terms}",
            "local_is_opportunity": 0,
            "local_classifier_version": CLASSIFIER_VERSION,
        }

    if advice_hits:
        return {
            "local_kind": "college_advice",
            "local_score": 10,
            "local_reason": f"Looks like college/application advice: {', '.join(advice_hits[:3])}",
            "local_is_opportunity": 0,
            "local_classifier_version": CLASSIFIER_VERSION,
        }

    if resource_hits:
        return {
            "local_kind": "resource",
            "local_score": 10,
            "local_reason": f"Looks like a resource/tutorial post: {', '.join(resource_hits[:3])}",
            "local_is_opportunity": 0,
            "local_classifier_version": CLASSIFIER_VERSION,
        }

    return {
        "local_kind": "other",
        "local_score": 5,
        "local_reason": "No strong competition, scholarship, award, program, internship, or deadline signal found.",
        "local_is_opportunity": 0,
        "local_classifier_version": CLASSIFIER_VERSION,
    }


def display_kind(kind: str | None) -> str:
    labels = {
        "competition": "Competition",
        "scholarship": "Scholarship",
        "award": "Award / Honor",
        "research_program": "Research Program",
        "summer_program": "Summer Program",
        "internship": "Internship",
        "opportunity": "Opportunity",
        "needs_review": "Needs Review",
        "resource": "Resource",
        "college_advice": "College Advice",
        "other": "Other",
    }
    return labels.get(kind or "", "Unclassified")
