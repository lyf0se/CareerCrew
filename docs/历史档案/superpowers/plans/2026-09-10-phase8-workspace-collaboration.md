# CareerCrew Phase 8 Workspace and Collaboration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the remaining P1/P2 workspace capabilities after Phase 7 is accepted: conversation traceability, explainable consultation, resume master workspace, and tool operations center.

**Architecture:** Extend the existing conversation/message, consult, resume, action-item, ToolRegistry, and HITL boundaries with owner-scoped relational records. Use asynchronous derived indexes/exports and immutable event logs. Do not make organization collaboration part of the first implementation unless a confirmed multi-tenant requirement exists.

**Tech Stack:** Python/FastAPI/PostgreSQL/Alembic/Qdrant, React/TypeScript/Vitest, Playwright for browser acceptance, existing export/runtime services.

**Spec:** `docs/superpowers/specs/2026-09-10-phase8-workspace-collaboration.md`

## Global Constraints

- Start only after the Phase 7 ledger has no open Important/Critical findings.
- Preserve existing dirty work and all current API paths; use new migrations after the Phase 7 head.
- Every message/thread/task/plan/resume/tool ID is owner-scoped before access or mutation.
- AI outputs are drafts. Saving, confirming, exporting, enabling a tool, and executing a plan are separate user actions.
- Do not expose raw prompts, full JD/resume content, API keys, or raw tool arguments in audit/metrics responses.
- Test with TDD, focused API/unit tests, frontend tests, and real browser verification at desktop and 375px widths.

---

### Task 1: Conversation traceability

Add bookmarks, owner-scoped cross-session semantic search, message-bounded branches, and source-linked action items. Verify delete/clear/index consistency and no cross-user leakage.

### Task 2: Explainable consultation

Persist validated opinions/evidence, consensus/disagreement, alternatives matrix, risk list, and versioned execution-plan drafts. Add a read-only report UI and explicit save/confirm transitions.

### Task 3: Resume master workspace

Add master/derived version relationships, structured reusable experience materials, text/field diff, annotations, and owner-scoped asynchronous batch PDF/DOCX export.

### Task 4: Tool operations center

Expose registry status, effective permissions, redacted call history, failure categories, health probes, and admin-only enable/disable with audit. Keep MCP network access restricted.

### Task 5: Integration and scale decision

Run full backend/frontend/browser verification, update the roadmap, and document whether organization/mentor/member roles are justified by observed usage. Defer them explicitly when the product remains single-user.
