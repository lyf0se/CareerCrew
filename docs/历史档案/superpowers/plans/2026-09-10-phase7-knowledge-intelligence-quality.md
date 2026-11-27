# CareerCrew Phase 7 Knowledge and Intelligence Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining P1 quality loop: real-model evaluation, human-governed memory correction, knowledge-document governance, usage budgets, and safe baseline observability.

**Architecture:** Keep current FastAPI owner-scoped routers, Agent runtime, Qdrant access filters, and asynchronous upload model. Add isolated quality services and new migration revisions after 0008. Use immutable event/usage records, explicit governance endpoints, and derived metrics with low-cardinality labels. Do not couple this phase to the dirty Phase 5 Career Center/search/share files.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, Alembic/PostgreSQL, Qdrant, Prometheus text exposition, React/TypeScript/Vitest where UI is needed.

**Spec:** `docs/superpowers/specs/2026-09-10-phase7-knowledge-intelligence-quality.md`

## Global Constraints

- Preserve every existing dirty Phase 5 file and behavior. Do not reset, clean, checkout, or broad-rewrite the worktree; do not stage or commit a dirty shared file without explicit user instruction to submit it.
- Published migration `0008_prod_hardening` is immutable. New database objects begin at `0009` and the migration checksum/static validation contract remains intact.
- Preserve tenant isolation: resource IDs are never authorization. Public knowledge can be read by policy but only an owner/admin can govern it.
- Preserve “AI draft, human confirmation, full trace”; no automatic application, HR contact, or external side effect.
- Never persist JD/resume/prompt/output/API keys/raw tool arguments in usage, metrics labels, or generic audit events. Use hashes, enums, counts, and redacted summaries.
- Use TDD for every production behavior change: add a failing focused test, run it, implement the smallest change, then run focused and relevant regression suites.
- Local tests use fakes or an explicit test DSN only; do not mutate production databases, real user data, or the configured Qdrant collections.
- Do not push or merge. Keep implementation changes reviewable; isolated new files may be committed only when they contain no user-owned dirty overlap, otherwise leave them unstaged for the user.

---

### Task 1: Implement real-model evaluation and strict regression semantics

