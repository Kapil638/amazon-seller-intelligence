# 19 — 12B.2A.1: Database Deployment Hardening Architecture Review

**Status:** Investigation/design complete, and subsequently **implemented** under the same 12B.2A.1 milestone (uncommitted at time of writing — see the 12B.2A.1 implementation report). The product-owner decision that followed this review chose differently from this document's original top recommendation: rather than Option B (a bootstrap script, `create_all()` + `stamp head`, leaving `0001` untouched), the approved and implemented design **repairs historical migration `0001` directly** (this review's Option A) and does **not** ship a `create_all + stamp head` path at all. That decision, and the resulting implementation, supersede §4–§5 and §14 below wherever they conflict; the rest of this review's investigation (§1–§3, §6–§13, §15) held up as the implementation's starting point and is left as originally written for the historical record.

**Depends on:** 12B.2A (committed `51a877c`), `docs/AI_HANDOVER/04_DATABASE_AND_MIGRATIONS.md` (now updated with the repaired-history section).

---

## 1. Verified root cause

The stated diagnosis ("migration `0001` calls current `Base.metadata.create_all()`, which collides with `0002`") is **confirmed correct**, and the investigation went one step further: this is not schema drift that accumulated over time. It was present from the very first commit.

`git log --diff-filter=A -- apps/api/migrations/versions/0001_m10_persistence.py` shows `0001` and `0002` were both introduced in a single commit, `c0706cb` ("feat: persist history, custom scoring, and client PDF reports"), which is also the **same commit that added `alembic.ini` to the repository** — i.e., the commit that first introduced Alembic at all. `git show c0706cb -- apps/api/app/persistence/models.py` shows that commit's `models.py` already defines `scoring_profiles` as a table (`__tablename__ = "scoring_profiles"`) alongside eleven others (`organizations`, `product_snapshots`, `analysis_runs`, `listing_analysis_results`, `ai_listing_results`, `image_intelligence_results`, `report_uploads`, `bulk_jobs`, `bulk_job_items`, `generated_reports`, `usage_events`).

`0001_m10_persistence.py`'s `upgrade()` is exactly:

```python
def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    ...  # bootstrap the default Organization row
```

Because `Base.metadata` already included `scoring_profiles` in the very commit that wrote this, a genuine `alembic upgrade head` from an empty database has **never once been capable of succeeding** in this repository's history — `0001` creates `scoring_profiles` via `create_all`, then `0002_scoring_profiles.py`'s `op.create_table("scoring_profiles", ...)` fails with `table scoring_profiles already exists`. This is pinned by `apps/api/tests/test_amazon_seller_identity_schema.py::test_full_migration_chain_from_empty_database_currently_fails_at_0002` (added in 12B.2A), which asserts this exact failure.

**Corollary, and this is the important part:** no database in this project's history can have obtained its schema via a genuine `alembic upgrade head` run. Every database that currently has this schema — including the configured development database — was necessarily bootstrapped some other way (almost certainly `Base.metadata.create_all()` run directly, followed by `alembic stamp head` to mark it as already migrated) and has only ever received *incremental* single-revision upgrades since (`0003` onward, each of which only had to coexist with whatever schema already existed, never re-triggering `0001`'s blind `create_all`).

This was verified, not assumed: I re-ran `alembic current` against the configured database (read-only) and it correctly reports `0008_amazon_oauth_states` — a stamped position, consistent with the bootstrap-then-stamp theory, not a database that has ever executed `0001`'s `create_all()` for real since `0002` was written.

### Additional risk surfaced during this investigation (not previously known)

The existing pytest suite **never exercises the real migration files at all** for anything before `0009`. `Base.metadata.create_all()` (via `app.persistence.database.get_engine()`) builds every test database directly from the current ORM models — using `app.persistence.types.Guid` (a dialect-aware `TypeDecorator`: native `UUID` on PostgreSQL, `CHAR(36)` on SQLite). The migration files themselves (`0001`–`0009`) do **not** use `Guid`; they import `sqlalchemy.dialects.postgresql.UUID` (`PGUUID`) directly, and `0002` also uses `sqlalchemy.dialects.postgresql.JSONB` directly for two `op.add_column` calls. This means:

