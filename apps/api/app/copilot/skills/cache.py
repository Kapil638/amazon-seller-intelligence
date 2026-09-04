"""12B.5B — cache layers for the five Listings/Orders Copilot skills.

Two cache layers live here:

- **Layer A (evidence cache):** compact `SkillEvidence` keyed by
  organization/marketplace scope, skill identity/version, normalized
  parameters, and the current Listings/Orders evidence versions (see
  `evidence_version()` below). A successful sync naturally changes its
  evidence version, which naturally changes every key derived from it —
  invalidation falls out of key composition, not an explicit "clear"
  call anywhere in this module.
- **Layer B (final-answer cache):** completed, *validated* seller
  answers keyed by everything Layer A uses plus the normalized intent,
  prompt-template version, response-schema version, model, and
  provider. Only ever written with an already-validated
  `SynthesizedResponse` — see `app.copilot.synthesis.service`, which
  never writes an unvalidated, failed, or partial response here.

Storage: `InProcessSkillCache`, an in-process TTL dict guarded by a
`threading.Lock`, in the exact style already established by
`app.bulk.cache.KeyedTtlCache` (this module intentionally does not
import that one — skills evidence and bulk product caching are
unrelated features that should not share a class, even though the
shape is deliberately the same for consistency).

**This cache is process-local and is NOT sufficient for a deployment
running more than one API replica.** Two replicas each hold their own
`InProcessSkillCache` instance; a write in one is invisible to the
other, so two replicas can legitimately disagree about whether a given
key is cached at all, and — more importantly — an invalidating event
(a new Listings/Orders sync completing) changes evidence versions
everywhere at once (the version is derived from database state, not
from cache state), so a stale ENTRY is never served past that point on
any replica; what differs is only whether a given replica has to
recompute or can reuse its own prior warm entry. Correctness is
therefore preserved even with a process-local cache; only the *cache
hit rate* — not correctness — degrades with more replicas. **A
production deployment running more than one replica should still
replace this with a shared backend (e.g. Redis) behind the same
`SkillCache` protocol below**, so replicas can share hit rate rather
than each warming their own copy independently; this module does not
implement that backend, per this milestone's explicit instruction not
to introduce a new production dependency by assumption.

Every cache failure (a backend exception on get/set) is caught here and
treated as a miss / no-op — a broken cache must never break a skill.
No cache key or log line here ever contains a raw seller identifier,
question, or answer text; keys are opaque SHA-256 hex digests over a
sanitized, canonicalized payload (see `_hash_key`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import Any, Awaitable, Callable, Protocol, TypeVar
from uuid import UUID

T = TypeVar("T")

# Bump whenever a formula constant used by any of the five skills changes
# (minimum sample sizes, anomaly thresholds, ranking tie-break rules,
# result limits, etc.) — coarse by design, mirrors how `skill_version`
# already governs the evidence *contract shape*: this governs the
# *formula constants* used to fill that shape. See each skill module's
# own constants (`MIN_SAMPLE_SIZE`, `ANOMALY_RELATIVE_INCREASE`, ...).
SKILL_CONFIG_VERSION = "1"

# Governs the shape of `SkillEvidence`/`SynthesizedResponse` as sent to
# a cache consumer — bump if either contract's field set changes in a
# way that would make an old cached value structurally wrong to return
# from a call site expecting the new shape.
RESPONSE_SCHEMA_VERSION = "1"

DEFAULT_EVIDENCE_CACHE_TTL_SECONDS = 120
DEFAULT_ANSWER_CACHE_TTL_SECONDS = 120


class SkillCache(Protocol):
    """Storage interface every cache layer in this module is written
    against — never a concrete class — so a production shared backend
    (Redis or equivalent) can be substituted without changing any call
    site in `app/copilot/tools/skills.py` or `app/copilot/synthesis/`."""

    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any, ttl_seconds: int) -> None: ...

    def delete(self, key: str) -> None: ...


# 12B.5B remediation (Section 3): a production API process must not
# acquire an unbounded memory leak merely because the cache backend
# happens to be local. TTL expiry alone does not bound memory — an
# expired entry still occupies space until something touches its exact
# key again, and a process that only ever sees new, never-repeated keys
# (a plausible pattern across many organizations x marketplaces x
# periods x params combinations) would otherwise grow without limit for
# the life of the process. This cap is deliberately generous relative to
# any single API process's realistic working set (five skills, a
# handful of period/param variations, per organization/marketplace) —
# large enough that eviction should be rare in normal operation, small
# enough that worst-case memory is bounded and known.
DEFAULT_MAX_CACHE_ENTRIES = 5000


class InProcessSkillCache:
    """Process-local TTL cache, bounded by `max_entries` with
    deterministic least-recently-used eviction. See module docstring for
    the multi-replica limitation and the production topology
    requirement."""

    def __init__(
        self,
        clock: Callable[[], float] = monotonic,
        *,
        max_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._clock = clock
        self._max_entries = max_entries
        # `OrderedDict` gives O(1) "move to most-recently-used" on every
        # `get`/`set` (`move_to_end`) and O(1) "evict the least-recently-
        # used" (`popitem(last=False)`) — standard-library LRU, not a
        # pattern borrowed from `app.bulk.cache.KeyedTtlCache` (that
        # cache is TTL-only and unbounded; unrelated to this one, per
        # this module's own docstring).
        self._data: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires_at, value = item
            if self._clock() >= expires_at:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        with self._lock:
            self._data[key] = (self._clock() + max(ttl_seconds, 0), value)
            self._data.move_to_end(key)
            while len(self._data) > self._max_entries:
                # Deterministic eviction: always the least-recently
                # touched (by `get` or `set`) entry, never an arbitrary
                # or random choice — `popitem(last=False)` on an
                # `OrderedDict` is exactly "pop the oldest-at-the-front"
                # by construction.
                self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._data)

    def purge_expired(self) -> int:
        """Explicit sweep for entries whose TTL has already elapsed but
        that no caller has touched since (so lazy expiry-on-`get` never
        ran for them). Not required for correctness — `get()` already
        guarantees a stale value is never returned, and `max_entries`
        already bounds worst-case memory regardless — but lets an
        operator reclaim memory proactively (e.g. from a periodic
        housekeeping task) rather than waiting for LRU eviction to catch
        up. Returns the number of entries removed."""
        with self._lock:
            now = self._clock()
            expired = [key for key, (expires_at, _value) in self._data.items() if now >= expires_at]
            for key in expired:
                del self._data[key]
            return len(expired)


@dataclass
class _PendingResult:
    ok: bool
    value: Any = None
    error: BaseException | None = None


class SingleFlight:
    """Ensures concurrent callers computing the *identical* key share one
    underlying computation rather than each recomputing independently —
    "single-flight/request coalescing" (Phase 7). One instance is shared
    process-wide per cache layer; safe across threads (the actual
    computation these skills run is synchronous database work executed
    from a thread, not a coroutine), guarded entirely by a plain
    `threading.Lock` — no process-local assumption beyond what the cache
    itself already makes (see module docstring).

    12B.5B remediation (Section 3, bounding): a prior version wrote
    `self._results[key] = ...` on every call but never removed it — an
    unbounded per-key memory leak, one entry surviving forever for every
    *unique* key this process ever computed (a normal, expected outcome
    of normal traffic: distinct organizations/marketplaces/periods/
    params each mint their own key). Followers now capture a direct
    reference to their call's `_PendingResult` object while still holding
    `self._lock` — the same lock already taken to read `self._events`
    — rather than looking it up in `self._results` again after waking
    from `event.wait()`. That reference keeps the object alive for the
    follower regardless of what happens to the dict, which is what makes
    it safe for the leader to pop `self._results[key]` in the exact same
    `finally` block that already pops `self._events[key]`, before
    `event.set()` ever wakes a follower — closing the leak with no
    change to any caller's contract."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}
        self._results: dict[str, _PendingResult] = {}

    def run(self, key: str, compute: Callable[[], T]) -> T:
        with self._lock:
            event = self._events.get(key)
            if event is None:
                event = threading.Event()
                self._events[key] = event
                result_holder = _PendingResult(ok=False)
                self._results[key] = result_holder
                is_leader = True
            else:
                is_leader = False
                result_holder = self._results[key]
        if not is_leader:
            event.wait()
            if not result_holder.ok:
                raise result_holder.error  # type: ignore[misc]
            return result_holder.value
        try:
            value = compute()
            result_holder.ok = True
            result_holder.value = value
            return value
        except BaseException as exc:  # noqa: BLE001 - re-raised immediately below
            result_holder.ok = False
            result_holder.error = exc
            raise
        finally:
            with self._lock:
                self._events.pop(key, None)
                self._results.pop(key, None)
            event.set()


