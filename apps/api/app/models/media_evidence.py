from enum import StrEnum

from pydantic import BaseModel, Field


class MediaSourceType(StrEnum):
    MAIN_IMAGE = "main_image"
    GALLERY = "gallery"
    A_PLUS = "a_plus"
    BRAND_STORY = "brand_story"


class MediaEvidenceItem(BaseModel):
    id: str
    source_type: MediaSourceType
    url: str
    alt_text: str | None = None
    width: int | None = None
    height: int | None = None
    position: int = Field(..., ge=0)


class SkippedMediaItem(BaseModel):
    source_type: MediaSourceType | None = None
    reason: str
    host: str | None = None


class VideoEvidenceSummary(BaseModel):
    video_present: bool
    video_details_available: bool
    video_count_reported: int | None = None
    video_object_count: int = 0
    titles: list[str] = Field(default_factory=list)
    frames_not_analyzed: bool = True


class MediaSelectionResult(BaseModel):
    images_available: int = Field(..., ge=0)
    images_selected: int = Field(..., ge=0)
    images_skipped: int = Field(..., ge=0)
    selection_reason: str
    selected: list[MediaEvidenceItem] = Field(default_factory=list)
    skipped: list[SkippedMediaItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    video: VideoEvidenceSummary
