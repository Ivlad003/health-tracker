# PR #9 Apple Health Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan task-by-task.

**Goal:** Make PR #9 safe to merge by enforcing causally ordered, per-metric Apple Health snapshots; preserving legacy data during rollout; removing raw-payload forwarding; and requiring live PostgreSQL verification.

**Architecture:** Schema v3 stores one processed row per user, collector, local date, and metric family in a new additive table. Ingestion validates explicit coverage, aggregates in memory, and freshness-upserts every row in one transaction. Readers overlay v3 rows over legacy daily rows and raw rows per date/family during the expand-migrate-contract transition. Backfill is nondestructive by default; deletion is an explicit selected-ID purge.

**Tech Stack:** Python 3.12, FastAPI, asyncpg, PostgreSQL 15, pytest/pytest-asyncio, Apple Shortcuts plist and `shortcuts sign`.

## Global Constraints

- Never persist or forward raw HealthKit request bodies; only processed daily family aggregates and sanitized summaries may leave request memory.
- A snapshot may clear a metric only when that metric family and date are explicitly covered.
- Older snapshots must never overwrite newer rows; equal timestamp plus different processed content is a conflict, not a replacement.
- Native Shortcut and Health Auto Export data must not be summed when they describe the same family/date; readers select the freshest collector row.
- All writes for one request, including success bookkeeping and its import log, commit or roll back together.
- Legacy raw rows remain readable and retained until an explicitly requested purge succeeds and verifies exact selected IDs.
- Documentation changes are mirrored in `docs/en/` and `docs/uk/`.
- No new runtime dependency is introduced.

---

## Task 1: Lock the corrected protocol and aggregation behavior with failing tests

**Files:**

- Modify: `tests/test_apple_health.py`
- Modify: `tests/test_apple_health_db.py`
- Modify: `tests/test_apple_health_stats.py`
- Modify: `tests/test_apple_health_shortcut_artifact.py`
- Add: `tests/test_backfill_apple_health.py`

- [ ] Add schema-v3 validation tests for required `collector`, offset-aware `generatedAt`, and coverage by metric family/date.
- [ ] Add controlled-offset tests for `+24:00`, `+03:60`, valid `+14:00`, `-12:00`, `+05:45`, and `Europe/Kyiv`.
- [ ] Add sleep-stage tests: staged sleep excludes Awake/In Bed, In Bed minus Awake is a fallback only when no asleep stages exist, Awake-only is zero, and overlapping asleep stages are unioned.
- [ ] Add real-PostgreSQL tests for stale/equal/newer snapshots, partial family coverage, native/HAE collision, equal-time conflict, and transaction rollback after a later-row constraint failure.
- [ ] Add reader overlay tests proving v3 wins per family, legacy daily rows fill missing families, raw-only windows remain visible, and zero aggregates remain authoritative.
- [ ] Add backfill tests for Kyiv attribution, nondestructive default, selected-ID purge, concurrent late-row preservation, residual-row failure, and nonzero CLI exit.
- [ ] Update Shortcut structural tests to require schema v3, `collector=shortcut`, and the four explicitly covered families.
- [ ] Run targeted tests and confirm they fail for the expected missing behavior before implementation.

## Task 2: Implement schema-v3 ingestion, freshness, sleep, atomicity, and privacy

**Files:**

- Add: `database/migrations/010_health_daily_metric_aggregates.sql`
- Add: `database/migrations/010_health_daily_metric_aggregates_rollback.sql`
- Modify: `app/db_preflight.py`
- Modify: `app/services/apple_health.py`
- Modify: `app/routers/apple_health.py`
- Modify: `app/services/telegram_bot.py`

