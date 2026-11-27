# CareerCrew Phase 6 Stability Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a green backend baseline and add production-safe browser access, migration drift detection, immutable Qdrant supply-chain controls, and verifiable automated backups.

**Architecture:** Keep the existing FastAPI router and authentication dependency model. Add small, testable operational helpers: a static/online migration validator and a backup/restore command that writes a manifest for every artifact. CI invokes the static validator, online schema validator, and blocking dependency audit; runtime browser control is protected by both authentication and request-client loopback validation.

**Tech Stack:** Python 3.12, FastAPI, pytest, psycopg, Alembic, PostgreSQL 16, Qdrant v1.19.0, Docker Compose, GitHub Actions, PowerShell Task Scheduler.

**Spec:** `docs/superpowers/plans/2026-09-09-phase6-stability-governance-spec.md`

## Global Constraints

- Preserve the existing dirty working-tree changes; never use reset, checkout, clean, or broad file deletion.
- Preserve “AI 准备、人工确认、完整留痕”; do not add automatic application or HR contact behavior.
- Preserve all existing API paths and tenant isolation behavior.
- Browser control endpoints require a valid `CurrentUser` and a loopback `request.client.host`; do not trust `X-Forwarded-For` or arbitrary `Host` headers.
- Published migration files are immutable after the checksum manifest is committed; any intentional future migration must be a new revision and an explicit manifest update.
- Use `qdrant/qdrant:v1.19.0@sha256:057ee3a8da769fe7310dd3537b4dc7583bf87a95ce8ac43c0af5a46bc580d1fc` exactly.
- Backups default to a 30-day retention window and must never place a database password, share token, JD, resume text, or generated content in argv, logs, or telemetry.
- Use tests first for every production-code behavior change; do not push or merge.

---

### Task 1: Register and harden the local browser router

**Files:**
- Modify: `careercrew_api/routers/browser.py`
- Modify: `careercrew_api/main.py`
- Modify: `tests/unit/test_browser_router.py`

**Interfaces:**
- Consumes: existing `CurrentUser` dependency, `Request.client`, and CDP status/launch helpers.
- Produces: authenticated `GET /api/browser/cdp-status`, authenticated `POST /api/browser/launch-cdp`, and authenticated `GET /api/browser/cdp-command`; all three reject non-loopback clients before CDP work.

- [ ] **Step 1: Add the failing security tests.**

Add tests for a valid local authenticated client, missing bearer authentication, and a non-loopback client. Use `TestClient(..., client=("127.0.0.1", 50001))` for the success case and `TestClient(..., client=("192.0.2.10", 50001))` for the source-rejection case. Override only `get_current_user` in the success/rejection tests so the actual local-source dependency runs.

- [ ] **Step 2: Run the focused tests and observe the expected red state.**

Run:

```powershell
F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_browser_router.py -q
```

Expected: the existing endpoint tests fail with 404/405 because `main.create_app()` does not include the browser router; the new source/auth tests fail until the dependencies are wired.

- [ ] **Step 3: Implement the minimal router guard and registration.**

In `browser.py`, add a dependency that accepts only `request.client.host` values resolving to an IP loopback address (`127.0.0.0/8`, `::1`, or an IPv4-mapped loopback) and raises HTTP 403 otherwise. Add `CurrentUser` and `Depends(require_local_request)` to every browser endpoint. In `main.py`, import `browser` and include `browser.router` with prefix `/api` before the SPA fallback is defined.

- [ ] **Step 4: Run the focused tests and the route smoke check.**

Run:

```powershell
F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_browser_router.py -q
F:\Python_develop\miniconda3\envs\careercrew\python.exe -c "from careercrew_api.main import create_app; paths={(r.path, tuple(sorted(r.methods or []))) for r in create_app().routes}; assert ('/api/browser/cdp-status', ('GET',)) in paths; print('browser routes registered')"
```

Expected: focused tests pass and the route smoke check prints `browser routes registered`.

- [ ] **Step 5: Commit only the browser task files.**

```powershell
git add careercrew_api/routers/browser.py careercrew_api/main.py tests/unit/test_browser_router.py
git commit -m "fix: protect and register browser control routes"
```

### Task 2: Restore the eight-test backend baseline

