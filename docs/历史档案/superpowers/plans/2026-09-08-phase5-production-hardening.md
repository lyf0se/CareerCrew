# CareerCrew Phase 5 Production Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Secure and operationalize CareerCrew's completed job-search workflow, add reliable background state, measurable AI degradation, scalable search, and privacy-safe pilot analytics.

**Architecture:** PostgreSQL remains the durable source of truth. Focused FastAPI routers and store classes own sharing, generation metrics, search, upload jobs, and product events while preserving existing URLs. React keeps the existing workflow and emits only explicit, content-free product events.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, psycopg 3, Alembic, PostgreSQL, React 18, TypeScript, Vitest, Docker CLI.

**Spec:** `docs/superpowers/plans/2026-09-08-phase5-production-hardening-spec.md`

## Global Constraints

- Preserve “AI 准备、人工确认、完整留痕”; no automatic application or HR contact.
- Preserve all existing API paths and tenant isolation behavior.
- Never persist share-token plaintext, JD text, resume text, contact text, or generated content in telemetry.
- Preserve unrelated working-tree changes and review the existing phase-5 draft before editing it.
- Use tests first for every production-code change.
- Do not push or merge unless the user asks.

---

### Task 1: Secure public shares

**Files:**
- Modify: `migrations/versions/0008_prod_hardening.py`
- Modify: `careercrew_core/career/store.py`
- Create: `careercrew_api/routers/career_shares.py`
- Modify: `careercrew_api/routers/career.py`
- Test: `tests/api/test_career_api.py`
- Test: `tests/integration/test_career_store_pg.py`

**Interfaces:**
- Consumes: existing `CareerStore` dependency and preparation ownership lookups.
- Produces: `create_share`, `list_shares`, `revoke_share`, `resolve_share`; database rows expose `token_hint`, never plaintext.

- [x] **Step 1: Add failing tests for default masking, expanded PII, one-time plaintext, token hashing, tenant isolation, access audit, security headers, and rate limiting.**
- [x] **Step 2: Run focused share tests and confirm failures describe the missing behavior.**
- [x] **Step 3: Complete migration and store changes using parameterized SQL and constant-time token digest comparison through indexed lookup.**
- [x] **Step 4: Move share schemas/routes/helpers to `career_shares.py`, re-export the router through the existing career router, and implement bounded in-process rate limiting without storing token values.**
- [x] **Step 5: Run focused API and PostgreSQL integration tests, then record the results in the verification report.**

### Task 2: Persist upload-task status

**Files:**
- Create: `careercrew_core/upload_tasks.py`
- Modify: `careercrew_api/routers/resume.py`
- Modify: `careercrew_api/routers/knowledge.py`
- Modify: `careercrew_api/main.py`
- Modify: `migrations/versions/0008_prod_hardening.py`
- Test: `tests/unit/test_upload_tasks.py`
- Test: `tests/api/test_resume_api.py`
- Test: `tests/api/test_knowledge_api.py`

**Interfaces:**
- Consumes: `DATABASE_URL`, shared psycopg pool, current in-memory job dictionaries.
- Produces: `UploadTaskStore.create/update/get/mark_interrupted`; worker-safe status polling with owner checks.

- [x] **Step 1: Add failing unit tests for durable create/update/get, JSON results, breaker recovery, owner-scoped reads, and interrupted-task recovery.**
- [x] **Step 2: Add failing API tests proving a status lookup can recover from the persistent store and cannot read another user's task.**
- [x] **Step 3: Run focused tests and verify red results.**
- [x] **Step 4: Implement the minimal store and wire both upload flows so every state transition updates memory and PostgreSQL.**
- [x] **Step 5: Remove the obsolete multi-worker warning once cross-worker polling is durable; retain an explicit warning that execution itself is local.**
- [x] **Step 6: Run unit and API tests and append evidence to the verification report.**

### Task 3: Make release rehearsal safe and repeatable

**Files:**
- Create: `scripts/release_rehearsal.py`
- Create: `tests/unit/test_release_rehearsal.py`
- Create: `docs/OPS_RELEASE_REHEARSAL.md`
- Modify: `migrations/versions/0008_prod_hardening.py`

**Interfaces:**
- Consumes: `DATABASE_URL`, Docker or local PostgreSQL command availability, Alembic configuration.
- Produces: isolated temporary databases with a generated prefix, nonzero failures, and a Markdown evidence report.

- [x] **Step 1: Add failing tests for DSN parsing, safe database-name validation, command construction, report rendering, and cleanup on exceptions.**
- [x] **Step 2: Run the unit tests and confirm the hard-coded credentials/current cleanup behavior fail.**
- [x] **Step 3: Refactor the script into pure helpers plus `main`, derive credentials from the DSN, stream backups correctly, and guarantee `finally` cleanup.**
- [x] **Step 4: Run unit tests, then run the four-path rehearsal against an isolated local PostgreSQL instance.**
- [x] **Step 5: Write the actual versions, table counts, restore checks, duration, and any environmental limitation to `docs/OPS_RELEASE_REHEARSAL.md`.**

### Task 4: Validate and measure AI structured output