- [ ] Add `health_daily_metric_aggregates` keyed by `(user_id, source, collector, metric_date, metric_family)` with non-null freshness time and processed-content hash.
- [ ] Migrate existing daily aggregates additively into legacy collector family rows without deleting the old table.
- [ ] Parse schema-v3 coverage into a family-to-date map; reject missing, naive, future, malformed, or inconsistent metadata.
- [ ] Preserve each metric's encoded local attribution date so HAE samples crossing a DST boundary are not bucketed using the first sample's offset.
- [ ] Aggregate steps, active energy, heart rate, HRV, and sleep independently; implement the stage-aware sleep rules.
- [ ] Upsert only newer family rows, report identical replay as a no-op, report older rows as stale, and reject equal-time/different-content conflicts.
- [ ] Acquire one connection and transaction for all family rows, sync success bookkeeping, and success log insertion.
- [ ] Raise `AppleHealthPersistenceError` for database failures and map it to a sanitized HTTP 500.
- [ ] Remove raw Telegram document forwarding and the now-unused `send_document` helper.
- [ ] Run targeted unit and PostgreSQL tests until green.

## Task 3: Implement the safe expand-migrate-contract reader and backfill

**Files:**

- Modify: `app/services/apple_health.py`
- Modify: `app/backfill_apple_health.py`
- Modify: `database/migrations/009_health_daily_aggregates_rollback.sql`
- Modify: `tests/test_apple_health_stats.py`
- Modify: `tests/test_apple_health_db.py`
- Modify: `tests/test_backfill_apple_health.py`

- [ ] Read the freshest v3 collector row per date/family, then fill only missing authorities from legacy daily rows, then from raw rows.
- [ ] Default legacy raw attribution to `Europe/Kyiv` and keep explicit timezone override support.
- [ ] Write backfilled family rows under collector `legacy_backfill` with freshness derived from selected historical samples.
- [ ] Keep raw rows by default; add `--delete-raw` for the destructive phase.
- [ ] Select raw IDs with `FOR UPDATE`, delete exactly those IDs, compare returned and selected ID sets, and fail if residual rows remain.
- [ ] Raise an explicit backfill error on any per-user failure or incomplete purge, while always closing the connection.
- [ ] Make rollback refuse to drop a nonempty processed aggregate table.
- [ ] Run the reader/backfill unit and live-PostgreSQL tests until green.

## Task 4: Replace sensitive test data, add CI, update docs, and re-sign

**Files:**

- Modify: `tests/fixtures/apple-health-payload.json`
- Modify: `tests/test_apple_health.py`
- Modify: `tests/test_apple_health_db.py`
- Add: `.github/workflows/apple-health-postgres.yml`
- Modify: `docs/shortcuts/apple-health-sync.shortcut.plist`
- Modify: `docs/shortcuts/apple-health-sync.shortcut`
- Modify: `docs/en/api-integration.md`
- Modify: `docs/uk/api-integration.md`
- Modify: `docs/en/architecture.md`
- Modify: `docs/uk/architecture.md`
- Modify: `docs/en/session-knowledge.md`
- Modify: `docs/uk/session-knowledge.md`

- [ ] Replace the production-like export with a compact fixture explicitly marked synthetic and derive expectations from its contents.
- [ ] Add a GitHub Actions PostgreSQL 15 service job that exports `APPLE_HEALTH_TEST_DATABASE_URL` and runs the complete suite.
- [ ] Update the Shortcut payload to schema v3 with collector and covered-family metadata.
- [ ] Lint the source plist, sign with `shortcuts sign --mode anyone`, decrypt/read back the signed workflow, and verify action/import-question parity.
- [ ] Document the protocol, zero-raw Telegram behavior, transition reader, nondestructive backfill, explicit purge, and rollback limits in both languages.

## Task 5: Final verification and delivery

**Files:** all changed files

- [ ] Run `git diff --check` and inspect the complete staged diff for secrets and unrelated files.
- [ ] Run the full suite with `APPLE_HEALTH_TEST_DATABASE_URL` against disposable PostgreSQL 15.
- [ ] Run plist lint, signed-artifact decrypt/readback, and SHA checks.
- [ ] Request an independent whole-branch code/architecture review and resolve all critical or important findings.
- [ ] Commit with Conventional Commit plus Lore trailers and push to the PR head branch.
- [ ] Do not rewrite remote history without explicit approval; report that removing the old sensitive blob from reachable PR commits requires a force-push-with-lease and possible GitHub cache purge.
