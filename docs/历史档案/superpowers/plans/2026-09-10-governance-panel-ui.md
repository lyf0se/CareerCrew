# Governance Panel UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the already owner-scoped memory and knowledge governance APIs in the existing CareerCrew data panels so users can review, correct, and re-index durable content without leaving the product.

**Architecture:** Keep the current compact cards and Lucide icon language. Add progressive disclosure inside each row: everyday read/delete stays visible, governance actions appear only for durable records with a version, and history/chunk details expand on demand. All mutations use the existing `apiFetch` auth boundary, optimistic row versions, disabled loading states, and inline errors.

**Tech Stack:** React 19, TypeScript, Vitest, Testing Library, Tailwind utility classes, existing Radix/Lucide primitives.

**Spec:** `docs/superpowers/specs/2026-09-10-phase7-knowledge-intelligence-quality.md`

## Global Constraints

- Preserve legacy memory and knowledge upload/delete behavior; governance controls are additive.
- Never display or send raw secrets; governance requests contain only the selected record/version and user-entered correction text.
- Treat HTTP 409 as a refresh-required conflict, not as a successful mutation.
- Use visible labels, keyboard-accessible native controls, 44px minimum primary touch targets, and existing semantic color tokens.
- Do not claim full knowledge governance coverage while legacy uploads are not linked to governance document IDs.

---

### Task 1: Add memory correction controls and history

**Files:**
- Modify: `careercrew_web/src/components/data/MemoryPanel.tsx`
- Modify: `careercrew_web/src/components/data/MemoryPanel.test.tsx`

**Interfaces:**
- Governed memory rows use `id` as the UUID and `version` as `row_version`; legacy rows keep the delete-only UI.
- PATCH `/api/memory/records/{id}` receives `{ action, row_version, display_text?, reason? }`.
- POST `/api/memory/records/{id}/merge` receives `{ other_memory_id, row_version, other_row_version, reason }`.
- GET `/api/memory/records/{id}/history` returns `{ record, events }`.

- [x] **Step 1: Write failing tests** for confirm/ignore action requests, inline edit save, 409 refresh messaging, history expansion, and merge candidate selection.
- [x] **Step 2: Run the focused Vitest file red.**
- [x] **Step 3: Implement progressive controls** with local edit/history/merge state and parent reload after successful mutation.
- [x] **Step 4: Run the focused file and existing data-panel tests.**

### Task 2: Add knowledge governance overview and version actions

**Files:**
- Modify: `careercrew_web/src/components/KnowledgePanel.tsx`
- Modify: `careercrew_web/src/components/KnowledgePanel.test.tsx`

**Interfaces:**
- GET `/api/knowledge/governance/documents` returns `{ items, total }` with versions, chunks, expiry, credibility, and citation hits.
- PATCH `/api/knowledge/governance/documents/{id}` updates `expires_at` and/or `credibility`.
- POST `/api/knowledge/governance/documents/{id}/versions/{version_id}/reindex` starts a version reindex and returns the refreshed detail.
- Existing legacy `/api/knowledge` is still the upload/list/delete source and remains visible when no governance record is linked.

- [x] **Step 1: Write failing tests** for governance loading, expiry/credibility editing, version/chunk disclosure, citation-hit display, and reindex progress/error feedback.
- [x] **Step 2: Run the focused Vitest file red.**
- [x] **Step 3: Add a collapsible “治理视图” section** that loads independently so an unavailable governance service does not hide the legacy library.
- [x] **Step 4: Run the focused file and the full frontend test suite.**

### Task 3: Responsive and release verification

**Files:**
- Modify: `docs/superpowers/plans/2026-09-10-governance-panel-ui.md`
- Create: `docs/superpowers/plans/2026-09-10-governance-panel-ui-verification.md`

- [x] **Step 1: Run frontend test, lint, and production build.**
- [x] **Step 2: Check the panels at 375px and desktop widths with the local browser harness; verify keyboard focus and no horizontal overflow.**
- [x] **Step 3: Record that governance rows are tested with mocked API responses and that current legacy uploads still need server-side document linkage for complete end-to-end governance.**