**Files:**
- Create: `careercrew_core/ai/structured_output.py`
- Create: `careercrew_core/career/generation_metrics.py`
- Create: `careercrew_api/routers/career_generation.py`
- Modify: `careercrew_api/routers/career.py`
- Modify: `migrations/versions/0008_prod_hardening.py`
- Test: `tests/unit/test_structured_output.py`
- Test: `tests/api/test_career_generation_api.py`

**Interfaces:**
- Consumes: raw model response, explicit Pydantic response type, user ID and feature name.
- Produces: `parse_json_model(text, model_type)`, content-free generation metrics, and `/api/career/generation-metrics`.

- [x] **Step 1: Add failing tests for fenced JSON, surrounding prose, malformed JSON, wrong field types, output bounds, template fallback, and content-free metrics.**
- [x] **Step 2: Run the focused tests and verify failures.**
- [x] **Step 3: Implement a single decoder using `json.JSONDecoder.raw_decode` candidates plus Pydantic validation; define explicit ATS, application-kit, and intelligence schemas.**
- [x] **Step 4: Record latency/source/parse outcome in PostgreSQL and expose owner-scoped aggregate counts and P95 latency.**
- [x] **Step 5: Replace the three ad-hoc parsers while preserving their existing template results and response shape.**
- [x] **Step 6: Run focused and career API tests and append evidence.**

### Task 5: Add cursor search and PostgreSQL indexes

**Files:**
- Create: `careercrew_api/routers/career_search.py`
- Modify: `careercrew_core/career/store.py`
- Modify: `careercrew_web/src/lib/career.ts`
- Modify: `careercrew_web/src/components/GlobalSearch.tsx`
- Modify: `migrations/versions/0008_prod_hardening.py`
- Test: `tests/api/test_career_search_api.py`
- Test: `careercrew_web/src/components/GlobalSearch.test.tsx`

**Interfaces:**
- Consumes: `q`, `limit`, opaque base64url cursor tied to the authenticated owner and query digest.
- Produces: `{items: SearchResult[], next_cursor: string | null}` at the existing search URL.

- [x] **Step 1: Add failing API tests for default/max limits, deterministic next pages, malformed/cross-query cursors, tenant isolation, and stable result types.**
- [x] **Step 2: Add a failing component test for the paged response and “加载更多” behavior.**
- [x] **Step 3: Run focused tests and verify failures.**
- [x] **Step 4: Implement cursor encoding/validation and a bounded union query; add trigram extension/index migration statements.**
- [x] **Step 5: Adapt the frontend without changing the search entry or existing result navigation.**
- [x] **Step 6: Run API/component tests and append evidence.**

### Task 6: Capture privacy-safe pilot events and complete router decomposition

**Files:**
- Create: `careercrew_core/career/product_events.py`
- Create: `careercrew_api/routers/career_events.py`
- Modify: `careercrew_api/routers/career.py`
- Modify: `careercrew_web/src/lib/career.ts`
- Modify: `careercrew_web/src/components/preparation/SharePanel.tsx`
- Modify: `careercrew_web/src/components/preparation/ApplicationKitPanel.tsx`
- Modify: relevant job-save and reminder components discovered during implementation
- Modify: `migrations/versions/0008_prod_hardening.py`
- Test: `tests/api/test_career_events_api.py`
- Test: focused Vitest component tests

**Interfaces:**
- Consumes: allow-listed event names, authenticated owner, optional object ID/source.
- Produces: idempotent `POST /api/career/events` and owner-scoped `GET /api/career/events/funnel`.

- [x] **Step 1: Add failing API tests for the allow-list, payload size/field rejection, tenant isolation, idempotency, and funnel counts.**
- [x] **Step 2: Add focused frontend tests proving events fire after successful user actions and never contain content fields.**
- [x] **Step 3: Run focused tests and verify failures.**
- [x] **Step 4: Implement the event store/router and wire only the specified successful actions.**
- [x] **Step 5: Finish moving generation/search/share routes out of the monolithic router while keeping `/api/career/*` paths stable.**
- [x] **Step 6: Run all career, frontend type, and focused UI tests and append evidence.**

### Task 7: Full verification, security review, and browser acceptance

**Files:**
- Modify: `docs/superpowers/plans/2026-09-06-job-preparation-verification.md`
- Modify: `docs/OPS_RELEASE_REHEARSAL.md`

**Interfaces:**
- Consumes: Tasks 1–6 and migration head `0008_prod_hardening`.
- Produces: reproducible test/browser evidence and a clean reviewable worktree.

- [x] **Step 1: Apply `alembic upgrade head` to the development database after a backup and verify schema invariants.**
- [x] **Step 2: Run backend focused tests, then the complete backend suite with the CareerCrew interpreter.**
- [x] **Step 3: Run frontend TypeScript, complete Vitest, and production build.**
- [x] **Step 4: Run security review for share tokens, tenant isolation, telemetry data minimization, cursor validation, SQL parameterization, and migration safety.**
- [x] **Step 5: Use the local browser to verify default masked sharing, public security headers, paged search, template fallback, and personal funnel metrics.**
- [x] **Step 6: Scan the worktree for secrets, test accounts, raw tokens, rehearsal artifacts, and temporary migrations; remove only artifacts created by this plan.**
- [x] **Step 7: Append exact commands/results to the verification report and run a final whole-branch review.**
