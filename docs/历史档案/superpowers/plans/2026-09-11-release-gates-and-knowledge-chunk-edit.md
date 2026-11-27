# Release Gates and Knowledge Chunk Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the remaining code-level governance gap and make the real-model and production acceptance gates executable and fail-closed without claiming external environments are verified locally.

**Architecture:** Add a UUID-scoped chunk patch operation to the existing knowledge governance service and expose it through the authenticated API and progressive UI. Add one release acceptance command that validates the deployed migration head, Qdrant ownership, backup verification/restoration, and protected real-model evaluation only when an operator supplies the target environment. Keep all production mutations explicit and require a target marker so local development data cannot be mistaken for production.

**Tech Stack:** FastAPI, Pydantic, PostgreSQL/Alembic, Qdrant HTTP API, Python subprocess/argparse, React 19, TypeScript, Vitest, Testing Library, PowerShell-compatible operator commands, GitHub Actions.

**Spec:** `docs/superpowers/plans/2026-09-10-phase7-knowledge-intelligence-quality.md`, `docs/superpowers/plans/2026-09-10-governance-panel-ui.md`

## Global Constraints

- Preserve legacy upload/list/delete behavior; governance edits apply only to UUID-backed governed documents.
- A chunk edit invalidates the active index and must set the containing version to `draft` with `index_status='pending'`; the active document pointer is cleared until the user explicitly reindexes the version.
- Enforce document owner or administrator authorization at the service boundary; public readability never grants mutation rights.
- Validate chunk text at 50,000 characters and page as null or a positive integer; use parameterized SQL and never log user content or secrets.
- Real-model and production gates must fail closed when required credentials, target markers, or artifacts are absent; local rehearsal output must remain clearly labeled as non-production.
- Do not change or delete existing production data during local verification; restore drills use temporary database/collection names only.

---

### Task 1: Add governed single-chunk edit API

**Files:**
- Modify: `careercrew_core/knowledge/governance.py`
- Modify: `careercrew_api/routers/knowledge_governance.py`
- Test: `tests/unit/test_knowledge_governance.py`
- Test: `tests/api/test_knowledge_governance_api.py`

**Interfaces:**
- `KnowledgeGovernance.update_chunk(owner_id, document_id, version_id, chunk_id, *, text, page, is_admin=False) -> dict`.
- `PATCH /api/knowledge/governance/documents/{document_id}/versions/{version_id}/chunks/{chunk_id}` receives `{ "text": string, "page": number | null }` and returns the refreshed document detail.
- Only the owner or an administrator may mutate a chunk; mismatched document/version/chunk identifiers return the existing 404 boundary.

- [x] **Step 1: Write failing fake-db tests** for owner success, public-reader denial, mismatched version denial, text/page validation, active-version invalidation, and edit-after-reindex state.
- [x] **Step 2: Run the focused unit file and confirm the new method is absent or failing.**

Run: `F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_knowledge_governance.py -q`

Expected: the new chunk-edit assertions fail before implementation.

- [x] **Step 3: Implement the fake-db mutation** using the existing `_governable_document`, `_validate_chunks`, and `_detail_from_fake` helpers; set the version to `draft`, clear `indexed_at`, set the edited chunk to `pending`, and clear the active document pointer until explicit reindex.
- [x] **Step 4: Add the parameterized PostgreSQL mutation** with `UPDATE ... WHERE id=%s AND version_id=%s`, update `text_hash`, set `index_status='pending'`, set version `status='draft'` and `indexed_at=NULL`, and return `_get_detail_pg`.
- [x] **Step 5: Add the Pydantic request model and authenticated route** and map governance errors to the existing 403/404/422 response contract.
- [x] **Step 6: Run unit and API governance tests** and confirm all pass.

Run: `F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_knowledge_governance.py tests/api/test_knowledge_governance_api.py -q`

### Task 2: Expose chunk editing in the governance panel

**Files:**
- Modify: `careercrew_web/src/components/KnowledgePanel.tsx`
- Modify: `careercrew_web/src/components/KnowledgePanel.test.tsx`

**Interfaces:**
- The existing chunk preview gains an edit toggle, textarea, page input, save, and cancel controls.
- Save calls the new PATCH endpoint through `apiFetch`; success refreshes governance data, while 409/4xx/5xx remains an actionable inline error.
- Reindex remains a separate explicit action after editing.

- [x] **Step 1: Write failing Vitest tests** for opening chunk edit, saving text/page, canceling without a request, displaying a save error, and showing pending status before reindex.
- [x] **Step 2: Run the focused test file and confirm the new assertions fail.**
- [x] **Step 3: Implement the smallest accessible controls** with native inputs, 44px touch targets, disabled busy states, and the existing error helpers.
- [x] **Step 4: Run the focused file and the full frontend suite.**

Run: `npm run test -- --run`

### Task 3: Add fail-closed production acceptance orchestration