class AsyncSingleFlight:
    """`SingleFlight`'s coroutine-native counterpart, used by Layer B
    (`app.copilot.synthesis.service.SynthesisService.synthesize`, an
    `async def`). `SingleFlight` itself cannot be reused there: its
    follower path calls `threading.Event.wait()`, a blocking call that
    would freeze the entire event loop — including every OTHER request
    this process is serving, on any key — for as long as the leader's
    `await` on the LLM call takes. That is strictly worse than the
    "no coalescing" state this replaces.

    Coordination state is a per-key `asyncio.Future`, guarded only long
    enough to check-or-create it — never held across the actual `await
    compute()`. This is the same "one shared lock guards only the
    bookkeeping dict, never the expensive work itself" shape `SingleFlight`
    already uses; it is not the "process-global single lock" the 12B.5B
    remediation prohibits, which would mean one lock serializing
    unrelated keys' computations against each other. Different keys
    never wait on each other here: only callers sharing the identical
    key ever await the same future.

    Safe specifically because `synthesize()` only ever runs as a
    coroutine on the process's single asyncio event loop (never as
    genuinely parallel OS threads calling into this class), so no
    `asyncio.Future` here is ever touched from more than one thread."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # Value is `(future, follower_count)`: the count exists only so a
        # failed leader can tell whether any follower will ever call
        # `future.exception()` — asyncio logs a noisy "exception was never
        # retrieved" warning at GC time for a failed future nobody
        # inspects, which would happen on every uncoalesced failure
        # (the overwhelmingly common case: most keys have exactly one
        # caller). With zero followers the leader marks its own future's
        # exception retrieved before discarding it, purely to keep logs
        # clean — it never changes what the leader itself raises.
        self._futures: dict[str, tuple[asyncio.Future, int]] = {}

    async def run(self, key: str, compute: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            entry = self._futures.get(key)
            if entry is None:
                future: asyncio.Future = asyncio.get_running_loop().create_future()
                self._futures[key] = (future, 0)
                is_leader = True
            else:
                future, follower_count = entry
                self._futures[key] = (future, follower_count + 1)
                is_leader = False
        if not is_leader:
            # `asyncio.shield` matters here: without it, cancelling the
            # follower's OWN task (e.g. a client disconnect) would call
            # `.cancel()` on this SHARED future — corrupting it for the
            # leader (whose later `set_result`/`set_exception` would then
            # raise `InvalidStateError`) and for every other follower
            # still waiting on it. `shield` still lets a cancelled
            # follower raise `CancelledError` to ITS OWN caller; it just
            # stops that cancellation from reaching the shared future.
            return await asyncio.shield(future)
        try:
            value = await compute()
        except BaseException as exc:  # noqa: BLE001 - propagated to followers, then re-raised
            async with self._lock:
                _, follower_count = self._futures.pop(key, (future, 0))
            if follower_count > 0:
                future.set_exception(exc)
            else:
                future.cancel()
            raise
        else:
            async with self._lock:
                self._futures.pop(key, None)
            future.set_result(value)
            return value


def _hash_key(prefix: str, payload: dict[str, Any]) -> str:
    """Deterministic, sanitized cache key. `payload` must already contain
    only scope identifiers (UUIDs), version strings, and normalized
    parameters — never raw seller text, evidence records, or an answer
    body. `sort_keys=True` + `default=str` make the digest stable
    regardless of dict insertion order or UUID/enum value types."""
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"{prefix}:{digest}"


def evidence_cache_key(
    *,
    organization_id: UUID,
    marketplace_participation_ids: list[UUID],
    skill_id: str,
    skill_version: str,
    params: dict[str, Any],
    listings_evidence_version: str | None,
    orders_evidence_version: str | None,
    sales_traffic_evidence_version: str | None = None,
) -> str:
    """Layer A key. `marketplace_participation_ids` is sorted so a
    multi-marketplace call's key never depends on argument order, and a
    single-marketplace call's key can never collide with a different
    marketplace's — every component here is either an opaque id, a
    version string, or an already-normalized parameter (e.g.
    `period_days`), never a raw date/time (see module docstring: TTL,
    not a literal timestamp, bounds "now"-sensitivity).

    `sales_traffic_evidence_version` defaults to `None` (12B.6A —
    mechanism-ready only, no launch skill reads Sales and Traffic data
    yet) so every existing Listings/Orders call site is unaffected; a
    future skill passes its own version string exactly like the other
    two domains, and this parameter's presence in the hashed payload
    means a `None` (mechanism unused) and a real version string can
    never collide with each other's cache key."""
    payload = {
        "organization_id": str(organization_id),
        "marketplace_participation_ids": sorted(str(item) for item in marketplace_participation_ids),
        "skill_id": skill_id,
        "skill_version": skill_version,
        "params": params,
        "listings_evidence_version": listings_evidence_version,
        "orders_evidence_version": orders_evidence_version,
        "sales_traffic_evidence_version": sales_traffic_evidence_version,
        "config_version": SKILL_CONFIG_VERSION,
    }
    return _hash_key("skill_evidence", payload)


