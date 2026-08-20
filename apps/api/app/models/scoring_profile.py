from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ScoringWeights(BaseModel):
    title: float = Field(..., ge=0, le=100)
    bullets: float = Field(..., ge=0, le=100)
    description_a_plus: float = Field(..., ge=0, le=100)
    media: float = Field(..., ge=0, le=100)
    content_structure: float = Field(..., ge=0, le=100)

    @model_validator(mode="after")
    def weights_must_total_one_hundred(self) -> ScoringWeights:
        from app.analytics.scoring_profiles import validate_weights
        from app.core.exceptions import ScoringProfileValidationError

        try:
            validate_weights(self)
        except ScoringProfileValidationError as exc:
            raise ValueError(str(exc)) from exc
        return self


class ScoringProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    weights: ScoringWeights
    is_default: bool = False


class ScoringProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    weights: ScoringWeights | None = None
    is_default: bool | None = None


class ScoringProfileSnapshot(BaseModel):
    profile_id: str
    profile_name: str
    type: Literal["standard", "custom"]
    weights: ScoringWeights


class ScoringProfileResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    weights: ScoringWeights
    is_system: bool = False
    is_default: bool = False
    is_archived: bool = False
    editable: bool = True
    deletable: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None
    archived_at: datetime | None = None


class ScoringProfileListResponse(BaseModel):
    items: list[ScoringProfileResponse] = Field(default_factory=list)


class CustomScoreResult(BaseModel):
    custom_listing_quality_score: int = Field(..., ge=0, le=100)
    profile: ScoringProfileSnapshot
