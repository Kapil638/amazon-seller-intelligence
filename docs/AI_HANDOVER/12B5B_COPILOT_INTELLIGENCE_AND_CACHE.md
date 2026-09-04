# 12B.5B — Copilot Intelligence, Evidence Coverage, and Caching

> **12B.5B remediation notice (this pass supersedes specific claims
> below):** the user reviewed the original implementation pass recorded
> in this document and identified four defects before it was
> considered committable: (1) all five skills were bumped to
> `skill_version="2.0.0"` although §4/§5 below correctly describe the
> changes as presentational, not formula changes — a major version bump
> must never exist purely to invalidate a cache; (2) the cache had no
> maximum-entry/memory bound; (3) Layer B (final-answer cache) had no
> single-flight coalescing, so concurrent identical cold requests could
> each invoke a real attached LLM; (4) the "83% smaller" payload claim
> in §10 was measured for exactly one skill (Listing Health
> Prioritizer) and generalized to all five without evidence. A fifth,
> unrelated finding — an ad-hoc diagnostic script had accidentally
> resolved the live Supabase connection (disclosed in §1) — was also
> required to get a fail-closed guard. All five are corrected in this
> same working tree; the corrections are recorded inline, closest to
> the specific claim each corrects, rather than as a separate document,
> so a reader lands on the corrected claim directly instead of having
> to cross-reference two files. Original prose that remains accurate
> (the SP-API coverage audit in §2, the fields-added inventory in §3,
> the cache-key composition and invalidation mechanics in §8, the
> tenant-isolation proof in §11, the UI behavior in §12) is left as
> written.

> **Final safety/bounded-evidence review notice (this pass supersedes
> the remediation pass's own §18, and updates §4/§9/§10a):** a second
> review found the remediation's own database guard design unsafe (an
> import-time flag on `app.main` that broke the Listings/Orders
> workers and the Listings job admin CLI, and could be silently
> disabled by any script merely importing `app.main`) and replaced it
> with an explicit, launcher-set `ASI_DB_RUNTIME_CONTEXT` environment
> variable — see the rewritten §18. It also fixed the one remaining
> flagged-but-unfixed gap, Cancellation/Operational Anomaly Detector's
> unbounded `records` list (new §18a), and replaced §9's prompt-cache-
> eligibility discussion with a sourced, model-specific finding: this
> project's configured default model requires a 2,048-token threshold
> this project's actual system prompts (~757 and ~405 estimated tokens)
> do not clear. §4's version table and §13/§14 below are updated to
> match.

Durable record of the 12B.5B implementation pass. Branch:
`milestone-12b5b-copilot-intelligence-cache`, created from verified
`main` at `18316dd` (the genuine two-parent merge of PR #15 /
`milestone-12b5a-copilot-listings-orders-skills`, itself carrying the
`e83ea50` concurrency fix as an ancestor). Implement/test/review only —
no commit, push, PR, migration, live Amazon/LLM call, worker start, or
production mutation was authorized or performed while producing this
milestone.

## 1. Phase 0 — verified base

- `git log -1 --format="%H %P" origin/main` → `18316dd`, parents
  `3c3f605` (previous `main` tip) and `e83ea50` (12B.5A branch tip) —
  confirmed genuine two-parent merge.
- `git merge-base --is-ancestor e83ea50 origin/main` and the same for
  `fdc2728` (the feature commit) — both confirmed ancestors.
- Local `main` fast-forwarded `3c3f605..18316dd`; `git rev-parse main
  origin/main` confirmed equal.
- Working tree confirmed to contain only the seven pre-existing,
  unrelated Log Analyzer/ADR paths (`docs/adr/README.md`,
  `docs/adr/0007-...md`, `docs/adr/0008-...md`, `docs/operations/
  OPS1_*.md` × 4) — checksummed (SHA-256) and preserved byte-for-byte
  throughout; never staged.
- Alembic head confirmed as `0013_orders_durable_pagination` (the
  task's own text carried a typo — `lyph0013_...` — treated as noise
  and cross-checked against the actual repository/CI value, which
  matches every prior handover in this repository).
- Live Supabase revision independently confirmed identical
  (`SELECT version_num FROM alembic_version` via the app's own
  `get_engine()` — read-only, no write).
- Sanitized production evidence counts recorded (aggregate only, no
  identifier): 10 Listings rows, 153 Orders rows, 154 order-item rows,
  6 participating marketplaces, 2 marketplaces with ≥1 order, latest
  successful Listings run `succeeded` (2026-09-01), latest successful
  Orders run `succeeded` (2026-09-03), 6 Orders sync checkpoints, 1
  active Listings job and 1 active Orders job outstanding (pre-existing
  queued state, not created by this session — no worker was started to
  process them), no worker process running.

**Disclosed incident:** during later investigation, one ad-hoc
diagnostic Python invocation was run outside pytest's fixtures and
therefore resolved the *live* Supabase `DATABASE_URL` instead of the
test SQLite database. It failed on its very first write (a unique-
constraint collision against the real, pre-existing Production
connection row) before any commit — `session_scope()` rolled the
transaction back on that exception, exactly as designed. Verified
immediately afterward, read-only, against the same sanitized counts
above (participations/listings/orders unchanged) that nothing was
persisted. No further ad-hoc script in this session touched anything
but the pytest-fixture-redirected in-memory SQLite database.

## 2. Official SP-API coverage audit

Built from this repository's own pinned contract reports (12B.3A for
Listings Items, 12B.4A for Orders) plus direct inspection of the actual
parse/persist code — not a fresh external fetch, per the instruction to
use "the pinned contracts already adopted by the repository."

### Listings Items (`searchListingsItems`, pinned commit
`94219a3fd0b9ee9c319ce06bac293146440aa927`)

| `includedData` value | Requested | Parsed | Persisted | Used by a skill | Decision |
|---|---|---|---|---|---|
| `summaries` | Yes | Yes | Yes (`status`, `is_buyable`, `is_discoverable`, `item_name`, `asin`, `condition_type`) | Yes — all five skills, directly or via join | Keep. |
| `issues` | Yes | Yes | Yes, full documented shape (`code`, `message`, `severity`, `categories`, `attributeNames`, `enforcements`, `marketplaceIds`) | `code`/`severity`/`categories` — yes. `message` (Amazon-authored free text) — never read by any skill | `message` stays persisted (already-reviewed 12B.3D decision, unchanged) but confirmed still never surfaced to evidence/model/UI by any of the five skills (§6). `categories` newly wired into evidence this milestone (§5). |
| `offers` | Yes | Yes | Yes | No | No change — price/offer detail isn't decision-relevant to ranking/risk/trend/anomaly formulas. |
| `fulfillmentAvailability` | Yes | Yes | Yes | No | Unchanged. |
| `productTypes` | Yes | Yes | Yes | No (only `product_type` singular is used) | Unchanged. |
| `relationships` (variation graph) | **No** | N/A | N/A | N/A | **Excluded.** Already deferred at 12B.3A ("adds complexity not needed yet"). Re-evaluated for this milestone: none of the five skills' formulas need parent/child variation context to rank, investigate, or report exposure — a listing's own issues/status already fully determine its outcome. Revisit only if a future skill needs cross-variation reasoning. |
| `attributes` | **No** | N/A | N/A | N/A | **Excluded**, unchanged. `additionalProperties: true` (no Amazon schema) — directly the "no generic PII-capable JSON blob" rule this milestone's own Phase 3 restates. |
| `procurement` | **No** | N/A | N/A | N/A | **Excluded**, unchanged — vendor-only, not applicable to ASI's seller-central-only scope. |
| Sales rank / BSR | N/A | N/A | N/A | N/A | **Not available from this API at all.** Confirmed against the pinned contract's own `includedData` enum (`summaries, attributes, issues, offers, fulfillmentAvailability, procurement, relationships, productTypes` — no rank field anywhere). Would require Amazon's separate **Catalog Items API** — explicitly out of scope for this milestone ("those additional APIs are out of scope for implementation here"). |
| Deep category/classification | N/A | Partial (`productType` only) | Yes | No | Amazon's Listings Items API exposes only a flat `productType` string, not a category tree — nothing deeper to add without Catalog API. |

