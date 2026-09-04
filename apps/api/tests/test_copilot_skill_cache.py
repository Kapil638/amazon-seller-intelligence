"""12B.5B — unit tests for the skill evidence/answer cache primitives
(`app/copilot/skills/cache.py`). Pure in-process objects, no database, no
Amazon/AI-provider call anywhere in this file. Every test builds its own
fresh cache/single-flight instances — never the module-level singletons
`app/copilot/tools/skills.py` shares process-wide — so these tests never
depend on, or pollute, any other test's cache state."""

from __future__ import annotations

import threading
from uuid import uuid4

from app.copilot.skills.cache import (
    InProcessSkillCache,
    SingleFlight,
    answer_cache_key,
    cached_evidence_lookup,
    evidence_cache_key,
    evidence_content_key,
)


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_cache_hit_avoids_recompute() -> None:
    cache = InProcessSkillCache()
    single_flight = SingleFlight()
    calls = []

    def compute():
        calls.append(1)
        return "value"

    key = "k"
    assert cached_evidence_lookup(cache, single_flight, key=key, compute=compute) == "value"
    assert cached_evidence_lookup(cache, single_flight, key=key, compute=compute) == "value"
    assert len(calls) == 1


def test_cache_miss_for_different_key_recomputes() -> None:
    cache = InProcessSkillCache()
    single_flight = SingleFlight()
    calls = []

    def compute():
        calls.append(1)
        return "value"

    cached_evidence_lookup(cache, single_flight, key="a", compute=compute)
    cached_evidence_lookup(cache, single_flight, key="b", compute=compute)
    assert len(calls) == 2


def test_ttl_expiry_forces_recompute_using_injectable_clock() -> None:
    clock = _FakeClock()
    cache = InProcessSkillCache(clock=clock)
    single_flight = SingleFlight()
    calls = []

    def compute():
        calls.append(1)
        return "value"

    cached_evidence_lookup(cache, single_flight, key="k", compute=compute, ttl_seconds=10)
    assert len(calls) == 1
    clock.advance(5)
    cached_evidence_lookup(cache, single_flight, key="k", compute=compute, ttl_seconds=10)
    assert len(calls) == 1  # still within TTL
    clock.advance(6)
    cached_evidence_lookup(cache, single_flight, key="k", compute=compute, ttl_seconds=10)
    assert len(calls) == 2  # TTL expired — recomputed


def test_cache_backend_failure_falls_back_to_compute() -> None:
    class BrokenCache:
        def get(self, key):
            raise RuntimeError("backend down")

        def set(self, key, value, ttl_seconds):
            raise RuntimeError("backend down")

        def delete(self, key):
            raise RuntimeError("backend down")

    single_flight = SingleFlight()
    calls = []

    def compute():
        calls.append(1)
        return "value"

    result = cached_evidence_lookup(BrokenCache(), single_flight, key="k", compute=compute)
    assert result == "value"
    assert len(calls) == 1
    # A second call also degrades to "always compute" — a broken cache
    # must never break a skill, but it also can never fake a cache hit.
    result2 = cached_evidence_lookup(BrokenCache(), single_flight, key="k", compute=compute)
    assert result2 == "value"
    assert len(calls) == 2


def test_failed_compute_is_never_cached() -> None:
    cache = InProcessSkillCache()
    single_flight = SingleFlight()
    attempts = []

    def compute():
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError("boom")
        return "recovered"

    try:
        cached_evidence_lookup(cache, single_flight, key="k", compute=compute)
    except ValueError:
        pass
    else:
        raise AssertionError("expected the first attempt to raise")

    result = cached_evidence_lookup(cache, single_flight, key="k", compute=compute)
    assert result == "recovered"
    assert len(attempts) == 2


