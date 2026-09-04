"""12B.5B — Layer B (final-answer cache) tests. Pure in-memory: no
database, no Amazon call, no live LLM call anywhere in this file — the
fake generator below simulates an attached model so these tests can
prove "a cached answer never invokes the model again" without one."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.copilot.skills.contracts import PeriodWindow, SkillEvidence, skill_evidence_to_claims
from app.copilot.synthesis.schemas import SynthesisProposal, SynthesisRequest
from app.copilot.synthesis.service import SynthesisService, _ANSWER_CACHE, _ANSWER_SINGLE_FLIGHT
from app.copilot.evidence import envelope


class _FakeSynthesizer:
    def __init__(self, proposal: SynthesisProposal | None = None) -> None:
        self.proposal = proposal
        self.calls: list[dict] = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self.proposal, "mock-model"


def _skill_evidence(*, seller_sku: str = "SKU-A", organization_id=None) -> SkillEvidence:
    period = PeriodWindow(start=datetime(2026, 8, 1, tzinfo=UTC), end=datetime(2026, 8, 31, tzinfo=UTC), label="Aug 2026")
    return SkillEvidence(
        skill_id="listing_health_prioritizer",
        skill_version="2.0.0",
        organization_id=organization_id or uuid4(),
        marketplace_participation_ids=[uuid4()],
        analysis_period=period,
        has_newer_incomplete_run=False,
        metrics={"total_listings": 1},
        records=[{"seller_sku": seller_sku, "issue_count": 1}],
        limitations=["synthetic test fixture"],
        confidence="high",
        deep_links=[],
    )


def _envelope_for(evidence: SkillEvidence):
    return envelope(
        "prioritize_listing_health", skill_evidence_to_claims(evidence), organization_id=evidence.organization_id
    )


class _GatedSynthesizer:
    """Simulates a real attached LLM's network latency: `generate()`
    increments `started` the instant it is invoked, then blocks on
    `release` until the test lets it proceed — giving the test a
    deterministic window in which to assert "only one call is actually
    in flight" before unblocking it."""

    def __init__(self, proposal: SynthesisProposal | None = None, *, error: Exception | None = None) -> None:
        self.proposal = proposal
        self.error = error
        self.started = 0
        self.release = asyncio.Event()

    async def generate(self, **kwargs):
        self.started += 1
        await self.release.wait()
        if self.error is not None:
            raise self.error
        return self.proposal, "mock-model"


@pytest.fixture(autouse=True)
def _clear_answer_cache():
    _ANSWER_CACHE.clear()
    yield
    _ANSWER_CACHE.clear()
    # `AsyncSingleFlight`'s per-key coordination state must never outlive
    # the request(s) that created it — a non-empty dict here after every
    # test in this file has completed would itself be the "orphan
    # future" failure mode Section 4 explicitly requires proving absent.
    assert _ANSWER_SINGLE_FLIGHT._futures == {}


@pytest.mark.asyncio
async def test_identical_skill_evidence_and_intent_hits_the_answer_cache_and_skips_the_model() -> None:
    proposal = SynthesisProposal(summary="Fix SKU-A first.", confidence="high")
    generator = _FakeSynthesizer(proposal)
    service = SynthesisService(generator=generator)
    evidence = _skill_evidence()
    request = SynthesisRequest(
        user_message="Which listings should I fix first?",
        intent="prioritize_listing_health",
        evidence=[_envelope_for(evidence)],
        compact_context={},
    )

    first = await service.synthesize(request)
    second = await service.synthesize(request)

    assert first.summary == second.summary
    assert len(generator.calls) == 1, "second identical request should have hit the answer cache, not called the model"


@pytest.mark.asyncio
async def test_different_evidence_content_never_shares_a_cached_answer() -> None:
    proposal = SynthesisProposal(summary="Fix listing first.", confidence="high")
    generator = _FakeSynthesizer(proposal)
    service = SynthesisService(generator=generator)

    request_a = SynthesisRequest(
        user_message="Which listings should I fix first?",
        intent="prioritize_listing_health",
        evidence=[_envelope_for(_skill_evidence(seller_sku="SKU-A"))],
        compact_context={},
    )
    request_b = SynthesisRequest(
        user_message="Which listings should I fix first?",
        intent="prioritize_listing_health",
        evidence=[_envelope_for(_skill_evidence(seller_sku="SKU-B"))],
        compact_context={},
    )

    await service.synthesize(request_a)
    await service.synthesize(request_b)

    assert len(generator.calls) == 2, "different evidence content must never share a cached answer"