- There is currently **no automated check that the migration files and the ORM models describe the same schema**. They could silently drift and nothing in CI (there is no CI — see §7) or pytest would notice, because pytest never runs the migrations.
- `PGUUID(as_uuid=True)` happened to compile successfully against SQLite in my isolated 12B.2A migration test (SQLAlchemy degrades it gracefully for DDL purposes), but this was not verified for `JSONB` — `0002`'s `op.add_column(..., JSONB())` has never been exercised against SQLite at all (it's on the far side of the very failure this document is about), so whether a genuine zero-to-head run would hit a **second**, unrelated SQLite-incompatibility after the `0001`/`0002` collision is fixed is presently unknown. This is called out explicitly in the recommendation (§9) as something the fix must verify, not assume.
- SQLite does not enforce `VARCHAR(n)` length limits at the database level (dynamic typing); PostgreSQL does. Any column-length invariant relying on the database itself (rather than only application-level validation, e.g. `normalize_selling_partner_id`'s 64-character cap) is silently unverified by the SQLite-only test suite today.

### Confirmed: no CI exists

There is no `.github/workflows/`, no CI config of any kind, and no deployment configuration (`Procfile`, `docker-compose`, `render.yaml`, etc.) anywhere in this repository. "How should CI test both paths" (§7 below) is therefore a **greenfield recommendation**, not a modification of an existing pipeline.

---

## 2. Which tables migration `0001` historically intended to create

There is no fixed "historical" list recoverable from `0001` itself — it has never had one; it has always deferred entirely to whatever `Base.metadata` contains at the moment it runs. Reasoning from the commit that introduced it (§1), the apparent *intent* was: "`0001` bootstraps the full M10 schema as it existed at the time, and `0002` is a separate, additive migration for the scoring-profiles feature that shipped in the same PR." The bug is that this intent was never actually implemented — `0001`'s `create_all()` was never scoped to exclude `scoring_profiles` (or anything added afterward), so it has always silently included whatever `0002` (and every migration since) was also trying to create.

---

## 3. Would changing `0001` be safe for databases that already applied it?

**Yes, with a caveat.** Alembic tracks the single currently-applied revision in an `alembic_version` table; it does not re-run `0001`'s `upgrade()` for a database already stamped past it. Editing `0001`'s `upgrade()` body has zero effect on any database whose `alembic_version` is already `>= 0001`, including the configured development database (currently `0008`).

**The caveat:** this is only safe if no environment anywhere would ever run `alembic upgrade` *starting from* a truly empty database and expecting `0001`'s current (buggy) behavior. Given §1's finding that this has apparently never successfully happened even once, that risk appears low — but it cannot be verified with certainty from inside this repository alone. This is flagged in §14 as a product-owner confirmation to obtain before touching `0001`, not something to assume.

Editing a historical migration also conflicts with this project's general migration-immutability norm (`CLAUDE.md`: "Do not invent migrations"). Even where technically safe, it is the higher-risk option compared to the alternative below, and is not the recommended path.

---

## 4. Baseline/bootstrap strategy vs. editing `0001`

**A baseline/bootstrap strategy is preferable**, for reasons independent of the safety analysis in §3:

- It requires touching zero historical files, fully preserving migration immutability.
- It is the standard, widely-used pattern for exactly this situation (a schema whose early migration history was never clean) — Django, Rails, and Alembic itself document variations of "squash/baseline" for this.
- It gives a single, auditable, forward-only starting point for anything that needs a truly fresh database (CI, new environments, disaster recovery), without asking anyone to reason about `0001`'s historical bug ever again.

### Recommended design: add a baseline migration, not a repair migration

1. A new migration, `0010_baseline_schema` (or similarly named — exact numbering depends on whatever is current when implemented), whose `upgrade()` does nothing destructive: it can either be a genuine no-op (`pass`) if paired with a documented bootstrap procedure (see §5), or — more robustly — it can perform an idempotent `create_all()` guarded to only create tables that do not yet exist (SQLAlchemy's `create_all()` is `checkfirst=True` by default), which actually gives fresh installs a working `alembic upgrade head` path without needing a separate stamp step. This second variant is the one recommended below (§5), because it removes the "developer must remember an extra command" failure mode entirely.
2. `0001`'s `upgrade()` is left untouched (still buggy for a genuine empty-database run) — but no supported install path is documented as depending on it doing so correctly ever again. `04_DATABASE_AND_MIGRATIONS.md` already documents this; the new baseline migration supersedes it as the real fresh-install path.
3. `0001`'s `downgrade()` (`Base.metadata.drop_all(bind)`) also remains as-is — untouched, and not part of the supported downgrade path for the same reason.

---

## 5. Fresh-database strategy

Recommended: **`0010_baseline_schema` performs a checkfirst `Base.metadata.create_all(bind)` for every table not yet present, then continues the chain normally.**

```text
empty database
  → alembic upgrade head
    → 0001 (existing, buggy create_all — still runs, still creates most tables, including scoring_profiles)
    → 0002 (still fails today: "scoring_profiles already exists")
```

Wait — this ordering shows the new baseline migration does not, by itself, fix a genuinely *empty* database, because `0001` still runs *before* `0010` in revision order and still fails at `0002` on the way there. A baseline migration inserted at the *end* of the chain cannot repair a chain that already breaks in the middle. This is a real constraint the design must respect, and it changes the recommendation:

**Corrected recommendation:** the safe fresh-install path cannot be "run every revision file in order starting from `0001`" — that path is unfixable without touching `0001` itself (ruled out in §3–4) or making `0001`/`0002` mutually tolerant of the collision (a targeted, minimal, *additive* change described below). Two remaining options:

- **Option A — Make `0001` and `0002` collision-tolerant without rewriting history.** Wrap `0001`'s `create_all(bind)` call with a table-existence guard that explicitly excludes tables owned by later migrations that also `create_all`- or `create_table`-style bootstrap overlapping tables (concretely: nothing needs to change in `0001` *conceptually* — `Base.metadata.create_all()` already skips existing tables by default; the actual fix is that **`0002` must not use `op.create_table` for a table `0001` may have already created**. The safe fix is on the `0002` side: change `0002`'s `upgrade()` to check `if not inspector.has_table("scoring_profiles")` before calling `op.create_table`, or simply let `0001`'s `create_all()` be the sole creator of `scoring_profiles` and have `0002` only perform its two genuinely-new `op.add_column` calls plus the index. This *is* an edit to a historical migration (`0002`), but it is additive/defensive (an existence check), not a rewrite of what `0002` creates — a materially smaller, safer class of change than rewriting `0001`. It requires the same product-owner confirmation as §3 before being executed, and is out of scope to implement in this review.
- **Option B — New environments never run `0001`–`0002` for real; they start from a stamp.** Ship a documented, scripted bootstrap procedure: `Base.metadata.create_all(engine)` (creates the *complete current* schema in one step, using live models — this is exactly what `0001` already does today, just invoked directly instead of through Alembic) followed immediately by `alembic stamp head`. This requires no code change to any migration file at all. Its downside (noted in the original review prompt as something to avoid) is that it does still depend on `Base.metadata.create_all()` — but only as an explicit, scripted, one-time bootstrap action operators run once per new environment, never as something a historical migration silently does on your behalf during an ordinary `alembic upgrade`. This is a meaningfully different risk profile from today's situation, where the collision is invisible until someone tries the "wrong" (but reasonable-sounding) command.

**Recommendation: implement both, in this order of preference:** Option B first, immediately, because it requires no historical-migration edit and it is exactly the pattern that has (informally) already been keeping every real database in this project working; then propose Option A as a follow-up decision for the product owner, since it is the only way to make literal `alembic upgrade head` from empty actually succeed without an operator needing to know about a special bootstrap script at all. Ship the bootstrap script and its documentation regardless of whether Option A is later approved.

---

## 6. Existing-database upgrade strategy (`0008 → 0009`)

No change needed to the recommended design here. `0008 → 0009` is a single, ordinary incremental step; it does not touch `0001`/`0002` at all and is already proven safe in isolation by `test_migration_0009_upgrades_and_downgrades_in_isolation_from_0008`. The configured development database should apply it the normal way — `alembic upgrade head` — whenever the product owner authorizes applying 12B.2A's schema there. That authorization is separate from this review and was explicitly not requested or given here; **`0009` has not been, and was not, applied to the configured database during this investigation.**

---

## 7. CI test strategy (new — none exists today)

Recommended two-job GitHub Actions workflow (or equivalent), added as a new file — this repository has no existing CI to extend:

1. **Fresh-install job.** Launch a disposable PostgreSQL service container (GitHub Actions' built-in `services: postgres:` — ephemeral per workflow run, never persisted, never the configured development database). Run the bootstrap procedure from §5 (`create_all` + `stamp head`, or, if Option A is later approved, plain `alembic upgrade head`) against it and assert `alembic current` reports the expected head. This is the test that would have caught the `0001`/`0002` collision on day one.
2. **Existing-database upgrade job.** Against a second disposable Postgres instance, apply migrations up through `0008` (or whatever the previous release's head was), then run `alembic upgrade head` and assert success — proving the incremental-upgrade path independently of the fresh-install path.
3. **Python test suite job** (already exists as a local command, not yet as CI): `cd apps/api && uv run pytest`, unchanged.

Both new database jobs use a `DATABASE_URL` pointing at the ephemeral CI-only Postgres service — never the developer's configured `.env` value, which does not exist in the CI environment at all. This structurally makes "touch the configured database" impossible in CI by construction, which is the same safety property this investigation was required to preserve locally (see §10).

---

## 8. Downgrade behavior

Recommended: **forward-only, documented explicitly**, matching how `0001` already behaves in practice (its `downgrade()` calls `drop_all`, which is destructive and is not something any real rollback procedure should use). Concretely:

- The new baseline/bootstrap path (§5) does not need its own `downgrade()` beyond what already exists — it either creates nothing new (Option B, a script, not a migration) or (Option A) only adds a defensive existence check with no schema-shape change, so `0002`'s existing `downgrade()` remains valid as-is.
- Document plainly in `04_DATABASE_AND_MIGRATIONS.md` (already partially done) that "downgrade to before `0002`" is not a supported operation for any database that used the bootstrap path, since there is no clean single-step undo for a `create_all()`-based bootstrap. This is consistent with standard practice: baseline points are forward-only by convention.

---

## 9. Does SQLite hide PostgreSQL-specific schema behavior?

**Yes, in at least three concrete, verified ways**, beyond the general caution already known:

1. **Schema-drift blindness** (§1): SQLite-based pytest never runs the actual migration files, so any divergence between `models.py` and `migrations/versions/*.py` is invisible to the test suite today.
2. **Untested Postgres-only types on the migration path**: `0002`'s direct use of `postgresql.JSONB` for `op.add_column` has never been exercised against SQLite (it sits past the very failure point this review is about) — its SQLite compatibility is unverified, not confirmed safe.
3. **Constraint enforcement gaps**: SQLite does not enforce `VARCHAR(n)` length at the database layer (dynamic typing); PostgreSQL does. Any invariant assumed to be DB-enforced by a `String(n)` column (e.g., `selling_partner_id: String(64)`) is only actually enforced by PostgreSQL, and only ever *application*-enforced (by `normalize_selling_partner_id`) when tests run against SQLite.

None of these are things 12B.2A's SQLite-based unit tests could have caught, and CI (§7) is the correct place to close that gap — not by abandoning SQLite for fast unit tests, but by adding the Postgres-backed jobs that check the things SQLite structurally cannot.

---

## 10. How to run disposable PostgreSQL validation safely, without touching the configured database

This investigation could not run **any** live Postgres validation itself: there is no Docker and no local PostgreSQL binary available in this sandboxed environment (verified: `which docker`, `which postgres`, `which pg_ctl` all returned nothing; `brew services list` shows no Postgres). This matches the finding already disclosed in the 12B.2A concurrency remediation report. The recommendation below is therefore a **design for someone with Docker/CI access to execute**, not something already run.

Recommended safety pattern, applicable both locally and in CI:

1. **Never reuse the developer's `.env` `DATABASE_URL` for any disposable-database action.** `migrations/env.py` reads `DATABASE_URL` unconditionally; any script or test that wants an isolated Postgres must explicitly override the `DATABASE_URL` environment variable (and clear `app.core.config.get_settings`'s `lru_cache`) for the duration of that specific action, then restore it in a `finally` block — exactly the pattern already used by `test_amazon_seller_identity_schema.py`'s isolated migration tests, generalized to Postgres.
2. **Locally:** `docker run --rm -e POSTGRES_PASSWORD=test -p 5433:5432 postgres:16` (a throwaway container, non-default port to avoid colliding with any existing local Postgres), with a helper script (e.g. `scripts/disposable_postgres_check.sh`, not yet written) that exports a `DATABASE_URL` pointing at `localhost:5433` before invoking `alembic upgrade head` or the concurrency test suite, and tears the container down afterward regardless of outcome.
3. **In CI:** the GitHub Actions `services:` block for Postgres (§7) is inherently disposable — it exists only for the lifetime of the job and is never reachable outside it, so there is no risk of an operator accidentally pointing it at a real environment.
4. **Guard rail:** any new disposable-Postgres test or script should refuse to run unless a specific, unambiguous environment variable (e.g. `ASI_ALLOW_DISPOSABLE_POSTGRES=1`) is set, so it can never fire accidentally from a developer's normal `pytest` invocation and silently pick up their real `.env` value if the override logic has a bug.

---

## 11. Validating the atomic seller-identity claim under real PostgreSQL concurrency

The existing deterministic concurrency tests (`tests/test_amazon_connection_claim_concurrency.py`, from 12B.2A) already avoid the shared-`StaticPool`-connection trap (documented in that file's own docstring) by building dedicated, file-based SQLite engines per test with real per-thread connections. The **design** of those tests already generalizes to PostgreSQL with minimal change: swap `create_engine(f"sqlite:///{path}", connect_args={"timeout": ...})` for `create_engine(disposable_postgres_url)`, using the same `Organization`/`AmazonConnection` seeding and the same `threading.Barrier`-synchronized claim logic.

Recommended approach: add a **separate, explicitly-opt-in** test module (e.g. `tests/postgres/test_claim_concurrency_postgres.py`) that:

- Is skipped by default (`pytest.mark.skipif` or a conftest collection guard) unless `ASI_ALLOW_DISPOSABLE_POSTGRES=1` and a `POSTGRES_CONCURRENCY_TEST_URL` environment variable are both set, so it never runs as part of the normal local or CI-unit-test command and can never touch the configured database.
- Reuses the exact same four scenarios already proven on SQLite (empty-connection race, claimed-connection race, same-seller race, cross-org isolation), asserting the identical invariant: exactly one winner for incompatible identifiers, both succeed for the same identifier, cross-org claims never interfere.
- Runs in CI (§7) as a third job, wired to the same disposable Postgres service container used for the migration jobs.

This closes the disclosed gap from the 12B.2A concurrency report ("PostgreSQL-specific concurrency validation has not yet been run") with a concrete, safe design — implementation is a 12B.2A.2-or-later slice, not this review.

---

## 12. Backup, rollback, and verification steps before applying `0009` anywhere important

Before `0009` (or any future migration) is applied to a database anyone depends on:

1. **Backup**: a verified, restorable snapshot (e.g. Supabase's point-in-time recovery or an explicit `pg_dump`) taken immediately before the upgrade, with the restore path itself tested at least once against a disposable copy — a backup that has never been restored is not a verified backup.
2. **Dry run**: apply the migration to a disposable Postgres instance seeded with a *copy* of production-shaped data (or at minimum the current schema) first, exactly as designed in §10–11, and confirm `alembic current` and the expected table/constraint set match before touching anything real.
3. **Row-count and constraint verification**: before/after row counts on `amazon_connections` and `amazon_oauth_states` (0009 adds no columns to either — only new, empty tables — so these counts must be identical after upgrade), plus confirmation the three new tables exist and are empty.
4. **Rollback plan**: `alembic downgrade 0008_amazon_oauth_states` is already proven safe in isolation (12B.2A's own test suite) and drops only the three new, still-empty tables — genuinely low-risk as rollbacks go, since nothing has written to them yet. Document this explicitly so an operator does not treat it as a risky operation out of general caution.
5. **Canary order**: any shared/staging environment before the configured development database's own promotion, if one exists; if not, the configured development database itself is the appropriate first real target, specifically because 12B.2A's schema has zero write-path wiring yet (nothing populates the new tables), making this migration unusually low-consequence to apply for real, once approved.

---

## 13. Summary of files that would need to change (not touched in this review)

| File | Change | Slice |
| --- | --- | --- |
| `apps/api/migrations/versions/0002_scoring_profiles.py` | Optional (Option A): guard `op.create_table("scoring_profiles", ...)` with an existence check | Needs explicit approval — edits a historical migration |
| New: bootstrap script (e.g. `apps/api/scripts/bootstrap_fresh_database.py`) | `create_all()` + `stamp head` for fresh installs (Option B) | Recommended first step |
| New: `.github/workflows/backend-ci.yml` (or similar) | Two/three-job CI: fresh-install, existing-upgrade, Postgres concurrency | Recommended, greenfield |
| New: `apps/api/scripts/disposable_postgres_check.sh` (or similar) | Local disposable-Postgres helper, env-var gated | Recommended |
| New: `tests/postgres/test_claim_concurrency_postgres.py` | Opt-in Postgres concurrency test, mirrors the existing SQLite suite | Closes the disclosed 12B.2A gap |
| `docs/AI_HANDOVER/04_DATABASE_AND_MIGRATIONS.md` | Document the bootstrap procedure as the supported fresh-install path; keep the known-limitation note until Option A (if approved) actually ships | Documentation |
| `docs/AI_HANDOVER/14_LOCAL_DEVELOPMENT_SETUP.md` | Point to the bootstrap script instead of implying `alembic upgrade head` works from empty | Documentation |

No application code (`app/amazon/`, `app/persistence/repositories.py`, etc.) needs to change for any of this.

---

## 14. Decisions requiring product-owner approval before implementation

1. **Approve Option B (bootstrap script) as the supported fresh-install path.** Low risk, no historical-migration edit — recommended default if only one option is approved now.
2. **Approve or defer Option A** (adding an existence guard to `0002`, a historical migration) so literal `alembic upgrade head` from empty eventually works without a separate bootstrap step. Requires confirming no environment anywhere has ever depended on `0002` unconditionally creating `scoring_profiles` in a way an existence guard would break (analysis in §3 suggests this is safe, but the product owner should confirm no unknown environment's history contradicts that).
3. **Approve adding CI to this repository** — none exists today; this review recommends adding it as part of this hardening work, which is itself a scope decision (new infrastructure, not a pure bugfix).
4. **Approve the disposable-Postgres test/script pattern** (env-var-gated, Docker-based) before anyone with Docker access implements and runs it — in particular, confirm CI is permitted to spin up ephemeral Postgres service containers (typically no additional cost on GitHub Actions but worth an explicit yes).
5. **Confirm the backup/canary sequencing in §12** before `0009` is ever applied anywhere real, and confirm who owns taking that backup.

---

## 15. Risks if this is not done before real SP-API ingestion begins

- A fresh production deployment (or disaster-recovery restore) attempting a plain `alembic upgrade head` will fail partway through, with no automated warning beforehand, because nothing currently tests that path.
- Schema drift between `models.py` and the migration files could go undetected indefinitely, since the test suite never runs the real migrations.
- The seller-identity concurrency invariant (12B.2A) is proven correct in design and under SQLite, but has not been proven under PostgreSQL's actual concurrency behavior — the specific database every real deployment will use.

None of these block the 12B.2A development checkpoint already committed. They are release/deployment blockers for 12B.3-and-beyond, which will be the first slice to depend on this schema being reliably deployable.

---

```text
12B.2A.1 ARCHITECTURE READY — AWAITING APPROVAL
```