def test_single_flight_coalesces_concurrent_identical_key_callers() -> None:
    """Only the one "leader" thread that wins the race ever calls
    `compute()` — every other concurrent caller for the identical key
    waits and receives that same result, never triggering its own
    computation. `entered` proves exactly one thread reached `compute()`
    at all before `release` was set."""
    cache = InProcessSkillCache()
    single_flight = SingleFlight()
    entered = threading.Event()
    release = threading.Event()
    call_count = 0
    call_count_lock = threading.Lock()

    def compute():
        nonlocal call_count
        with call_count_lock:
            call_count += 1
        entered.set()
        release.wait(timeout=5)
        return "value"

    results = []
    results_lock = threading.Lock()

    def worker():
        value = cached_evidence_lookup(cache, single_flight, key="shared", compute=compute)
        with results_lock:
            results.append(value)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    assert entered.wait(timeout=5), "leader never reached compute()"
    release.set()
    for t in threads:
        t.join(timeout=5)

    assert results == ["value"] * 10
    assert call_count == 1


def test_evidence_cache_key_differs_by_organization() -> None:
    org_a, org_b, participation = uuid4(), uuid4(), uuid4()
    common = dict(
        marketplace_participation_ids=[participation],
        skill_id="listing_health_prioritizer",
        skill_version="2.0.0",
        params={"period_days": 30},
        listings_evidence_version="none",
        orders_evidence_version="none",
    )
    key_a = evidence_cache_key(organization_id=org_a, **common)
    key_b = evidence_cache_key(organization_id=org_b, **common)
    assert key_a != key_b


def test_evidence_cache_key_differs_by_marketplace_participation() -> None:
    org, participation_a, participation_b = uuid4(), uuid4(), uuid4()
    common = dict(
        organization_id=org,
        skill_id="listing_health_prioritizer",
        skill_version="2.0.0",
        params={"period_days": 30},
        listings_evidence_version="none",
        orders_evidence_version="none",
    )
    key_a = evidence_cache_key(marketplace_participation_ids=[participation_a], **common)
    key_b = evidence_cache_key(marketplace_participation_ids=[participation_b], **common)
    assert key_a != key_b


def test_evidence_cache_key_differs_by_skill_version() -> None:
    org, participation = uuid4(), uuid4()
    common = dict(
        organization_id=org,
        marketplace_participation_ids=[participation],
        skill_id="listing_health_prioritizer",
        params={"period_days": 30},
        listings_evidence_version="none",
        orders_evidence_version="none",
    )
    key_v1 = evidence_cache_key(skill_version="1.0.0", **common)
    key_v2 = evidence_cache_key(skill_version="2.0.0", **common)
    assert key_v1 != key_v2


def test_evidence_cache_key_differs_by_period_days() -> None:
    org, participation = uuid4(), uuid4()
    common = dict(
        organization_id=org,
        marketplace_participation_ids=[participation],
        skill_id="order_and_sales_trend_analyst",
        skill_version="2.0.0",
        listings_evidence_version=None,
        orders_evidence_version="none",
    )
    key_7 = evidence_cache_key(params={"period_days": 7}, **common)
    key_30 = evidence_cache_key(params={"period_days": 30}, **common)
    assert key_7 != key_30


def test_evidence_cache_key_changes_when_listings_evidence_version_changes() -> None:
    """A successful Listings ingestion changing the evidence version
    must naturally produce a different key — this is the entire
    invalidation mechanism for Layer A, with no explicit cache-clearing
    call anywhere."""
    org, participation = uuid4(), uuid4()
    common = dict(
        organization_id=org,
        marketplace_participation_ids=[participation],
        skill_id="listing_health_prioritizer",
        skill_version="2.0.0",
        params={"period_days": 30},
        orders_evidence_version="none",
    )
    key_before = evidence_cache_key(listings_evidence_version="none", **common)
    key_after = evidence_cache_key(listings_evidence_version="2026-09-01T00:00:00+00:00", **common)
    assert key_before != key_after


