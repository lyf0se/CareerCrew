# Task 3 Tenant Migration Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all Task 3 review findings while preserving authenticated tenant isolation, public logical IDs, existing tenant point IDs, and dry-run safety.

**Architecture:** Keep tenant-aware Qdrant UUID generation backward-compatible and move only legacy/no-owner source strings into a separate UUIDv5 namespace. Make SQLite checkpoint migration record completed source/destination pairs in a durable journal instead of classifying strings by prefix, inspect that journal against current state for idempotence, snapshot WAL databases through SQLite's backup API, and make every change-bearing run create a fresh backup. Make Qdrant migration compare destination payload/vector semantics so interrupted copies can finish cleanup, while mismatches and mutation failures are fatal.

**Tech Stack:** Python 3, sqlite3 backup API, qdrant-client local mode, pytest.

## Global Constraints

- Do not switch, reset, or check out the shared branch.
- Do not alter unrelated task work or pre-existing untracked SDD files.
- Preserve public `payload._id` and existing owner-scoped Qdrant physical IDs.
- Dry-run must not mutate SQLite, files, Postgres, or Qdrant.
- Qdrant mutation failures must raise or produce a nonzero CLI exit.
- Commit without a `Co-Authored-By` trailer.

---

### Task 1: Domain-separate Qdrant IDs

**Files:**
- Modify: `careercrew_ai/vector_store/qdrant_store.py`
- Test: `tests/unit/test_tenant_isolation.py`

**Interfaces:**
- Consumes: `QdrantStore._to_qid(sid: str, user_id: str = "") -> str`
- Produces: the same interface, with the existing tenant mapping retained and legacy IDs generated under a separate namespace.

- [x] **Step 1: Write a failing constructive-collision regression**

  Construct the former collision pair `legacy_id = f"{len(owner)}:{owner}{logical_id}"` and assert that `_to_qid(legacy_id)` differs from `_to_qid(logical_id, owner)`. Pin the existing tenant UUID result so the fix cannot accidentally re-key current tenant records.

- [x] **Step 2: Run the focused test and confirm it fails on the legacy mapping**

  Run `pytest -q tests/unit/test_tenant_isolation.py -k qdrant_physical` and expect the constructive-collision assertion to fail before implementation.

- [x] **Step 3: Introduce distinct legacy and tenant UUID namespaces**

  Retain the current namespace and length-prefixed source for owner-scoped values. Use a new fixed namespace for no-owner values so no identical UUIDv5 source-domain pair can be constructed across the two logical domains.

- [x] **Step 4: Re-run the focused tests**

  Run `pytest -q tests/unit/test_tenant_isolation.py -k qdrant_physical` and expect all selected tests to pass.

### Task 2: Make checkpoint migration complete, WAL-safe, and idempotent

**Files:**
- Modify: `scripts/migrate_legacy_tenant.py`
- Test: `tests/unit/test_tenant_migration.py`

**Interfaces:**
- Consumes: `tenant_thread_id(user_id, public_id)` and checkpoint tables containing a `thread_id` column.
- Produces: durable source/destination migration records, source/destination state inspection, and fresh consistent SQLite backups for every change-bearing apply.

- [x] **Step 1: Add failing regressions for `tenant:` public IDs and WAL backups**

  Test that a public ID shaped exactly like an internal ID (such as `tenant:7:u_adminpublic`) migrates, that the rerun reports zero changes, that the backup contains the pre-migration row committed in WAL, and that a later change-bearing apply creates a distinct second backup.

- [x] **Step 2: Run the checkpoint migration tests and confirm the regressions fail**

  Run `pytest -q tests/unit/test_tenant_migration.py -k checkpoint` and expect the public-prefix and stale/copy2 backup assertions to fail before implementation.

- [x] **Step 3: Implement durable migration state and SQLite snapshots**

  Record each selected tenant's source and computed destination in a small migration journal within the same transaction as the checkpoint updates, then inspect recorded destinations against current state instead of interpreting thread ID text. Replace filesystem copying of the main database with `sqlite3.Connection.backup()` to a fresh uniquely named backup database before each apply that has changes.

- [x] **Step 4: Re-run checkpoint migration tests**

  Run `pytest -q tests/unit/test_tenant_migration.py -k checkpoint` and expect all selected tests to pass.

### Task 3: Make Qdrant apply restart-idempotent and failure-strict

**Files:**
- Modify: `scripts/migrate_legacy_tenant.py`
- Test: `tests/unit/test_tenant_migration.py`

**Interfaces:**
- Consumes: scrolled source points and `client.retrieve/upsert/delete` Qdrant operations.
- Produces: semantic payload/vector comparison, retry cleanup for an identical copied destination, verified mutations, and raised/nonzero failures.

- [x] **Step 1: Add failing retry and failure regressions**

  Seed both a legacy source and semantically identical tenant destination and assert apply removes only the legacy source. Seed a mismatching destination and assert a conflict. Inject a delete failure after copy and assert the function raises, then retry with a healthy client and assert cleanup succeeds.

- [x] **Step 2: Run focused Qdrant migration tests and confirm failures**

  Run `pytest -q tests/unit/test_tenant_migration.py -k qdrant` and expect the identical-destination retry behavior to fail before implementation.

- [x] **Step 3: Implement semantic retry cleanup and strict mutation handling**

  Normalize Qdrant model/vector values before comparison. For identical destinations, skip the copy and delete the legacy source only under apply; for mismatches, record a conflict. After writes/deletes, retrieve and verify state; do not suppress any Qdrant exception in the CLI success path.

- [x] **Step 4: Re-run Qdrant migration tests**

  Run `pytest -q tests/unit/test_tenant_migration.py -k qdrant` and expect all selected tests to pass.

### Task 4: Verify isolation and deliver the remediation

**Files:**
- Create: `.superpowers/sdd/task-3-fix-report.md`
- Modify as required by Tasks 1-3 only.

**Interfaces:**
- Consumes: all Task 3 migration and tenant isolation behavior.
- Produces: a focused green regression suite, self-reviewed diff, detailed report, and one scoped commit.

- [x] **Step 1: Run all Task 3 focused and authenticated API tests**

  Run `pytest -q tests/unit/test_tenant_migration.py tests/unit/test_tenant_isolation.py tests/unit/test_read_image.py tests/api/test_tenant_isolation_api.py` with repository root on `PYTHONPATH`.

- [x] **Step 2: Run relevant prior tests and static checks**

  Run feasible `tests/api` coverage, `py_compile` for changed production files, and `git diff --check`.

- [x] **Step 3: Self-review every review requirement against the diff**

  Confirm constructive domain separation, `tenant:` public-ID migration, rerun idempotence, consistent fresh backups, Qdrant retry cleanup, mismatch failure, mutation failure propagation, dry-run immutability, and unchanged authenticated isolation.

- [x] **Step 4: Write the fix report and commit only scoped files**

  Record exact fixes, commands/results, and residual concerns in `.superpowers/sdd/task-3-fix-report.md`; stage the scoped implementation, tests, plan, and report; commit without any co-author trailer.