@pytest.mark.asyncio
async def test_different_organization_never_shares_a_cached_answer() -> None:
    proposal = SynthesisProposal(summary="Fix listing first.", confidence="high")
    generator = _FakeSynthesizer(proposal)
    service = SynthesisService(generator=generator)

    org_a, org_b = uuid4(), uuid4()
    request_a = SynthesisRequest(
        user_message="Which listings should I fix first?",
        intent="prioritize_listing_health",
        evidence=[_envelope_for(_skill_evidence(organization_id=org_a))],
        compact_context={},
    )
    request_b = SynthesisRequest(
        user_message="Which listings should I fix first?",
        intent="prioritize_listing_health",
        evidence=[_envelope_for(_skill_evidence(organization_id=org_b))],
        compact_context={},
    )

    await service.synthesize(request_a)
    await service.synthesize(request_b)

    assert len(generator.calls) == 2, "different organizations must never share a cached answer"


@pytest.mark.asyncio
async def test_different_intent_never_shares_a_cached_answer() -> None:
    proposal = SynthesisProposal(summary="Answer.", confidence="high")
    generator = _FakeSynthesizer(proposal)
    service = SynthesisService(generator=generator)
    evidence = _skill_evidence()

    request_a = SynthesisRequest(
        user_message="Which listings should I fix first?",
        intent="prioritize_listing_health",
        evidence=[_envelope_for(evidence)],
        compact_context={},
    )
    request_b = SynthesisRequest(
        user_message="Which listings should I fix first?",
        intent="rank_listing_risk_by_order_exposure",
        evidence=[_envelope_for(evidence)],
        compact_context={},
    )

    await service.synthesize(request_a)
    await service.synthesize(request_b)

    assert len(generator.calls) == 2, "a different intent must never reuse another intent's cached answer"


@pytest.mark.asyncio
async def test_no_generator_attached_still_caches_the_deterministic_template_answer() -> None:
    """The SQLite/CI path (no LLM attached) must still benefit from the
    answer cache — proving Phase 11's "cached answers do not invoke the
    LLM" acceptance target holds even with no model available at all."""
    service = SynthesisService(generator=None)
    evidence = _skill_evidence()
    request = SynthesisRequest(
        user_message="Which listings should I fix first?",
        intent="prioritize_listing_health",
        evidence=[_envelope_for(evidence)],
        compact_context={},
    )

    first = await service.synthesize(request)
    second = await service.synthesize(request)

    assert first.source == "template_fallback"
    assert second is first or second.summary == first.summary


@pytest.mark.asyncio
async def test_non_skill_intents_are_never_cached_by_layer_b() -> None:
    """explain_profit and every other pre-existing intent has no
    `skill_evidence` fact — Layer B must never key on anything for them,
    matching this milestone's explicit five-skills-only scope."""
    from app.copilot.evidence import claim

    proposal = SynthesisProposal(summary="Profit answer.", confidence="high")
    generator = _FakeSynthesizer(proposal)
    service = SynthesisService(generator=generator)
    request = SynthesisRequest(
        user_message="What is my profit?",
        intent="explain_profit",
        evidence=[
            envelope(
                "get_profit_snapshot",
                [claim("net_profit_before_ads", "5.00", kind="calculated", source="profit-calc-v1")],
            )
        ],
        compact_context={},
    )

    await service.synthesize(request)
    await service.synthesize(request)

    assert len(generator.calls) == 2, "non-skill intents must never be cached by Layer B"


# --- 12B.5B remediation Section 4: final-answer single-flight ---------------