def test_evidence_cache_key_changes_when_orders_evidence_version_changes() -> None:
    org, participation = uuid4(), uuid4()
    common = dict(
        organization_id=org,
        marketplace_participation_ids=[participation],
        skill_id="order_and_sales_trend_analyst",
        skill_version="2.0.0",
        params={"period_days": 30},
        listings_evidence_version=None,
    )
    key_before = evidence_cache_key(orders_evidence_version="none", **common)
    key_after = evidence_cache_key(orders_evidence_version="2026-09-03T00:00:00+00:00", **common)
    assert key_before != key_after


def test_evidence_cache_key_defaults_sales_traffic_version_to_none_unaffecting_existing_callers() -> None:
    """12B.6A — mechanism-ready only: an existing Listings/Orders call
    site that never passes `sales_traffic_evidence_version` must produce
    the exact same key as one that passes it explicitly as `None`."""
    org, participation = uuid4(), uuid4()
    common = dict(
        organization_id=org,
        marketplace_participation_ids=[participation],
        skill_id="listing_health_prioritizer",
        skill_version="2.0.0",
        params={"period_days": 30},
        listings_evidence_version="none",
        orders_evidence_version="none",
    )
    key_omitted = evidence_cache_key(**common)
    key_explicit_none = evidence_cache_key(sales_traffic_evidence_version=None, **common)
    assert key_omitted == key_explicit_none


def test_evidence_cache_key_changes_when_sales_traffic_evidence_version_changes() -> None:
    org, participation = uuid4(), uuid4()
    common = dict(
        organization_id=org,
        marketplace_participation_ids=[participation],
        skill_id="listing_health_prioritizer",
        skill_version="2.0.0",
        params={"period_days": 30},
        listings_evidence_version="none",
        orders_evidence_version="none",
    )
    key_before = evidence_cache_key(sales_traffic_evidence_version="none|none", **common)
    key_after = evidence_cache_key(sales_traffic_evidence_version="2026-09-03T00:00:00+00:00|2026-09-01", **common)
    assert key_before != key_after


def test_evidence_cache_key_is_stable_regardless_of_marketplace_id_order() -> None:
    org = uuid4()
    p1, p2 = uuid4(), uuid4()
    common = dict(
        organization_id=org,
        skill_id="listing_health_prioritizer",
        skill_version="2.0.0",
        params={"period_days": 30},
        listings_evidence_version="none",
        orders_evidence_version="none",
    )
    key_order_1 = evidence_cache_key(marketplace_participation_ids=[p1, p2], **common)
    key_order_2 = evidence_cache_key(marketplace_participation_ids=[p2, p1], **common)
    assert key_order_1 == key_order_2


def test_answer_cache_key_differs_by_intent_and_model() -> None:
    evidence_key = "skill_evidence:abc"
    key_a = answer_cache_key(evidence_key=evidence_key, intent="prioritize_listing_health", prompt_version="v1", model="gpt-a", provider="openai")
    key_b = answer_cache_key(evidence_key=evidence_key, intent="analyze_order_trends", prompt_version="v1", model="gpt-a", provider="openai")
    key_c = answer_cache_key(evidence_key=evidence_key, intent="prioritize_listing_health", prompt_version="v1", model="gpt-b", provider="openai")
    assert key_a != key_b
    assert key_a != key_c


def test_answer_cache_key_differs_by_prompt_version() -> None:
    evidence_key = "skill_evidence:abc"
    key_v1 = answer_cache_key(evidence_key=evidence_key, intent="prioritize_listing_health", prompt_version="v1", model="gpt-a", provider="openai")
    key_v2 = answer_cache_key(evidence_key=evidence_key, intent="prioritize_listing_health", prompt_version="v2", model="gpt-a", provider="openai")
    assert key_v1 != key_v2


# --- 12B.5B remediation Section 2: evidence_content_key completeness -------


