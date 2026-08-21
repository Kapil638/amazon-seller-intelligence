"""Copilot-facing input schemas. These are the only arguments an LLM planner may supply."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CopilotToolInput(BaseModel):
    """Ignore extra keys so a model cannot smuggle `confirmed`, `product`, or handlers."""

    model_config = ConfigDict(extra="ignore")


class GetSavedReportInput(CopilotToolInput):
    report_id: UUID


class ListSavedReportsInput(CopilotToolInput):
    asin: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class AnalyzeListingV2Input(CopilotToolInput):
    """Load a product through ProductService, then score with Listing Intelligence V2.

    Copilot must not accept a Product blob. Manual/seller-entered listings stay on
    `POST /api/v1/analysis/listing/v2`, not this tool.
    """

    asin: str
    marketplace: str | None = None


class GetProductInput(CopilotToolInput):
    asin: str
    marketplace: str | None = None
