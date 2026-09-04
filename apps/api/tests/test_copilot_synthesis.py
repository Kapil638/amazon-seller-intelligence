from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.copilot.evidence import claim, envelope
from app.copilot.listing_evidence import listing_analysis_claims
from app.copilot.synthesis.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from app.copilot.synthesis.schemas import (
    ProposedFinding,
    ProposedRecommendation,
    SynthesisProposal,
    SynthesisRequest,
)
from app.copilot.synthesis.service import SynthesisService
from app.copilot.synthesis.validator import build_allowed_facts, copy_evidence, validate_proposal
from app.core.exceptions import AIRequestFailedError, AIStructuredOutputError


class _FakeSynthesizer:
    def __init__(self, proposal: SynthesisProposal | None = None, *, error: Exception | None = None) -> None:
        self.proposal = proposal
        self.error = error
        self.calls: list[dict] = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.proposal, "mock-synthesizer"


def _report_envelope(*, score: int = 72):
    return envelope(
        "get_saved_report",
        [
            claim("asin", "B0TEST0001", kind="historical", source="snapshot"),
            claim(
                "listing_quality_score",
                score,
                kind="historical",
                source="snapshot",
                notes="Score from the saved analysis. Not recalculated.",
            ),
            claim(
                "findings",
                [
                    {
                        "code": "BULLET_COUNT",
                        "category": "bullets",
                        "severity": "high",
                        "message": "Add more complete bullet points.",
                    }
                ],
                kind="historical",
                source="snapshot",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_evidence_produces_valid_grounded_response() -> None:
    packed = _report_envelope()
    original = packed.model_dump()
    proposal = SynthesisProposal(
        summary="Your listing score is 72.",
        findings=[
            ProposedFinding(
                text="Your listing score is 72.",
                claim_key="listing_quality_score",
                evidence_id=packed.evidence_id,
            ),
            ProposedFinding(
                text="Your listing analysis identified bullet structure as a weakness.",
                claim_key="findings",
                evidence_id=packed.evidence_id,
            ),
        ],
        recommendations=[
            ProposedRecommendation(
                text="Add more complete bullet points.",
                claim_key="findings",
                evidence_id=packed.evidence_id,
            )
        ],
        confidence="high",
    )
    service = SynthesisService(generator=_FakeSynthesizer(proposal))
    result = await service.synthesize(
        SynthesisRequest(
            user_message="Why is my listing score low?",
            intent="explain_listing_score",
            evidence=[packed],
            compact_context={"last_asin": "B0TEST0001", "nonce": "secret-nonce"},
        )
    )
    assert result.source == "synthesis_llm"
    assert result.prompt_version == PROMPT_VERSION
    assert result.summary == "Your listing score is 72."
    assert any("72" in item for item in result.findings)
    assert result.recommendations
    assert {item.claim_key for item in result.citations} >= {"listing_quality_score", "findings"}
    assert all(item.tool_name == "get_saved_report" for item in result.citations)
    assert "## Summary" in result.message
    assert "ToolRegistry" not in result.message
    assert packed.model_dump() == original
    assert "secret-nonce" not in service._generator.calls[0]["user_prompt"]  # type: ignore[union-attr]
    assert "ALLOWED FACTS" in service._generator.calls[0]["user_prompt"]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_structured_output_schema_rejects_unknown_shape() -> None:
    parsed = SynthesisProposal.model_validate(
        {"summary": "ok", "findings": [{"text": "Score is 72", "claim_key": "listing_quality_score"}]}
    )
    assert parsed.findings[0].claim_key == "listing_quality_score"
    with pytest.raises(ValidationError):
        SynthesisProposal.model_validate("not-json")


def test_citation_validator_accepts_evidence_backed_claim() -> None:
    packed = _report_envelope()
    facts = build_allowed_facts([packed])
    result = validate_proposal(
        SynthesisProposal(
            summary="Your listing score is 72.",
            findings=[
                ProposedFinding(
                    text="Your listing score is 72.",
                    claim_key="listing_quality_score",
                    evidence_id=packed.evidence_id,
                )
            ],
            confidence="high",
        ),
        facts=facts,
        intent="explain_listing_score",
        user_message="Why is my score low?",
        prompt_version=PROMPT_VERSION,
        synthesis_model="mock-synthesizer",
    )
    assert result.source == "synthesis_llm"
    assert result.citations[0].claim_key == "listing_quality_score"
    assert result.citations[0].evidence_id == packed.evidence_id


def test_citation_validator_drops_missing_citation() -> None:
    packed = _report_envelope()
    facts = build_allowed_facts([packed])
    result = validate_proposal(
        SynthesisProposal(
            summary="Your listing score is 72.",
            findings=[ProposedFinding(text="Conversion is 12%.", claim_key="", evidence_id=None)],
            confidence="high",
        ),
        facts=facts,
        intent="explain_listing_score",
        user_message="Why is my score low?",
        prompt_version=PROMPT_VERSION,
        synthesis_model="mock",
    )
    assert result.source == "template_fallback"
    assert all("conversion" not in item.lower() for item in result.findings)
    assert any("72" in item for item in result.findings)


def test_citation_validator_rejects_invalid_evidence_id() -> None:
    packed = _report_envelope()
    facts = build_allowed_facts([packed])
    result = validate_proposal(
        SynthesisProposal(
            summary="Your listing score is 72.",
            findings=[
                ProposedFinding(
                    text="Your listing score is 72.",
                    claim_key="listing_quality_score",
                    evidence_id=uuid4(),
                )
            ],
            confidence="high",
        ),
        facts=facts,
        intent="explain_listing_score",
        user_message="Why is my score low?",
        prompt_version=PROMPT_VERSION,
        synthesis_model="mock",
    )
    assert result.source == "template_fallback"
    assert any("72" in item for item in result.findings)


def test_citation_validator_rewrites_hallucinated_ranking_claim() -> None:
    packed = _report_envelope()
    facts = build_allowed_facts([packed])
    result = validate_proposal(
        SynthesisProposal(
            summary="Your listing analysis identified improvement opportunities.",
            findings=[
                ProposedFinding(
                    text="Amazon ranking is lower because your bullet points are weak.",
                    claim_key="findings",
                    evidence_id=packed.evidence_id,
                )
            ],
            recommendations=[
                ProposedRecommendation(
                    text="Increase PPC spend 20%.",
                    claim_key="findings",
                    evidence_id=packed.evidence_id,
                )
            ],
            confidence="high",
        ),
        facts=facts,
        intent="explain_listing_score",
        user_message="Why is my score low?",
        prompt_version=PROMPT_VERSION,
        synthesis_model="mock",
    )
    assert result.source == "rewritten_citations"
    assert result.findings
    assert all("ranking" not in item.lower() for item in result.findings)
    assert "bullet" in result.findings[0].lower()
    assert all("ppc" not in item.lower() for item in result.recommendations)


@pytest.mark.asyncio
async def test_invalid_json_uses_template_fallback() -> None:
    packed = _report_envelope()
    service = SynthesisService(generator=_FakeSynthesizer(None))
    result = await service.synthesize(
        SynthesisRequest(user_message="Why is my score low?", intent="explain_listing_score", evidence=[packed])
    )
    assert result.source == "template_fallback"
    assert result.findings
    assert result.summary
    assert any("72" in item for item in result.findings)


@pytest.mark.asyncio
async def test_provider_unavailable_uses_template_fallback() -> None:
    packed = _report_envelope()
    service = SynthesisService(generator=_FakeSynthesizer(error=RuntimeError("provider down")))
    result = await service.synthesize(
        SynthesisRequest(user_message="Why is my score low?", intent="explain_listing_score", evidence=[packed])
    )
    assert result.source == "template_fallback"
    assert "72" in result.message


@pytest.mark.asyncio
async def test_timeout_and_structured_errors_use_template() -> None:
    packed = _report_envelope()
    timed_out = SynthesisService(generator=_FakeSynthesizer(error=TimeoutError("AI analysis timed out.")))
    broken = SynthesisService(generator=_FakeSynthesizer(error=AIStructuredOutputError()))
    failed = SynthesisService(generator=_FakeSynthesizer(error=AIRequestFailedError()))
    for service in (timed_out, broken, failed):
        result = await service.synthesize(
            SynthesisRequest(user_message="Why is my score low?", intent="explain_listing_score", evidence=[packed])
        )
        assert result.source == "template_fallback"
        assert result.findings
        assert result.message


@pytest.mark.asyncio
async def test_out_of_scope_is_canned_without_llm() -> None:
    generator = _FakeSynthesizer(
        SynthesisProposal(summary="Competitors have lower ACOS.", findings=[], confidence="high")
    )
    service = SynthesisService(generator=generator)
    result = await service.synthesize(
        SynthesisRequest(user_message="Compare with competitors", intent="out_of_scope", evidence=[])
    )
    assert result.source == "template_fallback"
    assert generator.calls == []
    assert "not available" in result.summary.lower()
    assert result.findings == []


@pytest.mark.asyncio
async def test_http_synthesize_template_path(client: TestClient) -> None:
    packed = _report_envelope()
    response = client.post(
        "/api/v1/copilot/synthesize",
        json={
            "user_message": "Why is my listing score low?",
            "intent": "explain_listing_score",
            "evidence": [packed.model_dump(mode="json")],
            "organization_id": str(uuid4()),
            "compact_context": {"last_asin": "B0TEST0001"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "template_fallback"
    assert "organization_id" not in body
    assert any("72" in item for item in body["findings"])
    assert "## Key Findings" in body["message"]


def test_copy_evidence_does_not_alias_source_objects() -> None:
    packed = _report_envelope()
    cloned = copy_evidence([packed])[0]
    cloned.claims[0].value = "mutated"
    assert packed.claims[0].value == "B0TEST0001"


def test_synthesis_modules_cannot_execute_or_query() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "copilot" / "synthesis"
    forbidden = {
        "openai",
        "app.services.product_service",
        "app.services.listing_analysis_v2_service",
        "app.services.analysis_history_service",
        "app.providers.rainforest",
        "app.copilot.registry",
        "app.persistence.repositories",
        "sqlalchemy",
    }
    imported: set[str] = set()
    source = ""
    for path in root.glob("*.py"):
        text = path.read_text()
        source += text
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    for name in forbidden:
        assert name not in imported, name
    assert ".execute(" not in source
    assert "copilot_plan" not in SYSTEM_PROMPT
    assert PROMPT_VERSION == "copilot_synthesize_v2"


def _rich_report_envelope(*, score: int = 80):
    return envelope(
        "get_saved_report",
        [
            claim("asin", "B01MD1SKLL", kind="historical", source="snapshot"),
            claim("listing_quality_score", score, kind="historical", source="snapshot"),
            claim("analysis_engine", "listing_analysis_v2", kind="historical", source="snapshot"),
            claim(
                "section_scores",
                {
                    "title": {"label": "Title", "score": 72, "max_score": 100, "status": "good"},
                    "bullets": {"label": "Bullets", "score": 15, "max_score": 100, "status": "poor"},
                    "description_a_plus": {
                        "label": "Description / A+",
                        "score": 70,
                        "max_score": 100,
                        "status": "good",
                    },
                    "media_coverage": {"label": "Images", "score": 10, "max_score": 100, "status": "poor"},
                    "content_structure": {
                        "label": "Content structure",
                        "score": 80,
                        "max_score": 100,
                        "status": "good",
                    },
                },
                kind="historical",
                source="snapshot",
            ),
            claim(
                "findings",
                [
                    {
                        "code": "LIMITED_GALLERY",
                        "category": "media",
                        "severity": "medium",
                        "message": "Only a small image gallery was observed.",
                    }
                ],
                kind="historical",
                source="snapshot",
            ),
            claim(
                "weaknesses",
                [
                    {
                        "area": "media",
                        "issue": "Only a small image gallery was observed.",
                        "severity": "medium",
                        "code": "LIMITED_GALLERY",
                    },
                    {
                        "area": "bullets",
                        "issue": "Benefits are not clearly highlighted",
                        "severity": "medium",
                        "code": "LOW_BULLET_COVERAGE",
                    },
                ],
                kind="historical",
                source="snapshot",
            ),
            claim(
                "recommendations",
                [
                    {
                        "priority": 1,
                        "priority_label": "high",
                        "action": "Add additional product-detail images if available.",
                        "reason": "Only a small image gallery was observed.",
                        "code": "LIMITED_GALLERY",
                        "area": "media",
                    },
                    {
                        "priority": 2,
                        "priority_label": "medium",
                        "action": "Improve bullet structure",
                        "reason": "Benefits are not clearly highlighted",
                        "code": "LOW_BULLET_COVERAGE",
                        "area": "bullets",
                    },
                ],
                kind="historical",
                source="snapshot",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_rich_listing_evidence_answers_improvement_question() -> None:
    packed = _rich_report_envelope()
    result = await SynthesisService().synthesize(
        SynthesisRequest(
            user_message="How can I improve my listing?",
            intent="explain_listing_score",
            evidence=[packed],
        )
    )
    assert result.source == "template_fallback"
    assert "80" in result.summary
    assert any("Images: 10/100" in item for item in result.findings)
    assert any("Bullets: 15/100" in item for item in result.findings)
    assert any("Weak area:" in item for item in result.findings)
    assert "Add additional product-detail images if available." in result.recommendations
    cited = {item.claim_key for item in result.citations}
    assert cited >= {"listing_quality_score", "section_scores", "weaknesses", "recommendations"}
    assert all("ranking" not in item.lower() for item in result.findings)
    assert all("20%" not in item for item in result.recommendations)


def test_rich_evidence_still_rejects_invented_recommendations() -> None:
    packed = _rich_report_envelope()
    facts = build_allowed_facts([packed])
    result = validate_proposal(
        SynthesisProposal(
            summary="Your listing analysis identified improvement opportunities. The listing quality score is 80.",
            findings=[
                ProposedFinding(
                    text="Your listing score is 80.",
                    claim_key="listing_quality_score",
                    evidence_id=packed.evidence_id,
                )
            ],
            recommendations=[
                ProposedRecommendation(
                    text="Amazon ranking will improve by 20%.",
                    claim_key="recommendations",
                    evidence_id=packed.evidence_id,
                )
            ],
            confidence="high",
        ),
        facts=facts,
        intent="explain_listing_score",
        user_message="How can I increase the rating?",
        prompt_version=PROMPT_VERSION,
        synthesis_model="mock",
    )
    assert all("20%" not in item for item in result.recommendations)
    assert all("ranking" not in item.lower() for item in result.recommendations)


def test_listing_analysis_claims_copy_deterministic_scores() -> None:
    from app.services.listing_analysis_v2_service import ListingAnalysisV2Service
    from tests.test_listing_analysis import make_product

    analysis = ListingAnalysisV2Service().analyze(make_product(bullet_points=[]))
    packed = envelope("get_saved_report", listing_analysis_claims(analysis, kind="historical", source="snapshot"))
    assert packed.value("listing_quality_score") == analysis.listing_quality_score
    assert packed.value("section_scores")["bullets"]["score"] == analysis.sections.bullets.score
    assert packed.value("findings")
    assert packed.value("recommendations")
    assert all(row["action"] for row in packed.value("recommendations"))
    assert "product" not in packed.claim_map()
    assert packed.value("analysis_engine") == "listing_analysis_v2"