**Files:**
- Create: `scripts/release_acceptance.py`
- Create: `tests/unit/test_release_acceptance.py`
- Modify: `docs/OPS_BACKUP.md`
- Create: `docs/OPS_RELEASE_ACCEPTANCE.md`
- Modify: `.github/workflows/ci.yml` only if the existing manual release job needs a documented invocation.

**Interfaces:**

> 以下为 2026-09-11 的原始接口约定；生产 target 与真实模型评测已于 2026-09-16
> 按决策移除，当前 `--help` 只包含本地预演参数（见文末《scope decision》）。

- `python scripts/release_acceptance.py --help` documents `--target production`, `--database-url`, `--qdrant-url`, `--backup-dir`, `--qdrant-container`, and `--run-real-eval`. （已废弃）
- `--target production` requires `CAREERCREW_RELEASE_TARGET=production` and refuses the protected database name when the operator has not explicitly supplied a production target marker. （已废弃）
- The command runs static migration validation, reads the live Alembic head, validates Qdrant health/required collections, runs ownership dry-run, verifies the supplied backup, runs an isolated restore drill, and optionally invokes `eval_runner.py --real --require-real --fail-on-regression`.
- A missing target, backup, Qdrant collection, or real-model credential returns nonzero and writes only redacted machine-readable evidence.

- [x] **Step 1: Write tests** for target-marker refusal, command ordering, redacted output, successful injected command adapters, and fail-closed missing backup/real-eval behavior.
- [x] **Step 2: Run the focused test file red.**
- [x] **Step 3: Implement command adapters and a JSON report** without embedding credentials in argv or reports; use existing `validate_migrations.py`, `backup_restore.py`, `verify_qdrant_ownership.py`, and `eval_runner.py` entry points.
- [x] **Step 4: Add operator documentation** distinguishing local Docker rehearsal from production acceptance and listing the exact evidence required for migration, reindex, backup media, restore, and protected real-model evaluation.
- [x] **Step 5: Run the unit tests and a local dry-run with injected adapters.**

### Task 4: Review and final verification

**Files:**
- Modify: this plan with checked steps and final evidence.
- Modify: phase 7/8 verification reports with the new chunk-edit and acceptance-command status.

- [x] **Step 1: Complete a targeted independent read-only review** for chunk authorization/invalidation, the release target guard, protected runtime evaluation, and secret handling; broad repository review remains separate.
- [ ] **Step 2: Run backend full tests, frontend test/lint/build, migration static validation, Docker local release rehearsal, and Qdrant ownership dry-run.**

  Evidence: frontend 51 files/214 tests, lint exit 0, build exit 0, and migration static validation passed. Backend reached 100% with one environment-only PostgreSQL failure (`tests/api/test_user_settings_api.py::test_apikey_settings_crud_lifecycle`) because `localhost:5432` was unavailable. Docker/Qdrant local release rehearsal and ownership dry-run were not rerun in this turn because Docker Desktop was unavailable; prior dry-run evidence remains historical and is not upgraded here.
- [x] **Step 3: Run the release acceptance command only against an explicitly configured target; absent production credentials/marker is recorded as fail-closed, never as pass.**
- [ ] **Step 4: Review `git diff --check`, changed-file secret scan, `git status --short`, and commit only the requested files.**

### 2026-09-14 continuation evidence

- Added regression coverage and fixes for cold vector initialization, duplicate
  uploads of draft/failed versions, whole-version invalidation after chunk edits,
  and relational visibility revocation on unpublish. Sequential reindex after
  unpublish retains private visibility.
- Protected evaluation and production evidence receipts now bind canonical
  database/Qdrant deployment endpoints as well as resource identities. The
  external authority must independently validate that mapping.
- Six focused suites completed successfully (98 tests): knowledge ownership,
  governance unit/API, upload API, evaluation runner, and release acceptance.
  Ruff on the eight touched implementation/test files passed; diff whitespace
  check passed. Background refresh-session cleanup still logged a PostgreSQL
  connection timeout after the focused suite; this is not live database proof.
- Docker remains unavailable (`dockerDesktopLinuxEngine` pipe missing).
  No protected real-model or production migration/recovery acceptance is claimed.
- Fresh independent follow-up review could not execute: selected model at
  capacity. Broad review remains unchecked. In particular, multi-worker
  reindex/unpublish concurrency and publish/unpublish symmetry still require
  follow-up review and regression coverage before release.
- Changes remain uncommitted; no production data was modified.

### 2026-09-16 continuation evidence

- Docker Desktop available again. Live local checks (all on
  `localhost`/`127.0.0.1`): isolated migration rehearsal passed four paths
  (empty → head, `0002` → head, failed-migration rollback/recovery, synthetic
  backup restore) at head `0018_eval_case_updated_at`; `validate_migrations
  --static` and the live schema invariant check both pass.
