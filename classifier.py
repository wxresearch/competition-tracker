from __future__ import annotations

import re
from typing import Any


CLASSIFIER_VERSION = 1

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
    "common app",
    "supplemental essay",
    "personal statement",
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


def classify_saved_post(title: str = "", raw_text: str = "") -> dict[str, Any]:
    text = f"{title}\n{raw_text}".lower()
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

    if kind in {"scholarship", "internship", "research_program", "summer_program", "competition"}:
        # A concrete opportunity noun is strong enough on its own.
        is_opportunity = raw_score >= 5
    elif kind == "award":
        # "Award" can also appear in generic admissions advice, so require one
        # additional action/timing signal unless the score is already strong.
        is_opportunity = raw_score >= 5
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
