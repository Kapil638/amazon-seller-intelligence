# 12B.4C — Typed Amazon Orders API Client and Privacy-Safe Parser

Durable record of the 12B.4C implementation pass. Client + DTOs + tests
only. No database persistence, ingestion orchestration, checkpoint,
worker, trigger route, UI, Copilot integration, or live Amazon call was
made while producing this milestone. Branch: `milestone-12b4c-orders-client`,
created from verified `main` (`064c2862af15d687715d3fc85c81f8665814c495`).

## 0. Final pre-push audit (two items resolved after initial review)

A reviewer requested two items before push, both now resolved:

1. **Literal `getOrder` envelope fixture added.** The initial pass proved
   `getOrder`'s URL construction and order-ID encoding using a
   `searchOrders`-fixture order wrapped inline in `{"order": ...}` at test
   time — that proves parser reuse, but never independently pinned
   Amazon's actual top-level `GetOrderResponse` shape. Added
   `tests/fixtures/sp_api/orders/17_get_order_response_envelope.json`,
   structurally copied from the pinned `2026-01-01` model's own
   `GetOrderResponse`/`Order` definitions (same synthetic `FIXTURE-*`
   placeholder convention as every other fixture in the directory — no
   real seller/order data). It is also the first fixture in the directory
   to populate `Order.packages`, so it doubles as fixture-driven coverage
   of `OrderPackage`/`PackageStatus`, which had none before. New test:
   `test_get_order_parses_the_committed_literal_envelope_fixture`.
2. **Deferred fulfillment fields explicitly audited and classified.** See
   §4a below. `ItemFulfillment.picking`/`.shipping`, `OrderPackage
   .packageItems`, and `.shipFromAddress` were each individually checked
   against the pinned model's field-level schema (not left as one
   generic "out of scope" note) and classified as safe-and-valuable,
   intentionally-deferred, or prohibited. **No field was added to any
   model as a result of this audit** — per this review round's explicit
   instruction, the classification itself is the deliverable, so 12B.4D
   (or whichever future increment adds any of these) has a privacy
   decision already on record instead of having to make one under
   ingestion-implementation time pressure. `orders_models.py`'s
   `ItemFulfillment`/`OrderPackage` docstrings now cite this audit
   per-field, not a vague "may be added later."

## 1. Supported operations and API version

Pinned against `docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md`,
re-verified directly against the primary-source model file during this
milestone (SHA-256 `4bd47126b466b94ccb04d822e3b94edee1fa7977a8b376a8c329852ef61be431`
— identical to the hash 12B.4A recorded; zero drift between the two
passes, confirmed by re-fetching and re-parsing the raw model JSON, not by
re-reading the prior report).

- `searchOrders` — `GET /orders/2026-01-01/orders`, one page per client
  call. No internal loop over `pagination.nextToken`/`paginationToken`.
- `getOrder` — `GET /orders/2026-01-01/orders/{orderId}`, one order per
  call. Order ID percent-encoded into the URL path.
- Both confirmed against the live-refetched model: `GetOrderResponse` is
  `{"order": Order}` (required, no further envelope); `SearchOrdersResponse`
  is `{"orders": Order[], "pagination"?: {"nextToken"?: string}}` — flat,
  no `payload` wrapper (unlike deprecated v0).
- Query-array parameters (`marketplaceIds`, `includedData`) have no
  `collectionFormat` declared in the Swagger 2.0 model, which defaults to
  comma-separated (`csv`) — same convention already used by
  `listings_client.py`.

**Phase 1 finding, no discrepancy:** the pinned contract report's central
technical claims (operations, parameters, response shape, rate limits,
role list, RDT-not-required) were re-verified against the live model file
and matched exactly. No correction was required to the 12B.4A report.

**Signing infrastructure finding (Phase 2):** the task brief for this
milestone listed "AWS SigV4 signing" among the infrastructure to reuse.
This repository has no SigV4 signing module at all — `AmazonSpApiListingsClient`
and `AmazonSpApiSellersClient` both authorize purely with an LWA bearer
access token (`x-amz-access-token` header), which is this application's
actual, already-proven-live self-authorization model. `orders_client.py`
follows that same pattern exactly. No new signing infrastructure was
introduced or is needed.

