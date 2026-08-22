"""Seller-facing synthesis from EvidenceEnvelope facts. Does not execute tools."""

from app.copilot.synthesis.schemas import EvidenceCitation, SynthesisRequest, SynthesizedResponse
from app.copilot.synthesis.service import SynthesisService, get_synthesis_service

__all__ = [
    "EvidenceCitation",
    "SynthesisRequest",
    "SynthesisService",
    "SynthesizedResponse",
    "get_synthesis_service",
]