def _skill_evidence_dict(**overrides) -> dict:
    base = {
        "skill_id": "listing_health_prioritizer",
        "skill_version": "1.1.0",
        "organization_id": str(uuid4()),
        "marketplace_participation_ids": [str(uuid4())],
        "metrics": {"total_listings": 10},
        "records": [{"seller_sku": "SKU-A"}],
        "confidence": "high",
    }
    base.update(overrides)
    return base


def test_evidence_content_key_is_identical_for_identical_evidence() -> None:
    evidence = _skill_evidence_dict()
    assert evidence_content_key(evidence) == evidence_content_key(dict(evidence))


def test_evidence_content_key_changes_when_confidence_degrades() -> None:
    """The exact proof the 12B.5B remediation asked for: a failed/
    partial/stale sync transition that downgrades a skill's `confidence`
    (see e.g. listing_risk.py's `majority_unmatched` rule or any skill's
    `incomplete_run()` check) must never let a previously-cached "high
    confidence, fresh evidence" answer be reused once that evidence's own
    `confidence` field has changed — even if `metrics`/`records` happen
    to be byte-identical, which is exactly the scenario where a naive
    key that ignored `confidence` could otherwise collide."""
    org_id = str(uuid4())
    participation_id = str(uuid4())
    fresh = _skill_evidence_dict(organization_id=org_id, marketplace_participation_ids=[participation_id], confidence="high")
    degraded = _skill_evidence_dict(
        organization_id=org_id, marketplace_participation_ids=[participation_id], confidence="medium"
    )
    assert evidence_content_key(fresh) != evidence_content_key(degraded)


def test_evidence_content_key_is_stable_regardless_of_marketplace_participation_order() -> None:
    org_id = str(uuid4())
    p1, p2 = str(uuid4()), str(uuid4())
    key_order_1 = evidence_content_key(
        _skill_evidence_dict(organization_id=org_id, marketplace_participation_ids=[p1, p2])
    )
    key_order_2 = evidence_content_key(
        _skill_evidence_dict(organization_id=org_id, marketplace_participation_ids=[p2, p1])
    )
    assert key_order_1 == key_order_2


def test_evidence_content_key_differs_by_skill_version() -> None:
    key_v1 = evidence_content_key(_skill_evidence_dict(skill_version="1.0.0"))
    key_v2 = evidence_content_key(_skill_evidence_dict(skill_version="1.1.0"))
    assert key_v1 != key_v2


def test_evidence_content_key_differs_by_organization() -> None:
    key_a = evidence_content_key(_skill_evidence_dict(organization_id=str(uuid4())))
    key_b = evidence_content_key(_skill_evidence_dict(organization_id=str(uuid4())))
    assert key_a != key_b


def test_evidence_content_key_differs_by_metrics_content() -> None:
    key_a = evidence_content_key(_skill_evidence_dict(metrics={"total_listings": 10}))
    key_b = evidence_content_key(_skill_evidence_dict(metrics={"total_listings": 11}))
    assert key_a != key_b


def test_evidence_content_key_changes_when_full_population_aggregate_changes_with_identical_top_n_records() -> None:
    """Final safety/bounded-evidence review, Cancellation bounding
    proof: two evidence payloads can share byte-identical top-N
    `records` (e.g. the same top 25 affected SKUs) while the full-
    population aggregate behind them differs (e.g. `affected_sku_count`
    grew from 30 to 40, or `cancellation_rate` moved) — the answer
    cache must never treat these as the same cached answer merely
    because the *displayed* records happen to match."""
    org_id = str(uuid4())
    participation_id = str(uuid4())
    shared_top_n_records = [{"kind": "sku_on_cancelled_order", "seller_sku": f"SKU-{i:04d}"} for i in range(25)]

    evidence_before = _skill_evidence_dict(
        organization_id=org_id,
        marketplace_participation_ids=[participation_id],
        skill_id="cancellation_operational_anomaly_detector",
        records=shared_top_n_records,
        metrics={"affected_sku_count": 30, "cancellation_rate": 0.12, "returned_sku_count": 25},
    )
    evidence_after = _skill_evidence_dict(
        organization_id=org_id,
        marketplace_participation_ids=[participation_id],
        skill_id="cancellation_operational_anomaly_detector",
        records=shared_top_n_records,
        metrics={"affected_sku_count": 40, "cancellation_rate": 0.15, "returned_sku_count": 25},
    )

    assert evidence_content_key(evidence_before) != evidence_content_key(evidence_after)