**Files:**
- Modify: `careercrew_api/runtime/common.py`
- Modify: `careercrew_api/runtime/streaming.py`
- Modify: `careercrew_api/runtime/regenerate.py`
- Modify: `careercrew_ai/vector_store/qdrant_store.py`
- Modify: `tests/unit/test_config_loading.py`
- Test: `tests/unit/test_knowledge_sources.py`
- Test: `tests/unit/test_qdrant_store.py`

**Interfaces:**
- Consumes: the existing configuration fixture, `_cap_sources`, and `QdrantStore._filter_expr` contract.
- Produces: `qwen-plus`/`qwen-vl-max` fixture expectations, a default `min_score=0.1` source cap at both normal and regenerate call sites, and a pure access Qdrant filter with top-level `should` while combined filters keep access in nested `must`.

- [ ] **Step 1: Confirm the already-red regression tests and make the test-only expectation correction first.**

Change only the two stale assertions in `tests/unit/test_config_loading.py` to the values already used by `tests/conftest.py` and both checked-in settings files: `qwen-plus` for `settings.llm.model` and `qwen-vl-max` for `settings.vlm.model`. Run the three regression files before changing production code:

```powershell
F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_config_loading.py::test_load_settings_ok tests/unit/test_knowledge_sources.py tests/unit/test_qdrant_store.py::test_access_filter_alone_still_uses_should -q
```

Expected: configuration and existing implementation regressions remain red; the output identifies the source threshold and Qdrant filter failures.

- [ ] **Step 2: Restore the source threshold and Qdrant filter branch.**

Set `_cap_sources(..., min_score=0.1)` as the default and pass `min_score=0.1` from both streaming and regenerate paths. In `_filter_expr`, return `Filter(must=[], should=access_should)` when the only input is `__access_user`; only wrap the access conditions in a nested `Filter(..., min_should=MinShould(...))` when other `must` conditions exist.

- [ ] **Step 3: Run the red-to-green focused regressions.**

```powershell
F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_config_loading.py::test_load_settings_ok tests/unit/test_knowledge_sources.py tests/unit/test_qdrant_store.py -q
```

Expected: all selected tests pass, including the combined-filter tenant isolation assertions.

- [ ] **Step 4: Commit only the baseline-fix files.**

```powershell
git add careercrew_api/runtime/common.py careercrew_api/runtime/streaming.py careercrew_api/runtime/regenerate.py careercrew_ai/vector_store/qdrant_store.py tests/unit/test_config_loading.py
git commit -m "fix: restore backend regression baseline"
```

### Task 3: Add immutable migration and schema invariant checks

