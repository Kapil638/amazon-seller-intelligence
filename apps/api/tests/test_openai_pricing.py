from datetime import datetime

from app.usage.openai_pricing import (
    PRICING_VERSION,
    estimate_openai_cost_usd,
    lookup_openai_pricing,
)
from app.usage.ledger import ApplicationUsageLedger


def test_pricing_table_is_versioned() -> None:
    pricing = lookup_openai_pricing("gpt-5.4")
    assert pricing is not None
    assert pricing.pricing_version == PRICING_VERSION
    assert pricing.input_price_per_million == 2.50
    assert pricing.cached_input_price_per_million == 0.25
    assert pricing.output_price_per_million == 15.00


def test_snapshot_alias_uses_canonical_gpt_54_price() -> None:
    assert lookup_openai_pricing("gpt-5.4-2026-03-05") is not None
    assert lookup_openai_pricing("gpt-5.4-2026-03-05").model == "gpt-5.4"


def test_unknown_model_does_not_invent_cost() -> None:
    assert lookup_openai_pricing("definitely-not-a-model") is None
    assert (
        estimate_openai_cost_usd(
            model="mystery-model",
            input_tokens=1000,
            cached_input_tokens=100,
            output_tokens=50,
        )
        is None
    )


def test_estimated_cost_uses_input_cached_and_output_rates() -> None:
    cost = estimate_openai_cost_usd(
        model="gpt-5.4",
        input_tokens=1_000_000,
        cached_input_tokens=0,
        output_tokens=1_000_000,
    )
    assert cost == 17.5


def test_cached_input_tokens_use_cached_rate() -> None:
    cost = estimate_openai_cost_usd(
        model="gpt-5.4",
        input_tokens=1_000_000,
        cached_input_tokens=1_000_000,
        output_tokens=0,
    )
    assert cost == 0.25


def test_cached_tokens_cannot_exceed_input_tokens() -> None:
    cost = estimate_openai_cost_usd(
        model="gpt-5.4",
        input_tokens=100,
        cached_input_tokens=500,
        output_tokens=0,
    )
    uncached_only = estimate_openai_cost_usd(
        model="gpt-5.4",
        input_tokens=100,
        cached_input_tokens=100,
        output_tokens=0,
    )
    assert cost == uncached_only


def test_ledger_records_tokens_when_cost_unavailable() -> None:
    ledger = ApplicationUsageLedger()
    cost = ledger.record_openai_call(
        workflow="listing_intelligence",
        model="unknown-model",
        input_tokens=90,
        cached_input_tokens=10,
        output_tokens=20,
        total_tokens=110,
    )
    assert cost is None
    snapshot = ledger.openai_app_snapshot()
    assert snapshot.requests == 1
    assert snapshot.input_tokens == 90
    assert snapshot.cached_input_tokens == 10
    assert snapshot.output_tokens == 20
    assert snapshot.total_tokens == 110
    assert snapshot.estimated_spend_usd is None
    assert snapshot.cost_status == "unavailable"
    assert snapshot.unpriced_requests == 1
    assert ledger.openai_calls[0].timestamp.tzinfo is not None
    assert isinstance(ledger.openai_calls[0].timestamp, datetime)
    assert ledger.openai_calls[0].workflow == "listing_intelligence"


def test_ledger_accumulates_priced_openai_calls() -> None:
    ledger = ApplicationUsageLedger()
    ledger.record_openai_call(
        workflow="listing_intelligence",
        model="gpt-5.4",
        input_tokens=1_000_000,
        output_tokens=0,
    )
    ledger.record_openai_call(
        workflow="competitive_intelligence",
        model="gpt-5.4",
        input_tokens=0,
        output_tokens=1_000_000,
    )
    snapshot = ledger.openai_app_snapshot()
    assert snapshot.requests == 2
    assert snapshot.estimated_spend_usd == 17.5
    assert snapshot.cost_status == "ok"
    assert snapshot.total_tokens == 2_000_000