def evidence_content_key(skill_evidence: dict[str, Any]) -> str:
    """A content-addressed stand-in for Layer A's key, built from an
    *already-computed* `SkillEvidence.model_dump()` rather than the
    original tool-call parameters — used by Layer B (the final-answer
    cache), which runs in `app/copilot/synthesis/service.py`, a
    completely separate HTTP call from the one that ran the skill tool
    and has no access to that call's original `params` dict. Keying on
    the evidence's own `metrics`/`records` content instead is at least
    as correct: two calls that produced byte-identical metrics/records
    for the identical skill/scope/versions are, by construction, exactly
    the calls whose synthesized answer should be identical too — the
    evidence content is the actual source of truth for what an answer
    says, not the request parameters that happened to produce it."""
    payload = {
        "skill_id": skill_evidence.get("skill_id"),
        "skill_version": skill_evidence.get("skill_version"),
        "organization_id": skill_evidence.get("organization_id"),
        "marketplace_participation_ids": sorted(skill_evidence.get("marketplace_participation_ids") or []),
        "metrics": skill_evidence.get("metrics"),
        "records": skill_evidence.get("records"),
        "confidence": skill_evidence.get("confidence"),
        "config_version": SKILL_CONFIG_VERSION,
    }
    return _hash_key("skill_evidence_content", payload)