**Files:**
- Create: `scripts/validate_migrations.py`
- Create: `migrations/checksums.json`
- Create: `tests/unit/test_validate_migrations.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `Dockerfile`

**Interfaces:**
- Consumes: `migrations/versions`, `migrations/checksums.json`, and a PostgreSQL URL for the online check.
- Produces: `read_revision_metadata`, `validate_revision_graph`, `build_checksum_manifest`, `validate_checksum_manifest`, and `validate_schema_invariants`; CLI modes `--static` and `--database-url` with nonzero failure status.

- [ ] **Step 1: Add failing validator tests.**

Cover an annotated revision assignment (`revision: str = ...`), one linear head, duplicate/missing revisions, checksum mismatch/extra file, and schema rows that fail when the head/table/column/extension/index invariant is missing. Use a fake connection object for schema checks so the tests do not mutate PostgreSQL.

- [ ] **Step 2: Run the new tests and confirm they fail because the validator is absent.**

```powershell
F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_validate_migrations.py -q
```

Expected: collection/import fails with the missing `scripts.validate_migrations` module or missing helper names.

- [ ] **Step 3: Implement static revision/checksum validation.**

Parse migration metadata with `ast.literal_eval` without executing migration code. Ignore only `__init__.py`; require exactly one root and one head, every `down_revision` to resolve, and the expected head `0008_prod_hardening`. Hash every version `.py` file with SHA-256 and require exact filename/hash equality with `migrations/checksums.json`; do not provide an implicit update mode.

- [ ] **Step 4: Implement online schema invariants and the CLI.**

Use `psycopg.connect` with `DATABASE_URL` or `--database-url`, then assert `alembic_version=0008_prod_hardening`, the tables `career_generation_events`, `career_product_events`, `upload_tasks`, and `career_share_tokens`, the hash/audit columns (`token_hash`, `access_count`, `last_accessed_at`) and absence of the legacy `token` column, the `pg_trgm` extension, and all 14 `ix_p5_*` indexes. Print only compact failure details and return 1 on any mismatch.

- [ ] **Step 5: Generate the committed manifest and run both validator modes.**

```powershell
F:\Python_develop\miniconda3\envs\careercrew\python.exe -c "from pathlib import Path; from scripts.validate_migrations import build_checksum_manifest; import json; Path('migrations/checksums.json').write_text(json.dumps(build_checksum_manifest(Path('migrations/versions')), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')"
F:\Python_develop\miniconda3\envs\careercrew\python.exe scripts/validate_migrations.py --static
```

Expected: static tests and the static CLI pass. The manifest must be generated only after all migration source files are final.

- [ ] **Step 6: Wire CI and the image smoke check.**

Add a static validator step before unit tests. In the Docker smoke path, run `python scripts/validate_migrations.py --database-url "$DATABASE_URL"` immediately after `alembic upgrade head`; copy `migrations/checksums.json` into the runtime image with the existing `COPY migrations/ migrations/` instruction already present.

- [ ] **Step 7: Commit the migration guard task.**

```powershell
git add scripts/validate_migrations.py migrations/checksums.json tests/unit/test_validate_migrations.py .github/workflows/ci.yml Dockerfile
git commit -m "ci: guard migration files and schema invariants"
```

### Task 4: Add automated backups and a safe restore drill

**Files:**
- Create: `scripts/backup_restore.py`
- Create: `scripts/install_backup_schedule.ps1`
- Create: `tests/unit/test_backup_restore.py`
- Modify: `docs/OPS_BACKUP.md`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `DATABASE_URL`, `QDRANT_URL`, `BACKUP_ROOT`, `BACKUP_RETENTION_DAYS`, `data/uploads`, `data/parsed`, and Qdrant collection names.
- Produces: CLI commands `create`, `verify`, and `restore-drill`; helpers `parse_database_url`, `create_backup`, `verify_backup`, `prune_backups`, `validate_restore_target`, and `restore_postgres_dump`.

- [ ] **Step 1: Add failing backup tests.**

Test that URL passwords are returned only in an internal value and never in `pg_dump` argv or rendered output, that the manifest records size and SHA-256 for the dump/archive/snapshot files, that a changed artifact fails verification, that retention deletes only `careercrew-*` directories older than the cutoff, and that source/root/path-traversal restore targets are rejected.

- [ ] **Step 2: Run the focused tests and observe the missing implementation.**

```powershell
F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_backup_restore.py -q
```

Expected: import/helper failures because the backup module does not exist.

- [ ] **Step 3: Implement backup creation with content-addressed manifest entries.**

Derive host, port, username, decoded password, database, and query options with `urllib.parse`; invoke `pg_dump` with host/port/user/database flags and `PGPASSWORD` only in the child environment. Request Qdrant snapshots, download each snapshot into the run directory, archive only `data/uploads` and `data/parsed` with safe relative names, and write `manifest.json` containing run time, source identifiers, sizes, SHA-256 values, collection point counts, and retention days. Never log the DSN or secret environment values.

- [ ] **Step 4: Implement verification, pruning, and restore protection.**

`verify` must check manifest schema, exact file size/hash, zip integrity, and `pg_restore --list` when available. `prune_backups` may remove only child directories whose names match `careercrew-YYYYMMDD-HHMMSS`; it must never delete the source data directory or an arbitrary path. `restore-drill` must derive a name such as `careercrew_restore_<timestamp>`, reject the source database and nonconforming names, restore PostgreSQL into that temporary database, verify `SELECT 1`, optionally copy/recover Qdrant snapshots into temporary `__restore__` collections using `QDRANT_CONTAINER`, verify point counts, and clean every temporary target in `finally`.

- [ ] **Step 5: Add a non-destructive Windows schedule installer and operator documentation.**

The PowerShell installer must accept an explicit Python path, repository root, task name, daily time, and retention days, then register a Task Scheduler action that runs `backup_restore.py create` without embedding credentials. Document the default 02:00 schedule, environment variables, `create/verify/restore-drill` commands, retention behavior, and the fact that restore drills use only generated temporary names.

- [ ] **Step 6: Run focused tests and a local artifact-only verification.**

```powershell
F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_backup_restore.py -q
F:\Python_develop\miniconda3\envs\careercrew\python.exe scripts/backup_restore.py --help
```

Expected: all focused tests pass; help lists `create`, `verify`, and `restore-drill`. Do not register a real Task Scheduler entry during this task.

- [ ] **Step 7: Commit the backup task files.**

```powershell
git add scripts/backup_restore.py scripts/install_backup_schedule.ps1 tests/unit/test_backup_restore.py docs/OPS_BACKUP.md .env.example
git commit -m "feat: automate verified backups and restore drills"
```

### Task 5: Fix container and security gates

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: the pinned Qdrant digest and the existing `security-audit` job.
- Produces: immutable Qdrant image reference and a blocking pip-audit job.

- [ ] **Step 1: Verify the exact current text before editing.**

The expected Qdrant reference is:

```text
qdrant/qdrant:v1.19.0@sha256:057ee3a8da769fe7310dd3537b4dc7583bf87a95ce8ac43c0af5a46bc580d1fc
```

The workflow must not contain `continue-on-error: true` under `security-audit`.

- [ ] **Step 2: Replace the mutable image and remove the failure bypass.**

Change only the Qdrant image reference and the security-audit job’s explanatory comments/`continue-on-error` setting. Keep the existing package set and `pip-audit` invocation; its nonzero result must fail the job.

- [ ] **Step 3: Validate compose/YAML text and commit.**

```powershell
docker compose config -q
rg -n 'qdrant/qdrant:v1\.19\.0@sha256:057ee3a8da769fe7310dd3537b4dc7583bf87a95ce8ac43c0af5a46bc580d1fc' docker-compose.yml
rg -n -C 4 'security-audit|continue-on-error' .github/workflows/ci.yml
git add docker-compose.yml .github/workflows/ci.yml
git commit -m "ci: pin qdrant and block dependency audit failures"
```

Expected: compose validation exits 0, the immutable image appears once, and `continue-on-error` is absent from the security-audit job.

### Task 6: Full verification and browser acceptance

**Files:**
- Modify: `docs/superpowers/plans/2026-09-06-job-preparation-verification.md`
- Create: `docs/superpowers/plans/2026-09-09-phase6-stability-governance-verification.md`

**Interfaces:**
- Consumes: Tasks 1–5, the local PostgreSQL/Qdrant containers, and the current frontend at `careercrew_web`.
- Produces: reproducible commands/results for tests, migration invariants, backup/restore, container supply-chain checks, and authenticated browser access.

- [ ] **Step 1: Run the full backend suite and focused operational checks.**

```powershell
F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest -q
F:\Python_develop\miniconda3\envs\careercrew\python.exe scripts/validate_migrations.py --static
F:\Python_develop\miniconda3\envs\careercrew\python.exe scripts/validate_migrations.py --database-url $env:DATABASE_URL
```

Record exit codes and the complete pass/fail summary. If the online check cannot connect, report that as an environment limitation rather than a pass.

- [ ] **Step 2: Run frontend typecheck, Vitest, and build from `careercrew_web`.**

```powershell
npm run typecheck
npm run test -- --run
npm run build
```

- [ ] **Step 3: Perform controlled real backup/restore verification.**

With the existing local Docker services, run `backup_restore.py create`, `verify`, and `restore-drill` using a generated backup directory. Confirm the manifest hashes, temporary PostgreSQL cleanup, Qdrant temporary collection cleanup, and no new files outside the backup root. Do not touch the existing `data/backups/careercrew-pre-0008-20260909-101905.dump` artifact.

- [ ] **Step 4: Use an authorized local browser/Playwright session for the browser route.**

Open the running CareerCrew app, authenticate with a disposable local test account if needed, and verify the matcher CDP status bar receives a 200 response and renders connected/disconnected state. Verify an anonymous request is 401 and a non-loopback ASGI client is 403 through the API test harness. Do not access third-party sites or expose credentials in the report.

- [ ] **Step 5: Scan and record the final evidence.**

Run `git diff --check`, `git status --short`, a secret-pattern scan limited to changed files and generated reports, and `docker compose config -q`. Append exact commands, counts, environment limitations, and browser evidence to the new verification report. Preserve unrelated worktree files.

- [ ] **Step 6: Complete a final review before claiming completion.**

Review the plan checklist against the actual diff, rerun the full backend and frontend verification commands after any review fix, and only then report the delivered scope. Do not push or merge.
