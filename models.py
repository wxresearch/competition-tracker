from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class CompetitionExtraction(BaseModel):
    is_competition: bool = True
    competition_name: Optional[str] = None
    organizer: Optional[str] = None
    category: Optional[str] = None
    deadline: Optional[str] = Field(
        default=None,
        description="Deadline in YYYY-MM-DD when a precise date is known.",
    )
    deadline_text: Optional[str] = None
    entry_fee: Optional[str] = None
    prize: Optional[str] = None
    eligibility: Optional[str] = None
    requirements: Optional[str] = None
    official_url: Optional[str] = None
    confidence: float = Field(default=0.5, ge=0, le=1)


class CompetitionVerification(BaseModel):
    competition_name: Optional[str] = None
    organizer: Optional[str] = None
    category: Optional[str] = None
    deadline: Optional[str] = None
    deadline_text: Optional[str] = None
    entry_fee: Optional[str] = None
    prize: Optional[str] = None
    eligibility: Optional[str] = None
    requirements: Optional[str] = None
    official_url: Optional[str] = None
    status: Optional[str] = Field(
        default=None,
        description="One of open, upcoming, closed, unclear when determinable.",
    )
    notes: Optional[str] = None
    confidence: float = Field(default=0.5, ge=0, le=1)