- Real backup/restore exercised against the Docker stack: a new opt-in
  integration test (`tests/integration/test_backup_restore_docker_live.py`)
  runs real `pg_dump`/`pg_restore` inside the PostgreSQL container against a
  disposable database and restores Qdrant snapshots into a disposable
  collection on the live Qdrant, verifying manifests and cleanup. The dev
  database itself was backed up through the same code path
  (`data/backups/careercrew-20260916-115813`: 5 artifacts, 90 archived files,
  Qdrant snapshots for all three collections) before being migrated.
- Migration `0018_eval_case_updated_at` added: `eval_cases.updated_at` existed
  only in the runtime lazy DDL while edit/approve wrote the column, so the live
  integration test failed with `UndefinedColumn`. `migrations/checksums.json`
  and `EXPECTED_HEAD` were updated; the dev database was migrated
  `0016 → 0018` and re-validated afterwards.
- Backend full suite now runs against a disposable PostgreSQL database and
  finishes green: 1370 tests, 0 failures, 0 errors, 1 skipped (the opt-in
  Docker backup drill). Frontend: 214 tests, lint, production build all pass.
- Release-security follow-ups from the independent review: protected evaluation
  now resolves runtime endpoints and refuses to start when resolved settings
  disagree with the attested deployment, and refuses live execution while
  worker-lifecycle isolation is unproven; `verify_qdrant_ownership` no longer
  creates snapshots in dry-run; release acceptance requires the ownership
  report to cover every required collection; `parse_database_url` rejects
  inherited libpq routing variables (`PGHOSTADDR` and friends); CI
  `cancel-in-progress` is disabled so a later run cannot kill a protected
  restore.
- Local release acceptance (`--target local`) now reports
  `migration_static`, `migration_live`, `qdrant_health` and `backup_verify` as
  passing. It still fails, and therefore does not run the restore drill, on the
  Qdrant ownership gate: `careercrew_episodic_v2` holds 4 points owned by
  `u_817769db5520494eab6d481f6af7ba8f`, an account that no longer exists in
  `auth_accounts` (4 matching `memory_records` rows). The tool refuses to
  overwrite them by design, so the gate reports `conflicts=4`. Removing or
  re-owning that leftover data is a product decision, not a code defect.
- Production/pilot acceptance is still open and cannot be produced locally:
  real production target marker, backup media evidence, reindex/cutover
  evidence and the external evidence verifier, plus the protected real-model
  evaluation tenant. No production credentials, media or model runs were used
  in this session.

### 2026-09-16 late: local acceptance green

- Local release acceptance now reports `rehearsal_passed` with every local check
  green, including the isolated restore drill (restored PostgreSQL verified
  against schema invariants and cross-table canaries, 92 archived files
  re-verified by SHA-256, temporary database cleaned):
  `data/reports/local-release-acceptance-20260916.json`.
- `pg_dump`/`pg_restore` fall back to the configured PostgreSQL container when
  the host has no client binaries, mirroring the existing Qdrant fallback; argv
  never carries a password. Unit-tested with injected runners.
- Account deletion now purges long-term memory rows plus episodic/knowledge/
  workspace vector copies, aborting the deletion on failure so it can be
  retried. Governance audit remains append-only: tenants with
  `memory_record_events` are soft-deleted instead of physically removed.
- Incident and recovery (local dev database): the auth API tests resolved the
  real global runtime, so the delete-user endpoint actually cleaned the
  developer's own account. 28 `memory_records` rows and 2 knowledge vectors were
  lost, then restored from the same-day backup (targeted row backfill plus
  Qdrant snapshot upload). The test module now injects a fake runtime
  (autouse), and a sentinel row plus Qdrant delete counts confirm the suite no
  longer touches real data. See `docs/PHASE6-8_COMPLETION_REPORT.md`.
- Backend full suite after the fixes: 1375 passed, 0 failed, 1 skipped, with the
  dev database unchanged afterwards (`memory_records=28`, `mm=2`,
  `episodic_v2=24`).

### 2026-09-16 scope decision: production acceptance and real-model eval removed

- The production acceptance path and the protected real-model evaluation were
  removed from the repository by decision: both require infrastructure this
  project does not have (a real production target, backup media and
  reindex/cutover evidence verified by a third-party authority; a protected
  eval tenant attestation plus a provable exclusive writer lease).
- Removed: `--target production`, `CAREERCREW_RELEASE_TARGET`,
  `CAREERCREW_RELEASE_EVIDENCE_VERIFIER_*`, backup-media/reindex evidence
  validation, `--run-real-eval`, the runtime evaluation session with tenant
  attestation and its fail-closed isolation error, `scripts/deployment_identity.py`,
  the `release-real-eval` and `production-release-acceptance` CI jobs, and the
  matching environment variables in `.env.example`.
- Kept and still verified: the loopback-only local rehearsal runner
  (`migration_static`, `migration_live`, `qdrant_health`, `qdrant_ownership`,
  `backup_verify`, `restore_drill`) and the offline evaluation gate
  (`eval_runner.py --offline`).