**Files:**
- Modify: `scripts/eval_runner.py`
- Create: `scripts/eval_real.py` if a separate adapter keeps the runner testable
- Create: `data/eval/README.md`
- Create/modify: `tests/unit/test_eval_runner.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes the fixed JSONL case dataset and current `careercrew_ai.llm.create_llm` / runtime adapter.
- Produces validated case loading, actual model collection for supported case kinds, experiment metadata, per-case consult bounds, normalized bad-case scoring, `--require-real` failure semantics, and a non-swallowing release/nightly CI split.

- [x] Write tests for schema validation, real adapter injection, metadata, per-case bounds, bad-case normalization, and strict-vs-optional CLI exit codes.
- [x] Run the focused tests red.
- [x] Implement the adapter against the existing production LLM/Agent boundary; keep a fake adapter injectable in unit tests.
- [x] Add an immutable dataset README/version contract and reject malformed or duplicate case IDs.
- [x] Update CI so optional nightly observation is explicitly `--allow-skip`, while protected release/manual evaluation uses `--require-real --fail-on-regression` and uploads its report.
- [x] Run focused tests and offline gate; record the real-model environment limitation if no protected key is available locally.

### Task 2: Add migration 0009 and long-term-memory correction workflow

**Files:**
- Create: `migrations/versions/0009_phase7_quality_governance.py`
- Create: `careercrew_core/memory/governance.py`
- Create: `careercrew_api/routers/memory_governance.py`
- Create: `tests/unit/test_memory_governance.py`
- Create: `tests/api/test_memory_governance_api.py`
- Modify only if necessary: `careercrew_api/main.py`, `careercrew_core/memory/records.py`, `careercrew_core/memory/service.py`

**Interfaces:**
- Produces `PATCH /api/memory/records/{id}` actions `confirm|edit|ignore|expire`, `POST .../{id}/merge`, `GET .../{id}/history`, status filtering, optimistic locking, immutable events, and owner-scoped responses.
- Migration adds `memory_record_events` and any minimal knowledge/usage tables that later tasks explicitly consume; it does not alter 0008.

- [x] Test owner isolation and 409 version conflicts first.
- [x] Add action state transitions, sources/relations snapshots, and history ordering.
- [x] Ensure default memory search excludes ignored/expired/superseded/deleted records.
- [x] Add UI controls in an isolated MemoryPanel slice only after API acceptance; cover success, conflict, and history rendering.

### Task 3: Add knowledge-document version and indexing governance

**Files:**
- Create: `careercrew_core/knowledge/governance.py`
- Create: `careercrew_api/routers/knowledge_governance.py`
- Create: `tests/unit/test_knowledge_governance.py`
- Create: `tests/api/test_knowledge_governance_api.py`
- Modify only if necessary: `careercrew_api/main.py`, `careercrew_api/routers/knowledge.py`, upload worker integration
- UI: isolated governance components and tests under `careercrew_web/src/components/knowledge/`

**Interfaces:**
- Produces owner-scoped document/version/chunk details, duplicate detection by raw SHA-256, expiry/credibility updates, whole-version reindex jobs with atomic active switch, and citation-hit counters.

- [x] Test duplicate privacy, version chain, chunk preview, expiry filtering, reindex failure preservation, and citation idempotency.
- [x] Implement relational governance state with Qdrant references rather than replacing current retrieval in one step.
- [x] Add the smallest UI that exposes version/status/expiry/credibility/chunks/reindex progress without changing current upload behavior.
- [x] Run existing knowledge visibility/owner tests plus new governance tests.

### Task 4: Add usage ledger, budgets, tool governance, and Prometheus metrics

**Files:**
- Create: `careercrew_core/usage/ledger.py`
- Create: `careercrew_core/observability/metrics.py`
- Create: `careercrew_api/routers/usage.py`
- Create: `careercrew_api/routers/metrics.py`
- Create: `tests/unit/test_usage_ledger.py`, `tests/unit/test_budget_enforcement.py`, `tests/unit/test_metrics.py`
- Create: `tests/api/test_usage_api.py`, `tests/api/test_metrics_api.py`
- Modify only if necessary: runtime integration and `main.py`

**Interfaces:**
- Produces immutable token/cost events, user/module summaries, daily/monthly budgets, conservative reservation and explicit downgrade reasons, plus `/metrics` with HTTP/LLM/RAG/upload/tool counters and latency histograms.

- [x] Test pricing-version math, unknown prices, owner scope, budget boundary/concurrency behavior, and sensitive-label rejection.
- [x] Integrate usage recording at one stable Agent/model boundary before expanding to every route.
- [x] Expose admin-only policy changes and user-scoped summaries; no raw event body in responses.
- [x] Add low-cardinality Prometheus metrics and scrape smoke tests.

### Task 5: Phase 7 integration, review, and release evidence

**Files:**
- Create: `docs/superpowers/plans/2026-09-10-phase7-knowledge-intelligence-quality-verification.md`
- Modify: `docs/CAREER_PRODUCT_ROADMAP.md` only after implementation evidence exists
- Create focused test/report artifacts as required by the SDD ledger

- [x] Run focused suites for every task, then the full backend suite and frontend lint/test/build if UI changed.
- [x] Run static migration validation and verify the new head/schema on an isolated test database when available.
- [x] Review sensitive logging/tenant boundaries and manually inspect API schemas.
- [ ] Dispatch a final broad code review; resolve or explicitly park findings with ledger rulings.
- [x] Write a verification report that clearly marks local real-model evaluation as blocked when protected external credentials/services are unavailable.

---

## Phase 8 follow-up (separate plan required)

After Phase 7 is accepted, create a separate spec/plan for cross-session semantic search, message bookmarks, message branches, answer-to-action-item links, explainable consultation reports, resume master/derived versions with diff/annotations/batch export, and the tools center. Organization/mentor/member collaboration remains conditional on actual multi-user scale.