## 2. Files changed

- `apps/api/app/amazon/orders_models.py` (new) — Amazon-shaped DTOs
  (`extra="ignore"`) plus ASI-owned result wrappers (`extra="forbid"`).
- `apps/api/app/amazon/orders_client.py` (new) — `AmazonSpApiOrdersClient`
  (`search_orders`, `get_order`), request dataclasses, retry/backoff,
  order-ID/pagination-token log redaction.
- `apps/api/tests/test_amazon_orders_client.py` (new) — 44 tests.
- `apps/api/tests/fixtures/sp_api/orders/17_get_order_response_envelope.json`
  (new, added in the pre-push review round) — the literal
  `GetOrderResponse` envelope, plus its `README.md` row.
- No file outside `apps/api/app/amazon/`, its own test file, and the one
  new fixture (+ that fixture directory's `README.md`) was touched. No
  migration, ORM model, route, or CI workflow file changed.

## 3. Client architecture

Mirrors `AmazonSpApiListingsClient` deliberately:

- Reuses `LwaClient` for token resolution (constructed internally unless
  injected) and the shared `SpApi*` exception taxonomy from
  `app.core.exceptions` — **no new exception type was added**; every
  failure mode (config, auth, rate limit, invalid request, transient
  failure, parse failure) already had a corresponding type from the
  Listings/Sellers work.
- `httpx.BaseTransport` injection point for tests (`httpx.MockTransport`
  everywhere in the test suite — no test performs a real network call).
- Injectable `sleep`/`jitter` callables so tests never sleep in wall-clock
  time, including for the documented ~178.6s `searchOrders` sustained
  interval (never invoked directly by this client — see §5).
- URL and query parameters are built once per logical call and reused
  verbatim across every retry attempt; only `x-amz-date` (built fresh via
  `_headers()`) and the access token's freshness vary.
- No SQLAlchemy, repository, database-session, FastAPI-route, worker, or
  UI import anywhere in either new file — enforced by
  `test_no_db_repository_session_or_route_imports` (AST-based, mirrors
  `test_validation_modules_do_not_import_ingest_or_intelligence`'s
  existing pattern in this repository).
- `search_orders()` has no `while`/`for` loop and calls the internal
  retry helper exactly once per invocation — enforced by
  `test_search_orders_has_no_internal_pagination_loop` via
  `inspect.getsource`, not just code review.

### Log redaction — order ID and pagination token

`listings_client.py` already established that `httpx`'s own INFO-level
request logging exposes the full request URL regardless of what this
module's own `logger.warning(...)` calls include, and fixed it for
`sellerId` with a centralized, idempotent `logging.Filter` on the `httpx`
logger. This milestone found and fixed the identical class of leak twice
over for Orders:

1. `getOrder`'s URL embeds the caller-supplied order ID in the path.
2. `searchOrders`' URL carries `paginationToken` in the query string when
   a caller passes one — and Phase 3 of this milestone's own brief
   explicitly requires that token never be logged, persisted, or exposed.

`_RedactOrdersOrderIdFilter` (installed once per process, at first client
construction, exactly like the Listings filter) rewrites both: the
`{orderId}` path segment on `getOrder` URLs and the `paginationToken=`
query value on `searchOrders` URLs, to fixed placeholders, before a
`LogRecord` is emitted. Every other URL (LWA, Sellers, Sandbox, Listings,
and every non-redacted part of Orders' own URLs) passes through
unaffected — proven by `test_other_endpoints_unaffected_by_order_id_redaction_filter`
and the before/after reproduction test
`test_httpx_logger_redaction_of_order_id`. Scoped to the `httpx` logger
only, for the same reason documented in `listings_client.py` (`httpcore`
tracing uses per-component sub-logger names the parent filter cannot see,
and this test suite's exclusive use of `httpx.MockTransport` means
`httpcore` code never executes in tests regardless).

## 4. Non-PII parsing boundary

Every field in `orders_models.py` is built from an explicit,
per-field allowlist matching the official schema — there is no `dict`
passthrough, no unrestricted `model_dump()`-based construction, and no
generic JSON blob anywhere in either DTO or result type. The boundary is
enforced structurally, not by a runtime filter step: a field that is
never declared on a Pydantic model (`extra="ignore"`) never becomes a
Python attribute at all when Amazon's response is parsed, so it cannot
reach a `model_dump()`, a log line, or an exception through this model —
this is stronger than "redact before logging."

**Never declared anywhere in `orders_models.py`** (present on the
official schema, deliberately omitted): `Order.buyer`, `.recipient`,
`.payment`, `.tax`, `.fulfillmentOrders`; `OrderItem.expense`,
`.promotion`; `ItemProduct.serialNumbers`, `.customization`;
`ItemCancellationRequest.cancelReason`,
`ItemCancellationExecution.cancelReason`; `GiftOption.giftMessage`;
`OrderPackage.shipFromAddress`, `.packageItems`; `ItemFulfillment.picking`,
`.shipping`.

**Two confirmed bundled-field hazards, resolved at the most granular
field level** (per this milestone's explicit brief, a finer-grained
resolution than 12B.4A's report anticipated — see that report's own
"cannot be solved by omitting an `includedData` flag" note):

- `packing.giftOption.giftWrapLevel` **is** modeled (a service-tier label,
  not customer-authored content) while `giftOption.giftMessage`
  (free-text, buyer-authored) sharing the same object is not modeled at
  all. Proven by `test_gift_message_field_does_not_exist_on_model` and
  `test_gift_wrap_level_retained_gift_message_dropped`.
- `cancellationRequest.requester` / `cancellationExecution.cancelledBy`
  (enum fields) are modeled; `cancelReason` (free text) on both sibling
  objects is not. Proven by `test_cancel_reason_field_does_not_exist_on_models`
  and `test_cancellation_enums_retained_free_text_reason_dropped`.

Fixture `16_restricted_pii_fields_present.json` (every excluded field
populated at once: `buyer`, `recipient`, `payment`, `tax`,
`product.customization`, `fulfillment.packing.giftOption`) is asserted
against DTO dumps, exception strings, and captured logs simultaneously in
`test_all_synthetic_pii_removed_from_dto_dump_and_logs` — all 12 synthetic
PII substrings from that fixture are asserted absent from all three
surfaces in one test.

### Forward-compatibility boundary (`extra=` policy)

Two different `extra=` policies are used deliberately, per this
milestone's brief:

- **Amazon-shaped response models** (`Order`, `OrderItem`, `Money`,
  every nested type mirroring the official schema) use `extra="ignore"`.
  Amazon owns this schema and adds fields over time (proven by fixture
  `15_unknown_additive_fields.json` — `test_unknown_additive_fields_are_ignored_not_fatal`);
  failing closed on an unrecognized field would be a production outage
  waiting for Amazon's next routine additive change.
- **ASI-owned result/provenance wrappers** (`OrdersPage`, `OrderResult`,
  `OrdersPageProvenance`) use `extra="forbid"`. Nothing external ever
  calls `model_validate()` on these — they are always constructed by this
  codebase's own client code from already-known fields, so `extra="forbid"`
  catches an internal programming mistake, not Amazon drift. Proven by
  `test_asi_owned_wrapper_models_forbid_unexpected_fields`.

### Null vs. missing vs. malformed

The pinned model is Swagger 2.0 with zero `nullable`/`x-nullable`
occurrences (re-verified directly against the primary source, identical
method to 12B.3C's Listings finding). `optional_not_null()` — imported
from `listings_models.py`, not duplicated, since it is a small,
provider-agnostic Pydantic helper — is used for every optional field:
missing key → `None`; explicit JSON `null` → validation failure
(`test_explicit_null_on_optional_field_is_rejected`); a malformed
required field or a malformed top-level envelope both fail the same way
(`test_malformed_envelope_and_field_types_rejected`); a structurally
valid, genuinely empty response (`08_empty_result.json`) is not an error
(`test_valid_empty_result_is_not_an_error`).

### Money / Decimal

`Money.amount` is documented as a `Decimal` transmitted as a JSON
*string*. `orders_models.DecimalAmount` rejects a raw Python `float`
before it reaches `Decimal(...)` (a `BeforeValidator`, since Pydantic
would otherwise happily coerce a JSON number that already lost precision
one step earlier, in the JSON parser) — proven directly by
`test_raw_float_amount_is_rejected`, and cross-currency exactness (JPY
zero-decimal-styled integer string, EUR two-decimal string) proven by
`test_decimal_preservation_across_currencies`. Scope note: this validator
checks specifically for `float` (the documented precision-loss hazard); it
does not separately reject a bare unquoted JSON integer, since an integer
carries no precision-loss risk and the documented contract shape is a
string regardless — not the specific hazard this validator exists to
catch.

## 4a. Deferred fulfillment-fields audit (pre-push review item 2)

12B.4A's original report and this milestone's first pass both noted
`ItemFulfillment.picking`/`.shipping` and `OrderPackage.packageItems`/
`.shipFromAddress` as generically "out of scope." This audit replaces
that generic note with an explicit, field-level classification against
the pinned `2026-01-01` model's own sub-schemas, so a future increment
(12B.4D or later) has a privacy decision already on record rather than
having to make one under ingestion-implementation time pressure.

| Field (path) | Underlying shape | Classification | Reasoning |
|---|---|---|---|
| `ItemFulfillment.picking.substitutionPreference.substitutionType` | enum: `CUSTOMER_PREFERENCE`, `AMAZON_RECOMMENDED`, `DO_NOT_SUBSTITUTE` | **Safe and valuable now** (not added) | Zero PII; describes substitution policy — useful for out-of-stock/substitution analytics. Not added in 12B.4C because this milestone is client/parser only; a conscious future choice, not an oversight. |
| `ItemFulfillment.picking.substitutionPreference.substitutionOptions[]` | `ItemSubstitutionOption`: `asin`, `sellerSku`, `title`, `quantityOrdered`, `measurement` | **Safe and valuable now** (not added) | Every field mirrors an already-approved non-PII `ItemProduct`-shaped field (ASIN/SKU/title/quantity). Same "not added yet, not an oversight" reasoning as above. |
| `ItemFulfillment.shipping.scheduledDeliveryWindow` | `DateTimeRange`: `earliestDateTime`/`latestDateTime` | **Intentionally deferred** | Plain timestamps, non-PII, but no stated analytics use in this narrow first slice. |
| `ItemFulfillment.shipping.shippingConstraints` | `ItemShippingConstraints`: pallet-delivery / cash-on-delivery / signature-confirmation / recipient-identity-verification / recipient-age-verification (`ConstraintType` = `MANDATORY` flags) | **Intentionally deferred** | Non-PII operational/compliance flags. Marginal analytics value for this slice's stated goals (profit/revenue/ads intelligence), not zero — a defensible future addition, but not made here. |
| `ItemFulfillment.shipping.internationalShipping.iossNumber` | string — EU Import One-Stop-Shop VAT registration number | **Prohibited** | The seller's own tax-registration identifier, not customer PII — but the same sensitivity class this schema's own `TAX`-gated `taxRegistrationNumber` is already excluded for elsewhere in this module. Must never be modeled even if `internationalShipping` is otherwise added for its non-tax fields (there are none currently — this is the object's only field). |
| `OrderPackage.packageItems[].orderItemId` / `.quantity` | string / integer | **Intentionally deferred** | Non-PII order/item-to-package linkage; would be safe to add for package-level reconciliation, not needed by this slice's stated goals. |
| `OrderPackage.packageItems[].transparencyCodes` | `string[]` | **Prohibited** | Amazon Transparency program serialization/anti-counterfeiting codes tied to a specific physical unit — the same supply-chain-sensitivity class this module already excludes `ItemProduct.serialNumbers` for (12B.4A). Must never be modeled even if `orderItemId`/`.quantity` above are later added — would require a `PackageItem` model that omits this one field, not a straight schema mirror. |
| `OrderPackage.shipFromAddress` | `MerchantAddress`: name, address lines, city, district/county, state/region, municipality, postal code, country code | **Intentionally deferred** | Not customer PII (the seller's own warehouse/facility address) — but 12B.4A's original, unrevisited reasoning stands: excluding it preserves a simple, audit-friendly "no address data anywhere in this schema" invariant rather than partially excepting one non-customer address. A future increment could reopen this with its own reviewed justification. |

No code change resulted from this audit — every field above was already
absent from `orders_models.py` before this review (the omissions were
correct by default; they simply lacked this documented reasoning). The
`ItemFulfillment` and `OrderPackage` docstrings in `orders_models.py` now
cite this table per field.

## 5. Rate-limit and retry behavior

This client's own retry loop is short-lived, bounded, in-process
transient-failure handling only — **it is not, and must not be read as,**
the durable ~178.6s `searchOrders` pacing documented in 12B.4A. That
pacing (spanning many separate calls over up to ~59.5 minutes for a
depleted burst) is explicitly 12B.4D's responsibility (durable worker +
`amazon_ingestion_runs` retry/lease machinery), consistent with this
milestone's brief ("Keep long-duration scheduling and durable checkpoint
behavior out of this client").

What this client does implement, all proven by dedicated tests:

- Retries only `429`, `5xx`, and transport (timeout/connect) failures.
  Never retries authentication (`401`/`403`), invalid-request (other
  4xx), or parse/privacy-validation failures — the latter two are
  structurally impossible to retry-loop, since parsing happens only after
  the retry loop already returned a successful HTTP status.
- Honors a valid `Retry-After` header on `429` exactly (not exponential
  backoff), bounded to the client's own configured `max_delay_seconds`
  ceiling so an unexpectedly large or malicious value can never stall the
  bounded retry loop past its own limit.
- Falls back to bounded exponential backoff with full jitter when
  `Retry-After` is absent or unparseable.
- Caps attempts via `max_attempts`; exhaustion maps `429` →
  `SpApiRateLimitedError`, `5xx`/transport → `SpApiRequestFailedError`.
- Surfaces Amazon's `x-amzn-RateLimit-Limit` header (when present) via
  `OrdersPageProvenance.rate_limit`, sanitized (length-bounded, control
  characters stripped) — for a future durable caller to act on; this
  client does not itself adapt pacing to it.
- Documented usage-plan constants (`SEARCH_ORDERS_DEFAULT_RATE_LIMIT_PER_SECOND`
  = 0.0056, burst 20; `GET_ORDER_DEFAULT_RATE_LIMIT_PER_SECOND` = 0.5,
  burst 30) are recorded as module constants for a future caller/test to
  reason against — not used by this client to pace requests.

No discrepancy was found between Amazon's documented rate-limit/error
behavior and the 12B.4A report during this milestone's re-verification.

## 6. Test results

- `apps/api && uv run pytest tests/test_amazon_orders_client.py -q` →
  **44 passed** (43 from the initial pass + 1 added for the literal
  `getOrder` envelope fixture in this pre-push review round). No real
  network call in any test (`httpx.MockTransport` throughout); no test
  sleeps in wall-clock time for a rate-limit scenario (injected `sleep`).
- `apps/api && uv run pytest tests/test_amazon_listings_client.py
  tests/test_sp_api_sandbox.py tests/test_amazon_seller_validation.py
  tests/test_migration_chain_matches_orm_metadata.py
  tests/test_amazon_seller_identity_schema.py -q` → **78 passed** (no
  regression in reused LWA/signing/client infrastructure or migration/
  model-drift checks).
- `apps/api && uv run pytest -q` (full backend suite) → **1151 passed, 60
  skipped**. All 60 skips are the existing, honest
  `ASI_ALLOW_DISPOSABLE_POSTGRES=1` opt-in guard on `tests/postgres/` —
  none is new, and none is a hidden failure; no migration or ORM model
  changed in this milestone, so no new guarded-PostgreSQL coverage was
  required.
- `uv run alembic heads` → `0012_orders_foundation (head)` — unchanged,
  single head.
- `test_client_construction_does_not_depend_on_process_environment`
  explicitly clears `SP_API_LWA_CLIENT_ID`/`SP_API_LWA_CLIENT_SECRET`/
  `SP_API_SANDBOX_REFRESH_TOKEN`/`DATABASE_URL` from the process
  environment before constructing a client with directly-injected
  synthetic settings, proving the suite does not accidentally depend on
  `apps/api/.env` or a real developer environment.
- Diff/secret/PII scan: only the 5 new files (§2) are untracked; the 7
  preserved Log Analyzer/ADR files remain byte-identical (SHA-256
  re-verified against the privately recorded pre-milestone baseline) and
  unstaged; no real credential-shaped string appears anywhere in the new
  files (only synthetic `Atza|test-...`/`Atzr|test-...`-style test
  constants, matching the existing Listings test convention); fixture
  `17_get_order_response_envelope.json` uses the same obviously-synthetic
  `FIXTURE-*` placeholder convention as every other fixture in its
  directory.

## 7. Remaining risks

1. **Production role possession remains unverified** (carried over from
   12B.4A, unchanged by this milestone): confirming the ASI production
   app holds at least one of the twelve endpoint-authorizing roles for
   `searchOrders`/`getOrder` requires checking Seller Central's Developer
   Console directly, or a live call — both out of this milestone's scope.
   Not a blocker for 12B.4C (no live call is made here), but 12B.4D must
   not assume role possession either way.
2. **`ItemFulfillment.picking`/`.shipping` and `OrderPackage.packageItems`/
   `.shipFromAddress` are now individually classified, not a blanket
   omission** — see §4a's per-field audit table. Two sub-fields
   (`picking.substitutionPreference.*`, `packageItems[].orderItemId`/
   `.quantity`) are classified safe-and-valuable but deliberately not
   added in this client/parser-only milestone; a future increment adding
   any of them should cite §4a rather than re-deriving the classification.
   Two sub-fields (`internationalShipping.iossNumber`,
   `packageItems[].transparencyCodes`) are classified **prohibited** and
   must never be modeled even if their sibling fields are added later.
3. **This client's retry ceiling is not Orders-rate-limit-aware.** The
   default `max_delay_seconds=30.0` is a reasonable bound for transient
   in-call retries, but is far shorter than the documented ~178.6s
   sustained interval — by design (durable pacing is 12B.4D's job), but a
   future caller must not mistake this client's short retry loop for real
   rate-limit compliance across multiple calls.

## 8. Responsibilities deferred to 12B.4D

- Durable, multi-call `searchOrders` pagination (looping until
  `pagination` is absent or a bounded per-job page cap is hit).
- Real rate-limit pacing against the documented ~178.6s sustained
  interval / burst-20 budget, including heartbeat renewal during
  inter-page waits.
- Incremental cursor / watermark management (`lastUpdatedAfter`, overlap
  window, checkpoint advancement gated on full-traversal success).
- `amazon_ingestion_runs` run creation/claim/finalize wiring for
  `run_type='orders'`.
- Upserting parsed `Order`/`OrderItem` DTOs into
  `amazon_seller_orders`/`amazon_seller_order_items` via the
  already-implemented (12B.4B) repository primitives.
- Worker process, HTTP trigger route, and any UI surface.
- Resolving the still-open Phase 4 point-11 tension from 12B.4A (one
  orders run per participation vs. one run spanning several
  participations) — unaffected by this milestone, since this client
  takes an explicit `marketplace_ids` tuple per call and has no opinion
  on run/participation scoping.

## Test summary

- 44/44 new Orders client/parser tests passing (43 initial + 1 added for
  the literal `getOrder` envelope fixture in this pre-push review round).
- 78/78 relevant existing (Listings client, sandbox, seller validation,
  migration-drift, seller-identity-schema) tests passing — no regression.
- 1151/1151 full backend suite passing, 60 honest skips (disposable
  Postgres opt-in) — unchanged in count from before this review round.
- Single Alembic head unchanged: `0012_orders_foundation`.
- Both pre-push review items resolved: literal `getOrder` envelope
  fixture added (§0.1); deferred fulfillment fields explicitly audited
  and classified, none added (§0.2, §4a).

**12B.4C ORDERS CLIENT AND PRIVACY PARSER IMPLEMENTED — READY FOR REVIEW**