### Orders (`2026-01-01`, `APPROVED_INCLUDED_DATA = (PROCEEDS,
FULFILLMENT, CANCELLATION, PACKAGES)`)

| Field group | Requested | Parsed | Persisted | Used by a skill | Decision |
|---|---|---|---|---|---|
| `product` (SKU/ASIN/title/price) | Always present | Yes | Yes (`item_name` newly wired into evidence this milestone) | Yes — all order-touching skills | Unchanged persistence; `item_name` newly *surfaced* (§5). |
| `proceeds`/`fulfillment`/cancellation status | Yes | Yes | Yes | Yes | Unchanged. |
| `packages`/`packageItems` (`OrderPackage`, `PackageStatus`) | Yes (requested for the *status* fields only) | **Yes** — `app/amazon/orders_models.py`'s `OrderPackage`/`PackageStatus` DTOs fully validate the response | **No** — parsed at the client/DTO boundary and discarded; no column exists on `AmazonSellerOrder`/`AmazonSellerOrderItem` | No | **Genuine requested→parsed→not-persisted gap, confirmed by direct inspection.** Not wired into a persisted column or evidence this milestone: none of the five skills' formulas need shipment/tracking timing granularity to rank, trend, or detect anomalies. Documented here rather than silently left unexamined — a future skill needing "days to ship"/carrier-level evidence would revisit this, not re-derive it from scratch. `shipFromAddress` (the seller's own warehouse — not PII) remains explicitly excluded per the existing 12B.4A decision. |
| `expenses`, `promotions` | **No** — outside `APPROVED_INCLUDED_DATA` | N/A | N/A | N/A | **Excluded**, unchanged from 12B.4A's own "can be added later as its own reviewed increment." Adding either would require a live-Amazon-facing client change (a wider `includedData` request against the real API) — explicitly not authorized in an implement/test/review-only milestone, and margin/expense analysis is the separate profit engine's job per CLAUDE.md's provider table, not these five Listings/Orders skills. |
| `buyer`/`recipient`/`payment`/`tax` identifiers, gift message, cancellation free text, customized-product URL, serial numbers | **No** | N/A | N/A | N/A | Continue excluded — unchanged, reconfirmed. |

**Conclusion:** no new Amazon field needed to be *requested* this
milestone. The two genuine gaps found (`categories` already-persisted-
but-unused, and `packages` requested-parsed-but-never-persisted) were
resolved by (a) wiring the already-available `categories` field into
evidence with zero new persistence, and (b) explicitly documenting
`packages` as a reviewed, deliberate non-addition rather than an
oversight. **No schema migration was required or added.**

## 3. Fields added and intentionally excluded

**Added to evidence (zero migration — every field below was already a
persisted column, just not yet surfaced through the Copilot skill
layer):**

- `item_name` — added to `ListingCollectionItem` (Seller Listings' own
  paginated read DTO, additively) and `OrderItemWindowRow` (the Copilot-
  only order-item window DTO), sourced directly from the already-loaded
  ORM row in both cases (no extra query). Surfaced in every one of the
  five skills' per-SKU records.
- `issue_categories` — a new `contracts.issue_categories()` helper
  extracting Amazon's own `Issue.categories` (already persisted inside
  the `issues` JSON array) — wired into Non-buyable Listing
  Investigator's `issue_summary` record.
- `score_factors` (Listing Health Prioritizer) / `risk_factors`
  (Listing Risk by Order Exposure) — explicit, named breakdowns of
  exactly the signals each skill's own deterministic rank key already
  used, so an answer can *explain* a ranking without re-deriving or
  approximating it.
- `failure_category` (Non-buyable Listing Investigator) — a plain
  `not_buyable_only` / `not_discoverable_only` /
  `not_buyable_and_not_discoverable` / `buyable_and_discoverable`
  classification of Amazon's own two binary status flags, never a
  diagnosis of cause.

**Intentionally excluded** (see the full audit tables above for each):
`relationships`, `attributes`, `procurement`, sales rank/BSR, deep
category trees, `packages`/shipment timing, `expenses`, `promotions`,
and every PII-adjacent Orders field already excluded since 12B.4A.

## 4. Improvements to each of the five skills

> **Remediation correction:** the original pass bumped all five skills
> to `skill_version="2.0.0"` and the list below originally described
> every skill as receiving the same class of change (added fields).
> §5's own text already said this correctly — "presentational
> breakdowns of already-computed values, never new formulas, weights,
> or thresholds" — so a **major** version bump was the wrong signal:
> it implies a formula/intelligence change that did not happen, and a
> major bump must never exist purely to invalidate a cache. Each skill
> was re-audited individually. Three now have a genuine, tested formula
> change and a **minor** bump (`1.1.0`, a response-contract change, not
> a rewrite); one is confirmed unchanged and reverted to `1.0.0`. The
> `item_name`/`issue_categories`/`score_factors`/`risk_factors` field
> additions described below are real and still present, but on their
> own they do not justify any version change — they are additive,
> backward-compatible fields, not a shape change that could make an
> older cached value structurally wrong.

`SKILL_VERSIONS` (`app/copilot/skills/contracts.py`) is the single
source of truth every skill module reads its own version from:

| Skill | Version | Why |
|---|---|---|
| Listing Health Prioritizer | `1.1.0` | Material fix: `is_discoverable` was computed and returned on every record but never consulted by `_rank_key` — a buyable-but-not-discoverable listing (invisible in Amazon search/browse) ranked as if fully healthy whenever it carried zero Amazon-reported issues. Now an explicit ranking signal (position 6 of 8 in the tuple, after buyability, before active-state). `item_name`/`score_factors` (presentational) unchanged from the original pass. |
| Non-buyable Listing Investigator | `1.1.0` | Material fix: a new `active_offer_evidence` observed-fact record plus a new `possible_explanation` branch for "not buyable, no ERROR issue, no active offer" — previously that exact case fell through to a generic observed-fact with no offer signal considered at all, even though Amazon requires an active offer for a listing to be buyable. `item_name`/`issue_categories`/`failure_category` (presentational) unchanged. |
| Order and Sales Trend Analyst | `1.1.0` | Material fix: a `MIN_SAMPLE_SIZE_FOR_TREND = 10` gate (mirroring Cancellation/Operational Anomaly Detector's own existing minimum-observations rule) and a new `sample_size_sufficient_for_trend` metric — previously a percentage change computed from a handful of orders (e.g. 1 → 2 orders, "+100%") was reported with no reliability signal at all. The synthesis template now phrases a below-threshold change as "sample too small for a reliable trend" instead of a bare percentage. A `distinct_sku_count` metric was also added so the top/bottom-5-SKU selection can honestly be described as "top 5 of N," not just "top 5." `item_name` (presentational) unchanged. |
| Cancellation/Operational Anomaly Detector | `1.1.0` (see update below) | No formula, threshold, or evidence-shape change was made to this skill in the 12B.5B remediation pass — it already had its own `MIN_SAMPLE_SIZE`/anomaly-threshold design from 12B.5A. The version dict entry incorrectly read `2.0.0` in the original pass; corrected back to `1.0.0` at that point, its true, then-unchanged version. **A genuine evidence-shape change was made in the follow-up final safety/bounded-evidence review** (§18a below): `records` was bounded to a deterministic top-N, closing this skill's one unbounded-payload gap — re-bumped to `1.1.0` to reflect that real change. |
| Listing Risk by Order Exposure | `1.1.0` | Material fix: confidence is now capped at `medium` whenever at least half of all at-risk listings have no linked order in the window (`majority_unmatched`, `UNMATCHED_MAJORITY_THRESHOLD = 0.5`) — previously confidence ignored how much of the exposure picture was actually unmatched, so a handful of matched listings could make the whole ranking look "high confidence" even when most of it wasn't backed by any order evidence. Buyability/discoverability were deliberately **not** added to this skill's rank key — doing so would mix its strictly-observed-exposure framing with the forward-looking framing Listing Health Prioritizer already owns, which this skill's own no-causal/no-predictive-claim design explicitly avoids. `item_name`/`risk_factors` (presentational) unchanged. |

Before/after example for the one skill whose ranking *order* actually
changes (Listing Health Prioritizer): given one issue-free, buyable,
*not-discoverable* listing and one issue-free, buyable, *discoverable*
listing, `1.0.0`'s key ranked them arbitrarily (both tied on every
signal the key checked, falling through to the `seller_sku` tie-break
alone); `1.1.0` deterministically ranks the not-discoverable listing
first every time, regardless of SKU — the materially correct behavior,
since a listing invisible to new shoppers is not "healthy" merely
because Amazon reported zero issues against it. Regression test:
`test_listing_health_material_fix_not_discoverable_outranks_fully_
healthy` (`tests/test_copilot_skills_evidence.py`).

## 5. Formulas and confidence rules

The four *fields* named below (`score_factors`/`risk_factors`/
`failure_category`/`issue_categories`) remain exactly what this section
originally said: **presentational breakdowns of already-computed
values**, never new formulas, weights, or thresholds, and this is
precisely why none of them justified the version bump the original
pass gave them (§4's remediation correction). No calculation is moved
to, or ever delegated to, an LLM in this milestone.

That said, §4 above documents that this same remediation pass *did*
add four genuine formula/evidence-logic changes on top of 12B.5A's
original work — `is_discoverable` in Listing Health Prioritizer's rank
key, the new offer-based `possible_explanation` branch in Non-buyable
Listing Investigator, the minimum-sample-size gate in Order and Sales
Trend Analyst, and the majority-unmatched confidence cap in Listing
Risk by Order Exposure — so "unchanged from 12B.5A" is no longer true
for those four skills as of this remediation. It remains true only for
Cancellation/Operational Anomaly Detector, confirmed unchanged and
reverted to `1.0.0` in §4.

## 6. Historical-coverage conclusions

No new history mechanism was built this milestone: the existing
equivalent-period comparison (`build_periods`), minimum-sample-size rule
(`MIN_SAMPLE_SIZE = 10`), and unmatched-relationship reporting
(`unmatched_listings_count`, `unmatched_order_items_count`,
`orders_without_items_count`) — all from 12B.5A — were re-audited and
found sufficient for the five skills' current claims. No skill in this
milestone claims anything "unusual" without an explicit baseline,
sample-size floor, and deterministic threshold (unchanged,
re-confirmed). A Listing status/issue *change-history* table (distinct
from the current-state-only `amazon_seller_listings`) remains
unimplemented — flagged as a known limitation (§17), not built here,
since no skill in this five-skill scope requires point-in-time issue
history to answer its question.

## 7. Cache architecture and storage choice

**Storage:** `InProcessSkillCache` (`app/copilot/skills/cache.py`), a
new class matching the exact existing pattern of `app.bulk.cache.
KeyedTtlCache`/`app.providers.memory_cache.MemoryTtlValueCache` (both
already in this codebase) — an injectable-clock, `threading.Lock`-
guarded, TTL-expiring in-process dict. **Inspected before choosing**:
no Redis or other shared cache exists anywhere in this repository; per
this milestone's explicit instruction, none was introduced by
assumption.

**This cache is process-local and is not sufficient for a deployment
running more than one API replica** — a write in one replica is
invisible to another. Correctness is preserved regardless (an
invalidating event changes the *key* everywhere at once, derived from
database state, never from cache state — see §8), but hit rate degrades
per-replica. **Production topology requirement, explicitly documented,
not implemented here:** replace `InProcessSkillCache` with a shared
backend (Redis or equivalent) behind the same `SkillCache` protocol —
every call site (`app/copilot/tools/skills.py`,
`app/copilot/synthesis/service.py`) is already written against that
protocol, not the concrete class, so this is a drop-in swap.

**Bounding (remediation correction):** the original pass gave
`InProcessSkillCache` a TTL but no maximum-entry/memory bound — a
process that only ever sees new, never-repeated keys (a plausible
pattern across many organizations × marketplaces × periods × params
combinations) would have grown without limit for the life of the
process. `InProcessSkillCache` now takes `max_entries` (default
`DEFAULT_MAX_CACHE_ENTRIES = 5000`) and evicts the least-recently-used
entry (an `OrderedDict`, `move_to_end` on every `get`/`set`,
`popitem(last=False)` to evict) once over capacity — deterministic,
never random. A `purge_expired()` method was also added for optional
proactive housekeeping (not required for correctness: `get()` already
guarantees a stale value is never returned, and `max_entries` already
bounds worst-case memory regardless). Separately, the original
`SingleFlight` (Layer A's request coalescer) wrote
`self._results[key] = ...` on every call but never removed it — an
unbounded per-key memory leak, one entry surviving forever for every
*unique* key this process ever computed. Fixed: a follower now captures
a direct reference to its `_PendingResult` object while still holding
`self._lock` (the same lock already taken to read `self._events`),
which makes it safe for the leader to pop `self._results[key]` in the
same `finally` block that already pops `self._events[key]`, before
`event.set()` ever wakes a follower. Regression tests:
`test_cache_evicts_least_recently_used_entry_when_over_capacity`,
`test_cache_never_grows_past_max_entries_across_many_unique_keys`,
`test_single_flight_leaves_no_residual_state_for_a_completed_key`,
`test_single_flight_does_not_leak_across_many_unique_keys`
(`tests/test_copilot_skill_cache.py`).

**Two layers**, per the milestone's Phase 6:
- **Layer A (evidence cache):** wired into all five tool handlers in
  `app/copilot/tools/skills.py`. Includes `SingleFlight` request
  coalescing — safe here because the underlying handlers are already
  synchronous, blocking, DB-bound functions in this codebase's existing
  architecture (calling them from `ToolRegistry.execute()`'s `async
  def` already blocks the event loop for that duration; `SingleFlight`
  adds no new blocking characteristic, it only avoids *redundant*
  blocking work for concurrently-identical requests).
- **Layer B (final-answer cache):** wired into `SynthesisService.
  synthesize()`. **Remediation correction: now has single-flight
  coalescing.** The original pass deliberately omitted it, reasoning
  that mixing a blocking `threading.Event.wait()` with an in-flight
  async LLM call needed its own reviewed design — that design is now
  built: `AsyncSingleFlight` (`app/copilot/skills/cache.py`), a
  coroutine-native counterpart to `SingleFlight` whose follower path
  `await`s an `asyncio.Future` (via `asyncio.shield`, so cancelling one
  follower's own task can never cancel or corrupt the future the leader
  and every other follower still depend on) instead of blocking on
  `threading.Event.wait()`, which would have frozen the entire event
  loop — every other request this process is serving, on any key — for
  as long as the leader's LLM call took. Coordination state is a
  per-key `asyncio.Future` guarded only long enough to check-or-create
  it, never held across the actual `await compute()`; different keys
  never wait on each other. Wired into `SynthesisService.synthesize()`
  scoped to exactly the same population the answer cache already
  covers (only when a `skill_evidence` fact is present — every other
  pre-existing intent is untouched, matching this milestone's five-
  skills-only scope). Proven directly (`tests/
  test_copilot_skill_answer_cache.py`):
  `test_concurrent_identical_requests_invoke_the_model_at_most_once` (5
  concurrent identical requests → exactly 1 model call, all 5 receive
  the identical validated result), `test_different_keys_do_not_block_
  each_other` (a different evidence/organization/question never waits
  on another key's still-pending computation),
  `test_leader_failure_releases_followers_and_does_not_cache` (a
  raised exception during the shared computation still lets every
  follower complete instead of hanging, and nothing is cached from a
  failure), `test_cancelled_follower_does_not_corrupt_the_shared_
  future` (cancelling one follower's own task never disturbs the
  leader or any other follower), and the `_clear_answer_cache` fixture
  itself asserts `_ANSWER_SINGLE_FLIGHT._futures == {}` after every
  test in that file — proving per-key coordination state is actually
  removed once every caller has returned, not just logically supposed
  to be.
- **Layer C (provider prompt-prefix):** see §9.

### 7a. Production cache readiness classification (remediation addition)

**Deployment topology, as verifiable from this repository:** no
infra-as-code exists here (no Dockerfile, docker-compose file,
Procfile, or Kubernetes manifest in this repository) to confirm
replica count from source alone. The only deployment evidence
available is `docs/checkpoints/2026-08-25-production-connect-amazon.md`
and CLAUDE.md's own description of that checkpoint, both of which
describe a **single local uvicorn process** reaching production Amazon
and the live Supabase Postgres — not a multi-instance cloud deployment.
This document does not assert a replica count beyond what is
verifiable; if the actual production deployment already runs more than
one API process, that fact lives outside this repository and should be
confirmed with whoever operates it before trusting the classification
below.

**Classification:** `InProcessSkillCache`/`SingleFlight`/
`AsyncSingleFlight` are **production-capable for a verified single API
process only**. They are **not** a production-wide cache for a
multi-replica deployment: a write (or an in-flight single-flight
computation) in one replica is invisible to another, so replicas would
each warm their own copy independently rather than sharing hit rate —
correctness would still hold (invalidation is keyed off database state,
never cache state, per §8), but this milestone's own token-saving and
hit-rate claims apply per-process, not deployment-wide, and must never
be read as an aggregate production hit rate until a shared backend is
deployed and measured.

**If a shared backend is ever needed** (i.e., if this deployment is
confirmed to run, or is about to run, more than one API replica):
- **Recommended backend:** Redis, kept behind the exact same `SkillCache`
  protocol this module already defines — every call site
  (`app/copilot/tools/skills.py`, `app/copilot/synthesis/service.py`)
  is written against the protocol, not the concrete
  `InProcessSkillCache` class, so this would be a drop-in swap, not a
  call-site rewrite. **Not introduced in this milestone or this
  remediation** — no Redis dependency, client, or configuration exists
  anywhere in this repository, matching the explicit instruction not
  to introduce a new production dependency by assumption.
- **Serialization:** `SkillEvidence`/`SynthesizedResponse` are both
  Pydantic models with an existing `model_dump(mode="json")` — the
  same JSON-serializable shape already used for `evidence_content_key`
  hashing and for the payload-size measurements in §10a would be the
  natural wire format; no new serialization format is designed here.
- **TTL:** unchanged from what already exists — 120 seconds for both
  layers (`DEFAULT_EVIDENCE_CACHE_TTL_SECONDS`, `_ANSWER_CACHE_TTL_
  SECONDS`); a shared backend should keep the exact same TTL semantics
  the in-process implementation already uses, not a new value invented
  for this document.
- **Encryption/privacy:** no seller-identifying raw text, evidence
  record content beyond what §11's tenant-isolation proof already
  covers, or Amazon credential/token material is ever placed in a
  cache key (keys are opaque SHA-256 digests, §8) or in a cached value
  beyond `SkillEvidence`/`SynthesizedResponse` themselves (already the
  same content a caller would otherwise recompute and see directly).
  A shared backend would need transport encryption (TLS to Redis) and
  at-rest encryption consistent with wherever it is hosted — no new
  seller-secret material is introduced by caching that isn't already
  present in the uncached evidence/answer path.
- **Failure behavior:** unchanged from what already exists and is
  already tested — every cache backend exception (`get`/`set`)
  degrades to "always compute" (§8); a shared backend must preserve
  this exact fail-open-to-compute behavior, never fail a request
  because the cache itself is unreachable.
- **Deployment configuration / health / observability:** would need,
  at minimum, a connection URL/credential delivered the same way this
  project already delivers `DATABASE_URL` (never `.env` committed to
  the repository), a health check the API's own `/health` endpoint
  could report on (mirroring how it already reports `persistence`
  status), and cache-hit/miss metrics per layer — none of this exists
  today because no shared backend exists today.
- **Milestone estimate:** introducing a real shared backend is a
  **separately authorized deployment increment**, not part of 12B.5B
  or this remediation. This document's job is to leave the
  `SkillCache` protocol ready for that swap, classify the current
  implementation honestly, and stop there — not to build or deploy
  Redis by assumption.

**Do not claim production-wide cache hit rates or invalidation
guarantees until a shared backend is actually deployed and measured.**
Every hit-rate/invalidation proof in this document (§8, §10) is a
single-process, unit-test-level proof — true and load-bearing for that
scope, and not yet evidence of anything at production deployment
scale.

## 8. Exact keys and invalidation behavior

**Layer A key** (`evidence_cache_key`): SHA-256 over a canonical JSON
payload of `{organization_id, sorted(marketplace_participation_ids),
skill_id, skill_version, params, listings_evidence_version,
orders_evidence_version, config_version}`. `params` is the tool's own
normalized arguments (`period_days`, `limit`, `seller_sku`/`asin`
where applicable) — **never a literal resolved date range**: the
resolved analysis window is a function of `period_days` + "now," and
"now" is exactly what TTL (a documented secondary safeguard) governs,
not the cache key, so a key never goes stale purely from wall-clock
drift within its TTL window.

`listings_evidence_version`/`orders_evidence_version`
(`app/copilot/skills/contracts.py`) are each the ISO-8601
`last_successful_synchronized_at` of that dataset — **deliberately
built from success alone, never `status`**: a still-`queued`/`started`/
`waiting_to_retry` sibling row (including one a *concurrent* trigger
just created) can never be mistaken for a completed, evidence-changing
sync. `config_version` (`SKILL_CONFIG_VERSION`, currently `"1"`) is a
single coarse constant bumped whenever any skill's formula constants
change (minimum sample sizes, anomaly thresholds, ranking tie-break
rules) — mirroring how `skill_version` already governs the evidence
*contract shape*.

**Invalidation is entirely a consequence of key composition, not an
explicit cache-clearing call anywhere in this codebase:**
- A successful Listings ingestion changes `listings_evidence_version`
  → every Listings-touching skill's key for that participation changes.
- A successful Orders ingestion changes `orders_evidence_version` →
  every Orders-touching skill's key changes.
- Bumping `SKILL_VERSIONS[...]` or `SKILL_CONFIG_VERSION` changes every
  affected skill's key immediately on deploy.
- TTL (120s default for both layers) is a secondary safeguard only —
  bounds the residual staleness a version-based check doesn't cover
  (e.g., a currently-in-progress sync that hasn't succeeded yet).
- `force_refresh` ("Recompute from saved data," §11) skips only the
  cache *read* for that one call; the fresh result still repopulates
  the cache for the next caller — it never bypasses version
  correctness, and never touches Amazon.

**Layer B key** (`answer_cache_key`) is built from an `evidence_key`
(here, `evidence_content_key()` — see below) plus `{intent,
prompt_version, response_schema_version, model, provider, locale}`.
`intent` is the planner's own deterministic intent string, never the
seller's raw free-text question — two different phrasings of the
identical validated question share one entry, exactly matching "Similar
free-form wording must first pass through the approved deterministic
intent/skill validator" (this milestone introduced no new free-form-
text matching of any kind).

`evidence_content_key()` is a deliberate design choice: Layer B runs in
a *separate* HTTP call (`POST /synthesize`) from the one that computed
the evidence (`POST /conversations/{id}/execute`), with no access to
that call's original tool parameters. Rather than try to reconstruct
them, it hashes the **already-computed** evidence's own `{skill_id,
skill_version, organization_id, marketplace_participation_ids, metrics,
records, confidence, config_version}` — content-addressed, and at least
as correct: two calls whose evidence content is byte-identical are, by
construction, exactly the calls whose synthesized answer should be
identical too.

**Every cache backend exception (get or set) degrades to "always
compute"** — a broken cache can never break a skill, and can never fake
a hit. Verified directly (`test_cache_backend_failure_falls_back_to_
compute`). **A failed `compute()` is never cached** (verified:
`test_failed_compute_is_never_cached`) — this is how "never cache
authorization failures/failed calls" is actually enforced: the cache
`.set()` call is structurally unreachable on an exception path.

### 8a. Cache-key completeness audit (remediation addition)

Exact canonical inputs, checked against every dimension the
remediation asked for:

| Dimension | Layer A (`evidence_cache_key`) | Layer B (`answer_cache_key` via `evidence_content_key`) |
|---|---|---|
| Organization | `organization_id` | via `evidence_content_key`'s `organization_id` |
| Marketplace / multi-marketplace scope | `sorted(marketplace_participation_ids)` | via `evidence_content_key`'s `sorted(marketplace_participation_ids)` |
| Period / date boundaries | `params["period_days"]` (normalized; the resolved literal date range is deliberately never a key component — see §8's "never a literal resolved date range" note; TTL bounds "now"-drift instead) | not re-derived — Layer B keys off Layer A's already-computed evidence content, which already reflects whichever period Layer A used |
| Skill + formula version | `skill_id`, `skill_version` (from `SKILL_VERSIONS`, §4), `config_version` (`SKILL_CONFIG_VERSION`) | `skill_id`, `skill_version` (via evidence content); `config_version` folded into evidence content the same way |
| Normalized parameters | `params` dict (`period_days`, `limit`, `seller_sku`/`asin` where applicable) | not applicable directly — see period/date row above |
| Normalized validated intent | not applicable (Layer A has no intent) | `intent` — the planner's own deterministic intent string, never the seller's raw free-text question |
| Listings evidence version | `listings_evidence_version` | not a direct key input — captured indirectly: a version change forces Layer A to recompute, which changes the evidence content Layer B keys off |
| Orders evidence version | `orders_evidence_version` | same indirect mechanism as Listings, via evidence content |
| Sync status / freshness state | not directly keyed (see §8's "never `status`" design — a still-in-progress sibling row must not invalidate Layer A) | **the evidence's own `confidence` field** — every skill's `confidence` degrades whenever `incomplete_run()`/a skill-specific rule (e.g. `majority_unmatched`, §4) fires, and `confidence` is one of the seven fields `evidence_content_key` hashes; proven directly by `test_evidence_content_key_changes_when_confidence_degrades` (new this remediation, `tests/test_copilot_skill_cache.py`) |
| Prompt version | not applicable (Layer A never touches a prompt) | `prompt_version` (`PROMPT_VERSION`) |
| Response-schema version | not applicable | `response_schema_version` (`RESPONSE_SCHEMA_VERSION`) |
| Provider / model | not applicable | `provider`, `model` |
| Language / locale | not applicable | `locale` (defaults `"en"`) |

**Proven directly, by test (new or pre-existing, named where new):**
- *Identical evidence → identical canonical keys:*
  `test_evidence_content_key_is_identical_for_identical_evidence`
  (new).
- *Dictionary ordering cannot change a key:* `_hash_key`
  (`app/copilot/skills/cache.py`) always calls
  `json.dumps(payload, sort_keys=True, default=str)` — the ordering
  guarantee lives in one shared helper both `evidence_cache_key` and
  `evidence_content_key`/`answer_cache_key` funnel through, not
  reimplemented per key type.
- *Periods and marketplaces cannot collide:*
  `test_evidence_cache_key_differs_by_period_days`,
  `test_evidence_cache_key_differs_by_marketplace_participation`,
  `test_evidence_cache_key_is_stable_regardless_of_marketplace_id_order`
  (pre-existing) plus
  `test_evidence_content_key_is_stable_regardless_of_marketplace_
  participation_order` (new).
- *A newly successful Listings or Orders run invalidates the
  appropriate entry:*
  `test_evidence_cache_key_changes_when_listings_evidence_version_
  changes`, `test_evidence_cache_key_changes_when_orders_evidence_
  version_changes`, and the integration-level `test_new_successful_
  listings_run_invalidates_the_evidence_cache`/`test_new_successful_
  orders_run_invalidates_the_evidence_cache` (pre-existing).
- *Failed/partial/stale status transitions cannot reuse an answer that
  claimed fresh/complete evidence:*
  `test_evidence_content_key_changes_when_confidence_degrades` (new,
  detailed above) — the load-bearing proof that this remediation asked
  for by name, previously untested.
- *Recompute bypasses both answer and evidence cache reads, then
  safely refreshes eligible entries:* `cached_evidence_lookup`'s
  `force_refresh` parameter skips only the `cache.get()` call, never
  the `cache.set()` call afterward (§8's `force_refresh` bullet,
  pre-existing); Layer B has no `force_refresh` parameter of its own —
  a recompute request re-plans/re-executes the tool first (which
  bypasses Layer A per the above), producing new evidence content that
  naturally produces a new `evidence_content_key`, which naturally
  misses Layer B's cache without any separate bypass flag being needed
  there.
- *No identifier-bearing raw key is logged:* keys are opaque SHA-256
  digests everywhere (`test_evidence_key_never_contains_raw_scope_
  strings_only_a_digest`, pre-existing) and this module never calls
  a logger with a raw key or a raw scope value at all — confirmed by
  direct inspection of `app/copilot/skills/cache.py`, which contains no
  logging statement.

## 9. Prompt-prefix implementation and provider limitations

Both `app/copilot/planner/prompts.py` and `app/copilot/synthesis/
prompts.py` already separated a **stable, module-level constant
`SYSTEM_PROMPT` string** from a **dynamic, per-call `build_user_prompt`
function** before this milestone — this is not new architecture, it was
already correctly designed. What this milestone added: `synthesis/
prompts.py`'s `SYSTEM_PROMPT` gained a stable "LAUNCH SKILLS" block
(the five skills' names/purpose/terminology rules and the six-section
response contract, all fixed text, compiled once as part of the Python
module — byte-identical across every call by construction, containing
no timestamp, identifier, marketplace name, or evidence). `PROMPT_
VERSION` (`app/copilot/synthesis/schemas.py`) bumped `copilot_synthesize`
→ `copilot_synthesize_v2` alongside it, so the final-answer cache can
never conflate a pre- and post-bump prompt.

**Remediation correction:** the original pass stated OpenAI's prompt
caching is "**confirmed**" to be automatic. That overstates what was
actually verified — no live call was made, so no cache hit was ever
observed. The corrected, honest framing: this codebase is **structured
for** provider prompt caching, not **observed working**. What is
actually proven, locally, without a live call:

- **Stable-prefix bytes are identical across eligible requests:**
  `SYSTEM_PROMPT` (`app/copilot/synthesis/prompts.py`) is a static
  module-level string constant, compiled once as part of the Python
  module — by construction, every call sends the exact same bytes for
  it, containing no timestamp, identifier, marketplace name, or
  evidence.
- **All dynamic fields occur after the stable prefix:** the system
  message (fixed `SYSTEM_PROMPT`) and the user message (`build_user_
  prompt()`'s per-call `INTENT`/`USER MESSAGE`/`COMPACT CONTEXT`/
  `ALLOWED FACTS` blocks) are sent as two separate messages, system
  first, in that order, on every call — verified by direct inspection
  of `app/ai/openai_provider.py`'s message-array construction.
- **Stable serialization order (remediation fix):** `build_user_
  prompt()`'s `json.dumps(...)` calls for `compact_context` and
  `facts` did not pass `sort_keys=True` — meaning two calls carrying
  logically identical content could, in principle, serialize to
  different bytes if a future refactor iterated a dict/set in a
  different order upstream. Both calls now pass `sort_keys=True`,
  closing that gap without changing any prompt content.
- **Prefix length vs. the provider's caching eligibility threshold
  (final safety/bounded-evidence review, verified against current
  official OpenAI documentation):** OpenAI's automatic prompt caching
  requires a minimum number of *visible input tokens* before it applies
  at all, and that minimum is **model-dependent**: 1,024 tokens for
  GPT-5.6 and later, 2,048 tokens for earlier models. This project's
  configured default model (`app/core/config.py`'s `openai_model:
  str = "gpt-5.4"`) is an earlier model, so **the applicable threshold
  for this deployment, as configured today, is 2,048 tokens** — not
  the more commonly-cited 1,024-token figure, which applies only if
  `OPENAI_MODEL` is ever changed to GPT-5.6 or later.
  `synthesis/prompts.py`'s `SYSTEM_PROMPT` measures exactly 3,030
  characters (~757 tokens by a rough chars÷4 estimate — **not**
  measured with a real tokenizer; see §10a's tokenizer-availability
  note); `planner/prompts.py`'s own `SYSTEM_PROMPT` measures 1,620
  characters (~405 tokens, same caveat). **Both are very likely below
  both the 1,024- and 2,048-token thresholds on their own.** Because
  only content that is genuinely byte-identical across two *different*
  calls can ever be reused from the provider's cache, and the dynamic
  per-call content (user message, evidence, intent) never repeats
  verbatim between calls, the system message is the only content this
  design can rely on for a repeat cache hit — and at its current
  length, it does not clear the threshold this project's own default
  model requires. **Conclusion: this design is structurally prepared
  for provider prompt caching, but is very likely too short to actually
  activate it at the current prompt length and configured model.**
  This is a real, previously unstated finding, not a defect to silently
  fix — per this review's own explicit instruction, the prompt is not
  padded with useless text merely to cross a threshold. If token
  savings from *this specific* mechanism become a priority, the only
  legitimate paths are (a) growing the stable system content with
  genuinely useful, always-identical instructional content that would
  be added on its own merits anyway, or (b) accepting that provider
  prompt-prefix caching will not engage at this prompt's honest length
  and continuing to rely on this project's own Layer A/B application-
  level caching (§7, §8a) — which does not depend on any token-length
  threshold at all, and is the mechanism already proven, by test, to
  eliminate redundant computation and model calls.
- **Provider/model combinations supporting cached-input telemetry:**
  `app/ai/factory.py` wires exactly one provider (`OpenAIProvider`,
  `settings.ai_provider == "openai"` is the only branch that does not
  raise); `_cached_input_tokens()` (`app/ai/openai_provider.py`) reads
  the OpenAI **Responses API**'s `usage.input_tokens_details.
  cached_tokens` shape specifically. No other provider or API shape is
  wired in this codebase, so this claim does not generalize beyond
  OpenAI's Responses API today.
- **Graceful zero/absent telemetry handling, verified by direct
  inspection:** `_cached_input_tokens()` returns `None` (never `0`,
  never raises) when `input_tokens_details` is absent or malformed —
  `None` correctly means "not reported," which is a different fact
  from "confirmed zero cache hit" that a `0` would have falsely
  implied.
- **No direct KV-cache control is claimed anywhere** — this milestone
  only ensures the `system` message is a stable, unchanging prefix,
  which is the one lever OpenAI's automatic caching actually responds
  to; there is no explicit `cache_control`/opt-in parameter on this
  API (unlike, e.g., Anthropic's explicit `cache_control: {type:
  "ephemeral"}` blocks, which this codebase does not use since it does
  not integrate an Anthropic provider).

**Limitation, stated truthfully:** no live LLM call is authorized in
this implement/test/review-only milestone, so `cached_input_tokens`
could not be observed with a real request, and no provider-side cache
hit was ever observed. The stable-prefix design, the sorted
serialization, and the pre-existing usage-reporting plumbing are all
verified in place; the actual provider-side cache hit rate remains
unmeasured pending a live call under separate authorization — and,
per the threshold analysis above, is not expected to show a hit at
this prompt's current length and this project's currently-configured
default model even once a live call is authorized, unless the stable
system content grows past 2,048 tokens on its own genuine merits
first.

## 10. Before/after measurements

| Metric | Result |
|---|---|
| Evidence-cache hit vs. miss (repository calls) | Proven via spy: an identical second call makes **zero** additional calls to the evidence service (`test_repeated_identical_call_hits_the_evidence_cache`). |
| Listings-only invalidation | A new successful Listings run forces exactly one recompute reflecting the new data, never a stale cached ranking (`test_new_successful_listings_run_invalidates_the_evidence_cache`, 15/15 stable across repeated runs). |
| Orders-only invalidation | Same proof for Orders (`test_new_successful_orders_run_invalidates_the_evidence_cache`). |
| `force_refresh` | Bypasses the cache read exactly once, then repopulates it for the next plain caller (`test_force_refresh_bypasses_the_cache_read_but_still_repopulates_it`). |
| Answer-cache hit vs. miss | An identical `(evidence, intent)` pair makes **zero** additional calls to the (fake) generator on a second `synthesize()` call (`test_identical_skill_evidence_and_intent_hits_the_answer_cache_and_skips_the_model`). Different evidence, organization, or intent each independently force a fresh call — never a cross-tenant or cross-intent hit. |
| Evidence payload size (dynamic evidence vs. raw rows) | **Remediation correction:** the original pass measured exactly one skill (Listing Health Prioritizer) and reported its number, unqualified, as if representative of all five. It is not — see the corrected, per-skill table directly below §10. |
| LLM input/output tokens, provider cached-input tokens, LLM calls avoided in production | **Not measured — no live LLM call is authorized in this task.** The token-usage and cache-hit plumbing (`AITokenUsage.cached_input_tokens`, Layer B's cache) are both in place and unit-tested against a fake generator; real numbers require a live call under separate authorization. |
| Wall-clock latency (cold vs. warm) | **Not asserted as a hard threshold** — a shared CI runner's timing noise makes a millisecond-level assertion inherently flaky. The *structural* proof (zero additional repository/generator calls on a hit) is the load-bearing, non-flaky evidence for the latency claim instead. |

### 10a. Per-skill payload measurements (remediation addition)

All five skills measured on the same synthetic fixture scale
(`LISTING_COUNT = 100` synthetic listings/orders — the "large candidate
set" scenario), bytes/characters only (no tokenizer is installed in
this environment — `import tiktoken` fails; see the limitation note
below). "Raw" is the full ORM/DTO row set the evidence was computed
from, serialized with `json.dumps(..., default=str)`; "compact" is
`SkillEvidence.model_dump(mode="json")` for the same scope.

| Skill | Raw bytes | Compact bytes | Reduction | Regression test |
|---|---|---|---|---|
| Listing Health Prioritizer | 83,148 | 14,935 | 82.0% | `test_listing_health_evidence_payload_is_materially_smaller_than_the_raw_listing_rows` |
| Listing Risk by Order Exposure | 34,374 | 12,469 | 63.7% | `test_listing_risk_evidence_payload_is_materially_smaller_than_the_raw_at_risk_rows` |
| Non-buyable Listing Investigator (candidate-list mode) | 90,600 | 3,611 | 96.0% | `test_non_buyable_candidate_list_payload_is_materially_smaller_than_the_raw_listing_rows` |
| Order and Sales Trend Analyst | 180,000 | ~3,460–3,490 | ~98.1% | `test_order_trends_evidence_payload_is_materially_smaller_than_the_raw_order_item_rows` |
| Cancellation/Operational Anomaly Detector | 180,025 | 3,622 | 98.0% | `test_cancellation_evidence_payload_is_materially_smaller_than_the_raw_order_item_rows` |

(All five test files live in `tests/test_copilot_skill_evidence_payload_
size.py`.) **What the original `83,148 → 14,310` figure represented,
exactly:** the raw side is 100 synthetic `NormalizedListing` objects'
full fields (`issues`/`offers`/`fulfillment_availability`/
`product_types` arrays, each with realistic nested content) serialized
as one JSON array; the compact side is Listing Health Prioritizer's
`SkillEvidence.model_dump()` for that same 100-listing scope with
`limit=25` (only the top 25 of 100 candidates surfaced, by the
skill's own deterministic rank key, plus scope-wide aggregates like
`metrics.total_listings`/`with_issues_count` covering the full 100).
The `14,310` figure itself has since moved to `14,935` purely because
this same remediation added `is_discoverable` to `score_factors` (§4)
— the byte count was never frozen, it reflects whatever the current
evidence shape actually is.

**The wide range (63.7%–98.1%) is itself the honest finding:**
reduction magnitude depends on how much of a skill's raw input is
genuinely discardable. Order Trends and Cancellations aggregate almost
the entire raw order/item row set down to counts/sums/a handful of
top-N SKUs, so their reduction is largest. Listing Risk keeps a larger
fraction of its raw candidate set as full per-listing records (~25 of
~33 at-risk candidates at this scale) because its answer genuinely
needs that many individual listings named, so its reduction is
smallest. A single quoted "83%" was never representative of all five
skills — establishing that honestly, instead of assuming it, was
exactly this remediation's Section 7 ask.

**Truncation/top-N safety, confirmed for every skill that truncates:**
- Listing Health Prioritizer: `metrics.total_listings` (matching
  count) vs. `metrics.ranked_count` (returned count); a `limitations`
  entry names an exclusion when one occurs; selection is the
  documented, tested, deterministic `_rank_key`.
- Listing Risk by Order Exposure: `metrics.at_risk_listing_count` vs.
  `metrics.ranked_count`; deterministic `_risk_rank_key`.
- Non-buyable Listing Investigator (candidate-list mode):
  `metrics.not_buyable_count`; a `limitations` entry states exactly how
  many additional not-buyable listings are not shown; deterministic
  `_candidate_rank_key`.
- Order and Sales Trend Analyst: `metrics.distinct_sku_count` (added
  this remediation, §4) vs. the top/bottom-`TOP_BOTTOM_SKU_LIMIT=5`
  records — previously there was no way to state "top 5 of how many."
- Cancellation/Operational Anomaly Detector: **not top-N truncated at
  all** — `records` lists every distinct SKU present on a cancelled
  order in the window, sorted and deduplicated, with no limit. This
  means truncation-safety does not apply (nothing is removed), but it
  is also an unbounded-size limitation flagged honestly rather than
  fixed in this pass: a seller with an extreme number of distinct
  cancelled-order SKUs in one window could produce an evidence payload
  far larger than any of the measurements above. Out of scope for this
  remediation (§1 confirmed no formula/shape change was warranted for
  this skill); flagged in §17 as a genuine, separate follow-up if it is
  ever observed in practice.

**Tokenizer availability, checked directly in this environment:**
`import tiktoken` raises `ModuleNotFoundError` — no tokenizer is
installed. Every number above is bytes only, never an estimated token
count. `SYSTEM_PROMPT`'s "~757 tokens" figure in §9 is explicitly
labeled a rough chars÷4 estimate, not a real tokenizer measurement, for
the same reason.

## 11. Tenant-isolation and privacy proof

- Every Layer A/B key includes `organization_id` — proven never to
  collide across organizations (`test_evidence_cache_key_differs_by_
  organization`, `test_different_organization_never_shares_a_cached_
  answer`) or across marketplace participations
  (`test_evidence_cache_key_differs_by_marketplace_participation`).
- No cache key ever contains a raw organization/participation UUID —
  keys are opaque SHA-256 digests
  (`test_evidence_key_never_contains_raw_scope_strings_only_a_digest`).
- The full pre-existing 12B.5A eval suite (foreign-organization
  rejection, cross-marketplace isolation, mixed-currency separation,
  prompt-injection-shaped issue text) was re-run unchanged against the
  now-cached code path and still passes — caching introduced no new
  isolation surface.
- `Issue.message` (Amazon-authored free text) remains excluded from
  every one of the five skills' evidence, confirmed unchanged this
  milestone (§2).

## 12. UI behavior

- The five launch cards and the "Product research" secondary section
  are unchanged in structure and copy.
- Caching is entirely transparent at the HTTP-contract level — the
  `/plan → /execute → /synthesize` shape did not change, so a cached
  answer already renders through the exact same six-section
  `SkillAnswerCard` as a freshly-computed one; there was nothing to
  change here beyond what §12 below adds.
- **New:** a "Recompute from saved data" link inside each launch-skill
  answer's Data freshness section. Clicking it resubmits the exact
  originating question with `force_refresh: true` — bypassing only the
  cache read, never triggering a synchronization or an Amazon call
  (proven directly: the wiring test never mocks or exercises any sync/
  worker code path at all). Disabled while a request is already in
  flight, matching every other duplicate-submission guard already in
  place.
- No cache key, internal skill/tool name, model ID, or provider detail
  is ever rendered — confirmed by the existing "no internal identifier"
  assertion in `seller-copilot.test.tsx`, re-run unchanged.
- No new primary navigation tab was added.

## 13. Exact files changed

**New (original 12B.5B pass):**
- `apps/api/app/copilot/skills/cache.py`
- `apps/api/tests/test_copilot_skill_cache.py`
- `apps/api/tests/test_copilot_skill_evidence_cache_integration.py`
- `apps/api/tests/test_copilot_skill_answer_cache.py`
- `apps/api/tests/test_copilot_skill_evidence_payload_size.py`
- `docs/AI_HANDOVER/12B5B_COPILOT_INTELLIGENCE_AND_CACHE.md` (this file)

**New (this remediation pass):**
- `apps/api/tests/test_persistence_production_guard.py` (Section 8
  regression tests for the new fail-closed database guard)

**Modified (original 12B.5B pass):**
- `apps/api/app/amazon/listings_read.py` (`ListingCollectionItem.
  item_name`)
- `apps/api/app/amazon/orders_read.py` (`OrderItemWindowRow.item_name`)
- `apps/api/app/copilot/schemas.py` (`force_refresh` on the five skill
  input schemas)
- `apps/api/app/copilot/skills/contracts.py` (`SKILL_VERSIONS` +
  wired as source of truth, `listings_evidence_version`,
  `orders_evidence_version`, `issue_categories`)
- `apps/api/app/copilot/skills/{listing_health,non_buyable,
  listing_risk,order_trends,cancellations}.py` (evidence enrichment,
  `SKILL_VERSIONS` reference)
- `apps/api/app/copilot/tools/skills.py` (Layer A wiring,
  `force_refresh` plumbing)
- `apps/api/app/copilot/synthesis/{prompts,schemas,service}.py` (Layer
  B wiring, stable prompt-prefix block, `PROMPT_VERSION` bump)
- `apps/api/app/copilot/planner/{schemas,service,validator}.py`
  (`force_refresh` ephemeral field, mirroring `period_days`)
- `apps/api/app/api/routes/copilot.py` (`force_refresh` passthrough)
- `apps/api/tests/test_copilot_skills_evidence.py`,
  `test_copilot_skill_planner_routing.py`, `test_copilot_synthesis.py`
  (new/updated assertions for the above)
- `apps/web/src/components/copilot-message-list.tsx` ("Recompute from
  saved data" link, `originatingQuestion`)
- `apps/web/src/components/seller-copilot.tsx` (`forceRefresh` wiring)
- `apps/web/src/components/seller-copilot.test.tsx` (new test)
- `apps/web/src/lib/api.ts` (`forceRefresh` param)

**Modified (this remediation pass):**
- `apps/api/app/copilot/skills/contracts.py` — `SKILL_VERSIONS`
  corrected (§4).
- `apps/api/app/copilot/skills/listing_health.py` — `is_discoverable`
  material fix (§4).
- `apps/api/app/copilot/skills/non_buyable.py` — offer-based evidence
  material fix (§4).
- `apps/api/app/copilot/skills/order_trends.py` — minimum-sample-size
  gate, `distinct_sku_count` material fix (§4, §10a).
- `apps/api/app/copilot/skills/listing_risk.py` — majority-unmatched
  confidence cap material fix (§4).
- `apps/api/app/copilot/synthesis/validator.py` — order-trend synthesis
  template phrasing for a below-threshold sample size (§4).
- `apps/api/app/copilot/skills/cache.py` — `InProcessSkillCache`
  bounding/LRU eviction, `SingleFlight` leak fix, new
  `AsyncSingleFlight` (§7, §7a).
- `apps/api/app/copilot/synthesis/service.py` — Layer B wired through
  `AsyncSingleFlight` (§7).
- `apps/api/app/copilot/synthesis/prompts.py` — `sort_keys=True` on
  `build_user_prompt`'s serialization (§9).
- `apps/api/app/persistence/database.py` — fail-closed
  production-database guard (§8 below).
- `apps/api/app/main.py` — `mark_api_process_started()` call (§8
  below).
- `apps/api/tests/test_copilot_skills_evidence.py` — updated/added
  tests for all five skills' material fixes and corrected version
  assertions (§4).
- `apps/api/tests/test_copilot_skill_cache.py` — bounding/eviction/
  leak-fix/`evidence_content_key` regression tests (§7, §7a, §8a).
- `apps/api/tests/test_copilot_skill_answer_cache.py` — Layer B
  single-flight concurrency proofs (§7).
- `apps/api/tests/test_copilot_skill_evidence_payload_size.py` —
  extended to all five skills (§10a).
- `docs/AI_HANDOVER/12B5B_COPILOT_INTELLIGENCE_AND_CACHE.md` — this
  remediation (every section marked "remediation correction" or
  "remediation addition").

**Modified/new (final safety/bounded-evidence review):**
- `apps/api/app/persistence/database.py` — production-database guard
  **replaced**: `ASI_DB_RUNTIME_CONTEXT` context-based authorization,
  removing `_api_process_started`/`mark_api_process_started()` entirely
  (§18).
- `apps/api/app/main.py` — `mark_api_process_started()` call removed;
  replaced with an explanatory comment only (§18).
- `apps/api/app/amazon/listings_worker.py`,
  `apps/api/app/amazon/orders_worker.py` — each `main()` now declares
  its own `ASI_DB_RUNTIME_CONTEXT` after its pre-existing enable gate
  passes (§18).
- `apps/api/app/amazon/listings_job_admin.py` — `main()` now declares
  `ASI_DB_RUNTIME_CONTEXT=admin` at entry (§18).
- `scripts/dev.sh` — backend child now launched with
  `ASI_DB_RUNTIME_CONTEXT=api` via `env`, scoped to that one child
  (§18).
- `docs/AI_HANDOVER/14_LOCAL_DEVELOPMENT_SETUP.md`,
  `docs/AI_HANDOVER/12B3H_LISTINGS_WORKER_OPERATIONS.md` — manual
  uvicorn command and production runbook updated for
  `ASI_DB_RUNTIME_CONTEXT` (§18).
- `apps/api/app/copilot/skills/cancellations.py` — bounded `records`
  to `AFFECTED_SKU_LIMIT`, new truncation metrics (§18a).
- `apps/api/app/copilot/skills/contracts.py` — `cancellation_
  operational_anomaly_detector` re-bumped `1.0.0` -> `1.1.0` (§18a).
- `apps/api/tests/test_persistence_production_guard.py` — fully
  rewritten for the new context-based design, including tests that
  invoke the real `get_engine()` for every recognized context (§18).
- `apps/api/tests/test_amazon_listings_worker.py`,
  `apps/api/tests/test_amazon_orders_worker.py` — new tests proving
  each worker's real `main()` declares its context correctly, and only
  after its enable gate (§18).
- `apps/api/tests/test_amazon_listings_job_admin.py` — **new file**:
  tests for the admin CLI's context declaration and its
  `terminalize_queued_listings_job` real behavior (§18).
- `apps/api/tests/test_copilot_skills_evidence.py` — six new
  Cancellation bounding/large-population/isolation tests, corrected
  version-dict assertion (§18a).
- `apps/api/tests/test_copilot_skill_cache.py` — new test proving the
  answer-cache key changes when Cancellation's full-population
  aggregate changes with identical top-N records (§18a).

## 14. Test/build results

**Original 12B.5B pass:** Backend 1,351 passed/62 skipped; Frontend 161
passed (9 files); `tsc --noEmit` clean; `npm run build` succeeded.

**Remediation pass:** Backend 1,383 passed/62 skipped; Frontend 161
passed; `tsc`/build clean.

**Final safety/bounded-evidence review, re-verified:**
- Backend: `cd apps/api && uv run pytest` → **1,420 passed, 62
  skipped** (+37 tests over the remediation pass's 1,383/62: the
  rewritten production-database guard suite, worker/admin context-
  declaration tests, the new admin CLI test file, six Cancellation
  bounding tests, one cross-population cache-key test; 0 regressions).
- Focused cache/single-flight/guard suite (`test_copilot_skill_
  cache.py`, `test_copilot_skill_answer_cache.py`,
  `test_copilot_skill_evidence_cache_integration.py`,
  `test_persistence_production_guard.py`) → repeated 5 consecutive
  times, **67 passed every run, 0 flakes**.
- Five-skill evaluation + Copilot backend suite
  (`test_copilot_skills_evidence.py`, `test_copilot_skill_evals.py`,
  `test_copilot_skills_end_to_end.py`,
  `test_copilot_skill_planner_routing.py`, `test_copilot_tools.py`,
  `test_copilot_synthesis.py`) → **123 passed**.
- `bash scripts/test_dev_sh.sh` → all orchestration checks passed,
  confirming the `env ASI_DB_RUNTIME_CONTEXT=api` wrapping around the
  backend child is fully transparent to `dev.sh`'s own process-
  management/shutdown/duplicate-detection logic.
- Frontend: `cd apps/web && npm test -- --run` → **161 passed (9
  files)**, unchanged — this review made no frontend changes.
- `npx tsc --noEmit` → clean.
- `npm run build` → succeeds; all 14 routes generated, `/copilot`
  still statically prerendered.
- Migration/model-drift tests
  (`test_migration_chain_matches_orm_metadata.py`,
  `test_amazon_seller_identity_schema.py`) → included in, and passing
  as part of, the full 1,420-passed backend run above.
- `uv run alembic heads` → single head, unchanged:
  `0013_orders_durable_pagination`. **No migration was added or
  needed** — Alembic never calls `get_engine()`/`session_scope()`, so
  the guard redesign cannot affect it either way, confirmed unchanged.
- Guarded PostgreSQL collection (`tests/postgres/`) → **62 tests
  collect without error** (`pytest tests/postgres --collect-only`) and
  **62 skipped** on a normal run — no disposable PostgreSQL configured,
  and no Supabase connection made at any point during this review's
  verification.
- Seven unrelated Log Analyzer/ADR paths (`docs/adr/README.md`,
  `docs/adr/0007-...md`, `docs/adr/0008-...md`, `docs/operations/
  OPS1_*.md` × 4) — confirmed via `git status`/`git diff --cached`:
  present exactly as they were before this review (the same 4-line
  `README.md` diff observed before this review began), untouched by
  any edit in this pass, and still unstaged.

## 15. PostgreSQL checks awaiting CI

None required by this milestone's own changes (no migration, no
concurrency-control code touched). The existing guarded PostgreSQL
suite (including the 12B.5A concurrency-fix tests) will still run as
part of normal CI on any future push and is expected to remain green,
since nothing in this milestone altered `app/persistence/repositories.
py`'s ingestion-run claim/enqueue/cooldown logic.

## 16. Migration/deployment/live-sync actions requiring authorization

- **Production shared cache backend** (Redis or equivalent) — required
  before this deployment adds a second API replica; not implemented
  here, classified honestly in §7a as a separately authorized
  deployment increment.
- **A live LLM call** — required to observe real `cached_input_tokens`
  provider-side savings and an actual (not "structured for") working
  prompt-cache hit; not performed here (§9, §10).
- No migration, Supabase mutation, Amazon resynchronization/backfill,
  worker start, or production data mutation occurred or is proposed by
  this milestone or this remediation.
- This remediation additionally did **not**: commit, push, open/merge
  a PR, apply a migration, mutate Supabase, make a live Amazon or LLM
  call, start a worker, or deploy a shared cache — the same
  authorization boundary as the original pass, reiterated and held.

## 17. Remaining risks and deferred datasets

- ~~Layer B (answer cache) has no single-flight coalescing~~ —
  **resolved this remediation** (§7): `AsyncSingleFlight` now coalesces
  concurrent identical Layer B requests.
- ~~All five skills bumped to a major `2.0.0` version despite no
  formula change~~ — **resolved this remediation** (§4): versions now
  reflect actual, individually-audited change, and four skills
  received genuine formula/evidence-logic improvements in the process.
- ~~The in-process cache has no maximum-entry bound~~ — **resolved this
  remediation** (§7): `max_entries`-bounded LRU eviction, plus a fixed
  `SingleFlight` memory leak found during the same audit.
- **The in-process cache is confirmed insufficient for >1 API
  replica** — unresolved by design (§7a): still the single most
  important pre-scaling action item, now with an honest production-
  readiness classification and a concrete Redis-behind-`SkillCache`
  recommendation instead of an unqualified "not sufficient" note.
- **Provider prompt-prefix caching remains unobserved** — the
  structural design (§9) is stronger after this remediation
  (`sort_keys=True` serialization fix) but still cannot be labeled
  "confirmed" or "observed working" without an authorized live call.
- **Cancellation/Operational Anomaly Detector's `records` list is
  unbounded** (§10a, newly flagged this remediation) — every distinct
  SKU on a cancelled order in the window is returned with no top-N
  limit; low risk at the scale measured here, but a seller with an
  extreme number of distinct cancelled-order SKUs in one window could
  produce an unusually large evidence payload. Not fixed this pass
  (§1 confirmed no formula/shape change was warranted for this skill's
  actual logic); flagged as a genuine, separately-scoped follow-up.
- `packages`/shipment-timing data is parsed but not persisted or used
  (§2) — deliberate, but revisit if a future skill needs it.
- No Listing status/issue change-history table exists (§6) — trend/
  anomaly detection for Orders is unaffected (Orders already has
  genuine event-level rows), but a "did this listing's issue just
  appear or has it been there for weeks" question cannot be answered
  from Listings alone yet.
- Sales rank/BSR and deep category classification remain unavailable
  without a Catalog Items API integration — explicitly out of scope.
- `expenses`/`promotions` Orders data remains unrequested — would need
  a live-Amazon-facing client change under separate authorization.
- **No tokenizer is installed in this environment** (§10a) — every
  payload measurement in this document is bytes/characters only, never
  an estimated or real token count, except where explicitly labeled a
  rough chars÷4 estimate (§9's `SYSTEM_PROMPT` figure).
- **The new production-database guard (§18) is a code-level safety
  net, not a substitute for developer discipline** — it protects any
  code path that calls `get_engine()`/`session_scope()` against a
  remote (non-loopback) database from outside the running API process,
  but a future ad-hoc script that imports `app.main` incidentally (even
  without needing the FastAPI `app` object) would set the same "API
  process started" flag and bypass it. This is judged an acceptable,
  narrow residual risk: the flag is set by importing the one module
  every real deployment must import to serve traffic, and no ad-hoc
  diagnostic script has a legitimate reason to import `app.main`.

## 18. Fail-closed production-database guard (remediation addition,
Section 8; **redesigned** in the follow-up final safety/bounded-
evidence review — this section describes the current, shipped design
only)

**Framing, stated explicitly per the final review's own audit:** this
is a **safety interlock against accidental misuse, not an
authentication mechanism or a security boundary.**
`ASI_DB_RUNTIME_CONTEXT`/`ASI_ALLOW_PRODUCTION_DB_ACCESS` are plain
environment variables — anyone with shell access to a process (the
same access already needed to read `DATABASE_URL` or `.env`) can set
either one trivially, with no password or cryptographic proof
involved. It is not designed to, and does not, stop a determined or
malicious actor with that access; it exists to make the *unintentional*
failure mode below require a deliberate, legible action instead of
happening by default — the same class of interlock this codebase
already uses for `ASI_LISTINGS_WORKER_ENABLED`/`ASI_ORDERS_WORKER_
ENABLED`, never a substitute for real access control over who can
reach the machine/process/shell at all. See `app/persistence/
database.py`'s own module-level comment for the identical statement in
the source itself.

**Incident, disclosed in §1:** an ad-hoc `uv run python -c "..."`
diagnostic script, run outside pytest's fixtures, resolved this
project's real Supabase `DATABASE_URL` (inherited the same way any
script importing `app.persistence.database` does — from the
developer's own shell/`.env`) and attempted a write. `session_scope()`
rolled it back only because that specific write collided with an
existing row; a write that did not collide would have committed for
real.

**First design, rejected:** the initial fix authorized a connection by
checking whether `app.main` had been imported (an `_api_process_
started` flag set at import time). A follow-up review found this
unsafe in both directions: it broke the Listings/Orders workers and the
Listings job admin CLI (each a *separate OS process* that never
imports `app.main`, so the flag could never be true for them even when
legitimately run against a real database), and any diagnostic-style
script that merely did `from app.main import app` (exactly what `tests/
conftest.py` already does, for its own unrelated `TestClient` needs)
would have silently *disabled* the protection. A Python import is not a
legitimacy signal — it says nothing about how or why a process was
actually started. Replaced, not patched.

**Current design:** `ASI_DB_RUNTIME_CONTEXT`, an explicit environment
variable each legitimate process's own launcher/entry point sets —
never a side effect of any import. `get_engine()` calls `_guard_engine_
creation(url)` before constructing any non-SQLite, non-loopback engine,
and it authorizes exactly one of:

| Context (`ASI_DB_RUNTIME_CONTEXT=`) | Set by | Additional requirement |
|---|---|---|
| `"api"` | The launch command — `./scripts/dev.sh` (wired via `env ASI_DB_RUNTIME_CONTEXT=api`, scoped only to the backend child) and the manual `uv run uvicorn app.main:app ...` command documented in `docs/AI_HANDOVER/14_LOCAL_DEVELOPMENT_SETUP.md`. `app/main.py` itself sets nothing. | None. |
| `"listings_worker"` | `app.amazon.listings_worker.main()`, internally, only *after* its own pre-existing `ASI_LISTINGS_WORKER_ENABLED` gate has already passed. | None — not a new opt-in surface. |
| `"orders_worker"` | `app.amazon.orders_worker.main()`, symmetrically, after `ASI_ORDERS_WORKER_ENABLED`. | None. |
| `"admin"` | `app.amazon.listings_job_admin.main()`, unconditionally at entry (this CLI is never imported or started by accident). | **Yes** — also requires `ASI_ALLOW_PRODUCTION_DB_ACCESS=1`. Self-declaring as `"admin"` alone is never sufficient for a controlled production administrative operation. |
| *(unset/unrecognized)* | Nothing — the default state of an unclassified ad-hoc script. | Requires `ASI_ALLOW_PRODUCTION_DB_ACCESS=1` to proceed at all. |

SQLite is always exempt (never "production-like" by construction). A
Postgres/network URL whose host resolves to a loopback address
(`localhost`/`127.0.0.1`/`::1`) is also exempt regardless of context —
the same class of database `tests/postgres/`'s own pre-existing guard
already trusts a developer to run disposable data against directly.
Tests never need to set anything: `conftest.py` forces `DATABASE_URL=
sqlite://`. Alembic is entirely unaffected: `migrations/env.py` builds
its own engine directly from `sqlalchemy_database_url()` via
`engine_from_config`, never calling `get_engine()`/`session_scope()`.