def answer_cache_key(
    *,
    evidence_key: str,
    intent: str,
    prompt_version: str,
    model: str | None,
    provider: str | None,
    locale: str = "en",
) -> str:
    """Layer B key — always built *from* an already-composed evidence
    key (never re-derives scope/version fields independently), plus the
    normalized `intent` (the planner's own deterministic intent string —
    never the seller's raw free-text question, so two different
    phrasings of the identical validated question share one entry) and
    every synthesis-specific dimension that can change the answer's
    wording or shape."""
    payload = {
        "evidence_key": evidence_key,
        "intent": intent,
        "prompt_version": prompt_version,
        "response_schema_version": RESPONSE_SCHEMA_VERSION,
        "model": model,
        "provider": provider,
        "locale": locale,
    }
    return _hash_key("skill_answer", payload)


def cached_evidence_lookup(
    cache: SkillCache,
    single_flight: SingleFlight,
    *,
    key: str,
    compute: Callable[[], T],
    ttl_seconds: int = DEFAULT_EVIDENCE_CACHE_TTL_SECONDS,
    force_refresh: bool = False,
) -> T:
    """Read-through cache helper shared by both layers: check the cache,
    and on a miss, coalesce concurrent identical-key callers through
    `single_flight` before computing once and populating the cache. Any
    cache backend exception degrades to "always compute" — a broken
    cache must never break a skill.

    `force_refresh=True` ("Recompute from saved data" in the UI) skips
    only the cache *read* — the fresh value is still written back
    afterward, so the next caller benefits. It never bypasses anything
    else: `compute()` is exactly the same already-synchronized-data
    computation either way, never an Amazon call."""
    cached = None
    if not force_refresh:
        try:
            cached = cache.get(key)
        except Exception:  # noqa: BLE001 - cache must never break the skill
            cached = None
    if cached is not None:
        return cached

    def _compute_and_store() -> T:
        value = compute()
        try:
            cache.set(key, value, ttl_seconds)
        except Exception:  # noqa: BLE001 - cache must never break the skill
            pass
        return value

    return single_flight.run(key, _compute_and_store)
