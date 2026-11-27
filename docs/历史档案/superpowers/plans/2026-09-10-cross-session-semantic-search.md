# Cross-Session Semantic Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Phase 8 conversation-search `text_fallback` with owner-scoped embedding retrieval while retaining a deterministic text fallback when the local model or Qdrant is unavailable.

**Architecture:** Keep `messages` as the source of truth and build a separate Qdrant collection for ordinary conversation messages. On each search, synchronize only new or changed owned messages, query dense/sparse embeddings with an owner and record-type filter, then re-read the matching messages from the source store before returning snippets. Runtime initialization loads only the embedding and vector store for this route; it never initializes the LLM, reranker, or ingestion pipeline.

**Tech Stack:** Python 3.12, pytest, NumPy, BGE-M3 embedding, Qdrant, FakeVectorStore, FastAPI, Alembic-free configuration.

**Spec:** `docs/superpowers/specs/2026-09-10-phase8-workspace-and-collaboration.md`

## Global Constraints

- Conversation rows remain the authorization and content source of truth; a vector hit never grants access.
- Every Qdrant message point carries both `owner_user_id` and `user_id`, and every query applies `owner_user_id=<current user>` plus `record_type=conversation_message`.
- Deleted or missing source messages are never returned, even if a stale vector point exists.
- Embedding/Qdrant errors degrade to the existing `text_fallback` search response and do not make bookmarks, branches, or action items unavailable.
- Ordinary conversation vectors use `careercrew_workspace_messages`; existing knowledge and episodic collections are not reused or modified.
- The new collection is included in backup defaults and owner-scan configuration.
- Use TDD for production behavior: each behavior starts with a failing focused test and is followed by focused and relevant regression verification.

---

### Task 1: Add the semantic index contract and owner-safe synchronization

**Files:**
- Create: `careercrew_core/workspace/semantic_search.py`
- Create: `tests/unit/test_workspace_semantic_search.py`

**Interfaces:**
- `ConversationSemanticSearch(embedding, vector_store, message_loader)` consumes `BaseEmbedding`, `BaseVectorStore`, and `message_loader(owner_id) -> list[dict]`.
- `search(query, owner_id, limit) -> dict` returns the same item shape as `WorkspaceTraceability.search` with `mode="embedding"` on success.
- `sync(owner_id, rows) -> None` upserts only changed/new non-deleted messages and removes stale owner points by their `message_id` metadata.

- [x] **Step 1: Write failing tests** for owner filters, new/changed synchronization, stale-source suppression, and embedding error propagation.
- [x] **Step 2: Run `pytest tests/unit/test_workspace_semantic_search.py -q` and confirm the new import/behavior fails for the intended missing implementation.**
- [x] **Step 3: Implement the smallest index**: derive signatures from message id/content/role/status, compare existing records with `get_by_ids`, encode only changes, upsert dense+sparse records, query with owner/type filters, and map result ids back to current owned rows.
- [x] **Step 4: Run the focused semantic tests and the existing workspace unit tests.**

### Task 2: Integrate lazy runtime initialization and the API fallback

**Files:**
- Modify: `careercrew_core/workspace/traceability.py`
- Modify: `careercrew_api/routers/workspace.py`
- Modify: `careercrew_api/runtime/heavy.py`
- Create/modify: `tests/unit/test_workspace_traceability.py`
- Create/modify: `tests/api/test_workspace_api.py` if the existing API fixture exposes a stable search route test

**Interfaces:**
- `WorkspaceTraceability.attach_semantic_search(index) -> None` attaches an optional backend.
- `WorkspaceTraceability.search()` attempts the semantic backend first and calls the existing SQL/in-memory search on any backend failure.
- `HeavyInitMixin._ensure_workspace_semantic_search()` returns a semantic index after `_ensure_stores()` and does not call `_init_heavy_locked()`.

- [x] **Step 1: Add failing tests** proving a semantic result is used, a backend exception returns `text_fallback`, and a fake runtime without the lazy method keeps current behavior.
- [x] **Step 2: Run the focused tests red.**
- [x] **Step 3: Refactor the existing fallback into a private method, attach the backend in the search route, and implement lazy embedding/Qdrant construction with the configured message collection.**
- [x] **Step 4: Run workspace unit/API tests and confirm ordinary workspace endpoints do not initialize the LLM.**

### Task 3: Register backup, ownership, and configuration coverage

**Files:**
- Modify: `config/settings.yaml`
- Modify: `config/settings.docker.yaml`
- Modify: `scripts/backup_restore.py`
- Modify: `scripts/verify_qdrant_ownership.py`
- Modify: `tests/unit/test_verify_qdrant_ownership.py` if the configured collection map is expanded
- Modify: `tests/unit/test_backup_restore.py` only when default collection behavior is covered

**Interfaces:**
- Configuration key `vector_store.collections.conversation_messages` resolves to `careercrew_workspace_messages` in local and Docker variants.
- Backup defaults include the new collection without changing explicit `--collections` behavior.
- Owner scan treats the new collection as `owner_user_id` keyed data and skips it only when the collection does not exist.

- [x] **Step 1: Add failing configuration/ownership assertions.**
- [x] **Step 2: Run those focused tests red.**
- [x] **Step 3: Add the collection to both configs, backup defaults, and the owner resolver.**
- [x] **Step 4: Run config, backup, and ownership tests.**

### Task 4: Live semantic smoke and evidence update

**Files:**
- Modify: `docs/superpowers/plans/2026-09-10-cross-session-semantic-search.md`
- Create: `docs/superpowers/plans/2026-09-10-cross-session-semantic-search-verification.md`

- [x] **Step 1: Run the focused suite, backend workspace/API regression, migration validator, and frontend checks if the API response contract changes.**
- [x] **Step 2: With local Docker PostgreSQL/Qdrant and the configured BGE-M3 model, seed disposable owner-scoped messages, perform a semantic query, verify cross-owner exclusion, and clean all temporary rows/points.**
- [x] **Step 3: Record the exact command results, fallback result when the model is unavailable, and remaining protected real-model evaluation limitation.**
- [x] **Step 4: Leave the phase plan checkboxes honest: implementation and tests may be checked, but protected external model quality gates remain unchecked until credentials and the release dataset are available.**

---

## Self-review / known boundary

This plan closes the explicit semantic-search gap only. Memory and knowledge governance UI controls, protected real-model evaluation, and production deployment acceptance remain separate release gates until their own evidence exists.
