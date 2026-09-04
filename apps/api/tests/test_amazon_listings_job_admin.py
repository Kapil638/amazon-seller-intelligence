"""Final safety/bounded-evidence review — tests for `app.amazon.
listings_job_admin`, the operator-only Listings job maintenance CLI.
No live Amazon call anywhere in this file (the module itself never
makes one). Reuses `test_amazon_listings_worker.py`'s own seeding
helpers rather than duplicating them.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.amazon.listings_job_admin import main, terminalize_queued_listings_job
from tests.test_amazon_listings_worker import _enqueue, _seed_scope


@pytest.fixture(autouse=True)
def _clean_db_runtime_context():
    """`main()` sets `ASI_DB_RUNTIME_CONTEXT` directly via
    `os.environ[...] = ...`, not via `monkeypatch` — clean up
    explicitly so it never leaks into another test in the suite."""
    os.environ.pop("ASI_DB_RUNTIME_CONTEXT", None)
    yield
    os.environ.pop("ASI_DB_RUNTIME_CONTEXT", None)


def test_main_declares_the_admin_db_runtime_context_before_parsing_args() -> None:
    """Proves the real `main()` sets `ASI_DB_RUNTIME_CONTEXT=admin` —
    the exact declaration `app.persistence.database`'s production-
    database guard checks — unconditionally at entry, even when the
    supplied arguments turn out to be invalid. This CLI is never
    imported or started by accident (only `if __name__ == "__main__"`
    invokes it), so declaring unconditionally at entry is not a new
    opt-in surface — the entry point itself already is the operator's
    explicit action."""
    exit_code = main(["terminalize-queued", "--organization-id", "not-a-uuid", "--run-id", "also-not-a-uuid"])
    assert exit_code == 2
    assert os.environ.get("ASI_DB_RUNTIME_CONTEXT") == "admin"


def test_main_rejects_invalid_uuids() -> None:
    exit_code = main(["terminalize-queued", "--organization-id", "not-a-uuid", "--run-id", str(uuid4())])
    assert exit_code == 2


def test_terminalize_queued_listings_job_succeeds_for_a_genuinely_queued_unclaimed_run() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    assert claim.claimed, claim.reason

    succeeded = terminalize_queued_listings_job(scope["organization_id"], claim.run_id, reason="cancelled_before_start")

    assert succeeded is True


def test_terminalize_queued_listings_job_fails_closed_for_a_nonexistent_run() -> None:
    scope = _seed_scope()
    succeeded = terminalize_queued_listings_job(scope["organization_id"], uuid4(), reason="cancelled_before_start")
    assert succeeded is False


def test_main_end_to_end_reports_success_and_conflict_via_exit_code() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    assert claim.claimed, claim.reason

    success_exit = main(
        ["terminalize-queued", "--organization-id", str(scope["organization_id"]), "--run-id", str(claim.run_id)]
    )
    assert success_exit == 0

    # The same run is now terminal — a second attempt must report the
    # conflict path, never silently "succeed" again.
    conflict_exit = main(
        ["terminalize-queued", "--organization-id", str(scope["organization_id"]), "--run-id", str(claim.run_id)]
    )
    assert conflict_exit == 1