**Never prints the database URL or credentials:** `_looks_like_
production_database()` returns only a boolean, never the URL, host, or
any parsed fragment of it; `ProductionDatabaseGuardError`'s message is
a fixed, generic string that never interpolates the URL — unchanged
from the original design.

**Regression tests** (23 tests in `tests/test_persistence_production_
guard.py`, plus context-declaration tests added directly to `tests/
test_amazon_listings_worker.py`, `tests/test_amazon_orders_worker.py`,
and the new `tests/test_amazon_listings_job_admin.py`): every context
value individually authorizes (or, for `"admin"` alone, correctly
fails to authorize) a remote database; the narrow override works and
requires its exact value; sqlite/loopback are always exempt; the
exception never contains the URL/credential/host; **the real,
unmodified `get_engine()` function** succeeds for each of `"api"`,
`"listings_worker"`, `"orders_worker"`, and `"admin"`-with-override
against a synthetic remote URL (network I/O mocked out — `create_
engine`/`_bootstrap_organization` are monkeypatched to no-ops, so
these tests exercise the guard's real decision inside `get_engine()`'s
real body without ever touching a network, Supabase included) and
fails closed with no context set or with `"admin"` alone; each real
worker's `main()` is proven to set its own context correctly only
*after* its own enable gate passes, and never otherwise; the admin
CLI's `main()` is proven to declare its context unconditionally at
entry, and its `terminalize_queued_listings_job` function is proven to
succeed for a genuinely queued run and fail closed for a nonexistent
one.