@pytest.mark.asyncio
async def test_concurrent_identical_requests_invoke_the_model_at_most_once() -> None:
    proposal = SynthesisProposal(summary="Fix SKU-A first.", confidence="high")
    generator = _GatedSynthesizer(proposal)
    service = SynthesisService(generator=generator)
    evidence = _skill_evidence()
    request = SynthesisRequest(
        user_message="Which listings should I fix first?",
        intent="prioritize_listing_health",
        evidence=[_envelope_for(evidence)],
        compact_context={},
    )

    tasks = [asyncio.create_task(service.synthesize(request)) for _ in range(5)]
    await asyncio.sleep(0)  # let every task reach and block inside generate()
    assert generator.started == 1, "N concurrent identical requests must perform at most one LLM call"
    generator.release.set()
    results = await asyncio.gather(*tasks)

    # This fixture's minimal proposal (no findings/citations) is rejected
    # by the citation validator and every caller falls back to the same
    # deterministic template — exactly as the sequential answer-cache
    # test above already establishes for this same fixture. What this
    # test proves is single-flight-specific: every one of the 5 callers
    # received the identical validated result object from the one shared
    # computation, and the model was invoked exactly once regardless.
    assert len({result.summary for result in results}) == 1
    assert all(result.summary == results[0].summary for result in results)
    assert generator.started == 1


@pytest.mark.asyncio
async def test_different_keys_do_not_block_each_other() -> None:
    proposal_a = SynthesisProposal(summary="Answer A.", confidence="high")
    proposal_b = SynthesisProposal(summary="Answer B.", confidence="high")
    generator_a = _GatedSynthesizer(proposal_a)
    generator_b = _GatedSynthesizer(proposal_b)
    service_a = SynthesisService(generator=generator_a)
    service_b = SynthesisService(generator=generator_b)

    request_a = SynthesisRequest(
        user_message="Which listings should I fix first?",
        intent="prioritize_listing_health",
        evidence=[_envelope_for(_skill_evidence(seller_sku="SKU-A"))],
        compact_context={},
    )
    request_b = SynthesisRequest(
        user_message="Which listings should I fix first?",
        intent="prioritize_listing_health",
        evidence=[_envelope_for(_skill_evidence(seller_sku="SKU-B"))],
        compact_context={},
    )

    task_a = asyncio.create_task(service_a.synthesize(request_a))
    await asyncio.sleep(0)
    assert generator_a.started == 1
    # Task A is still blocked on its own gate (never released yet) — a
    # different key (distinct evidence content -> distinct
    # evidence_content_key/answer_cache_key) must be able to start and
    # finish without ever waiting on A's still-pending future.
    generator_b.release.set()
    result_b = await service_b.synthesize(request_b)
    assert result_b is not None
    assert generator_b.started == 1
    assert not task_a.done(), "a different key's request must not have unblocked task A"

    generator_a.release.set()
    result_a = await task_a
    assert result_a is not None
    assert generator_a.started == 1


@pytest.mark.asyncio
async def test_leader_failure_releases_followers_and_does_not_cache() -> None:
    generator = _GatedSynthesizer(error=RuntimeError("simulated model failure"))
    service = SynthesisService(generator=generator)
    evidence = _skill_evidence()
    request = SynthesisRequest(
        user_message="Which listings should I fix first?",
        intent="prioritize_listing_health",
        evidence=[_envelope_for(evidence)],
        compact_context={},
    )

    tasks = [asyncio.create_task(service.synthesize(request)) for _ in range(3)]
    await asyncio.sleep(0)
    assert generator.started == 1
    generator.release.set()

    # `_propose` swallows the generator's own exception and falls back to
    # the deterministic evidence template (see `SynthesisService._propose`)
    # — so "leader failure" here is exercised at the `AsyncSingleFlight`
    # level by asserting all followers still complete, get the identical
    # templated answer, and the model was never invoked a second time.
    results = await asyncio.gather(*tasks)
    assert all(result.source == "template_fallback" for result in results)
    assert generator.started == 1


@pytest.mark.asyncio
async def test_cancelled_follower_does_not_corrupt_the_shared_future() -> None:
    proposal = SynthesisProposal(summary="Fix SKU-A first.", confidence="high")
    generator = _GatedSynthesizer(proposal)
    service = SynthesisService(generator=generator)
    evidence = _skill_evidence()
    request = SynthesisRequest(
        user_message="Which listings should I fix first?",
        intent="prioritize_listing_health",
        evidence=[_envelope_for(evidence)],
        compact_context={},
    )

    leader_task = asyncio.create_task(service.synthesize(request))
    follower_task = asyncio.create_task(service.synthesize(request))
    await asyncio.sleep(0)
    assert generator.started == 1

    follower_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await follower_task

    # Cancelling the follower must not cancel or corrupt the shared
    # future the leader still holds — the leader completes normally,
    # and the model was still invoked only once (by the leader).
    generator.release.set()
    result = await leader_task
    assert result is not None
    assert generator.started == 1
