"""12B.6A — `sales_traffic_evidence_version` (app.copilot.skills.contracts).
Mechanism-ready-only proof: no launch skill reads Sales and Traffic data
yet, but the version function itself must behave correctly the moment a
future skill starts using it. No SP-API client, database, or worker
anywhere in this file.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.copilot.skills.contracts import sales_traffic_evidence_version


def _sync(**overrides):
    from app.amazon.sales_traffic_read import SalesTrafficSyncEvidence

    return SalesTrafficSyncEvidence(**overrides)


def test_none_sync_returns_the_double_none_sentinel() -> None:
    assert sales_traffic_evidence_version(None) == "none|none"


def test_never_synchronized_returns_the_double_none_sentinel() -> None:
    assert sales_traffic_evidence_version(_sync()) == "none|none"


def test_successful_run_alone_changes_the_first_half_only() -> None:
    completed = datetime(2026, 8, 1, tzinfo=UTC)
    version = sales_traffic_evidence_version(_sync(last_successful_synchronized_at=completed))
    assert version == f"{completed.isoformat()}|none"


def test_checkpoint_advance_alone_changes_the_second_half_only() -> None:
    version = sales_traffic_evidence_version(_sync(synced_through_date=date(2026, 8, 1)))
    assert version == "none|2026-08-01"


def test_either_signal_advancing_independently_changes_the_version() -> None:
    """A catalog-wide-only run can succeed without ever moving the daily
    checkpoint, and a checkpoint only ever advances inside a successful
    run — but the version must still change whenever *either* signal
    moves, since a cache built before either advance is stale evidence
    either way."""
    baseline = sales_traffic_evidence_version(_sync())

    only_run = sales_traffic_evidence_version(
        _sync(last_successful_synchronized_at=datetime(2026, 8, 1, tzinfo=UTC))
    )
    assert only_run != baseline

    only_checkpoint = sales_traffic_evidence_version(_sync(synced_through_date=date(2026, 8, 1)))
    assert only_checkpoint != baseline
    assert only_checkpoint != only_run


def test_version_is_stable_for_identical_evidence() -> None:
    sync = _sync(
        last_successful_synchronized_at=datetime(2026, 8, 1, tzinfo=UTC), synced_through_date=date(2026, 8, 1)
    )
    assert sales_traffic_evidence_version(sync) == sales_traffic_evidence_version(sync)