**How local development stays working:** `./scripts/dev.sh` and the
manual command in `docs/AI_HANDOVER/14_LOCAL_DEVELOPMENT_SETUP.md` were
both updated to set `ASI_DB_RUNTIME_CONTEXT=api` — the only two
documented ways this repository starts the API locally. No other
deployment mechanism is documented or invented in this repository; if
one exists outside it, it must also set this variable, exactly as it
must already set `DATABASE_URL` and the SP-API/LWA credential set.

**How an authorized production operation opts in:** set
`ASI_ALLOW_PRODUCTION_DB_ACCESS=1` in the shell running that one
script, for that one invocation — never in `.env`, never as a
persistent environment default.

**Does not modify `.env`, Supabase, or any credential** — the guard is
pure Python/shell control flow across `app/persistence/database.py`,
`app/main.py`, both worker modules, the admin CLI, `scripts/dev.sh`,
two documentation files, and new/extended test files; no configuration
file, secret, or database record was touched to build or verify it,
and no live Amazon or database connection was made during verification.

## 18a. Bounded Cancellation evidence (final safety/bounded-evidence
review)

**Gap, flagged but not fixed in the 12B.5B remediation pass:**
Cancellation/Operational Anomaly Detector's `records` listed *every*
distinct SKU present on a cancelled order in the window, with no limit
at all — the one skill among the five with a genuinely unbounded
per-record payload. §10a of this document originally flagged it as a
known, deliberately-unfixed follow-up; this review fixes it.