# --- 12B.5B remediation Section 3: bounding the in-process cache -----------


def test_cache_rejects_a_non_positive_max_entries() -> None:
    import pytest

    with pytest.raises(ValueError):
        InProcessSkillCache(max_entries=0)


def test_cache_evicts_least_recently_used_entry_when_over_capacity() -> None:
    cache = InProcessSkillCache(max_entries=2)
    cache.set("a", "value-a", ttl_seconds=60)
    cache.set("b", "value-b", ttl_seconds=60)
    cache.set("c", "value-c", ttl_seconds=60)  # over capacity -> evicts "a" (least recently touched)

    assert cache.size() == 2
    assert cache.get("a") is None
    assert cache.get("b") == "value-b"
    assert cache.get("c") == "value-c"


def test_cache_get_counts_as_recent_use_for_eviction_ordering() -> None:
    cache = InProcessSkillCache(max_entries=2)
    cache.set("a", "value-a", ttl_seconds=60)
    cache.set("b", "value-b", ttl_seconds=60)
    cache.get("a")  # touching "a" makes "b" the least-recently-used entry
    cache.set("c", "value-c", ttl_seconds=60)  # evicts "b", not "a"

    assert cache.get("b") is None
    assert cache.get("a") == "value-a"
    assert cache.get("c") == "value-c"


def test_cache_never_grows_past_max_entries_across_many_unique_keys() -> None:
    cache = InProcessSkillCache(max_entries=50)
    for i in range(1000):
        cache.set(f"key-{i}", i, ttl_seconds=60)
    assert cache.size() <= 50


def test_purge_expired_removes_only_elapsed_entries_using_injectable_clock() -> None:
    clock = _FakeClock()
    cache = InProcessSkillCache(clock=clock)
    cache.set("expires-soon", "v1", ttl_seconds=10)
    cache.set("expires-later", "v2", ttl_seconds=100)
    clock.advance(50)

    removed = cache.purge_expired()

    assert removed == 1
    assert cache.size() == 1
    assert cache.get("expires-later") == "v2"


def test_single_flight_leaves_no_residual_state_for_a_completed_key() -> None:
    """12B.5B remediation regression test: an earlier version of
    `SingleFlight` wrote `self._results[key]` on every call but never
    removed it, leaking one entry per unique key for the life of the
    process. Both bookkeeping dicts must be empty again once a key's
    computation (success or failure) has fully completed and every
    caller has returned."""
    single_flight = SingleFlight()

    assert single_flight.run("k1", lambda: "value") == "value"
    assert single_flight._events == {}
    assert single_flight._results == {}

    try:
        single_flight.run("k2", lambda: (_ for _ in ()).throw(ValueError("boom")))
    except ValueError:
        pass
    assert single_flight._events == {}
    assert single_flight._results == {}


def test_single_flight_does_not_leak_across_many_unique_keys() -> None:
    single_flight = SingleFlight()
    for i in range(1000):
        single_flight.run(f"key-{i}", lambda: "value")
    assert single_flight._events == {}
    assert single_flight._results == {}


def test_evidence_key_never_contains_raw_scope_strings_only_a_digest() -> None:
    org, participation = uuid4(), uuid4()
    key = evidence_cache_key(
        organization_id=org,
        marketplace_participation_ids=[participation],
        skill_id="listing_health_prioritizer",
        skill_version="2.0.0",
        params={"period_days": 30},
        listings_evidence_version="none",
        orders_evidence_version="none",
    )
    assert str(org) not in key
    assert str(participation) not in key
    assert key.startswith("skill_evidence:")