**Fix** (`app/copilot/skills/cancellations.py`): `AFFECTED_SKU_LIMIT =
25` (matching Listing Health Prioritizer's/Listing Risk by Order
Exposure's own `DEFAULT_RESULT_LIMIT`) now bounds `records` to the
top-N SKUs by how many *distinct cancelled orders* each was present on
— deterministic, tied-broken by `seller_sku` ascending. Three new
metrics make the truncation explicit rather than silent:
`affected_sku_count` (the full matching population),
`returned_sku_count`, `sku_list_truncated`. A new `limitations` entry
is added whenever truncation occurs, explicitly stating that the
displayed SKUs are a prioritized subset and that every cancellation
count/rate still reflects the full population. Every aggregate
cancellation metric (`total_orders`, `cancelled_orders`,
`cancellation_rate`, the previous-period comparison, `is_anomalous`) is
computed from the full, untruncated order-level query this skill
already ran (`_window_cancellation`) — never from the truncated SKU
list — so bounding `records` never changes what the skill can honestly
claim about the population. `skill_version` bumped `1.0.0` -> `1.1.0`
(§4's table updated accordingly) — a genuine evidence-shape change,
not a presentational one.

**Proven by test** (`tests/test_copilot_skills_evidence.py`, six new
tests): `records` stays exactly `AFFECTED_SKU_LIMIT` long, and the
measured evidence payload stays under 20KB, even when 55 distinct SKUs
are seeded; `affected_sku_count`/`returned_sku_count`/`sku_list_
truncated` report correctly; selection is deterministic by cancelled-
order count then by `seller_sku` (both a clear-winner case and an
exact-tie case are tested); every aggregate metric equals the true
full-population total even when the population is well past the
truncation limit (not recalculable from the visible 25 alone); no SKU
ever leaks across two different marketplace participations under the
same organization. A seventh test in `tests/test_copilot_skill_cache.
py` proves the cache-key implication directly: two evidence payloads
sharing byte-identical top-25 `records` but a different full-population
aggregate (`affected_sku_count` 30 vs. 40) produce **different**
`evidence_content_key` values — the answer cache can never conflate
them merely because the *displayed* records happen to match.

**Known, unfixed limitation, honestly stated:** no skill in this
five-skill set combines currency amounts across marketplaces, and
Cancellation itself never touches money at all (order-level
`was_cancelled` and SKU presence only) — so "mixed-currency leakage"
does not apply to this skill's own evidence by construction; the
cross-marketplace-participation isolation test above is this skill's
relevant analog, and it passes.
