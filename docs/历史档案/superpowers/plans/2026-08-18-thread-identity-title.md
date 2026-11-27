# Thread Identity and Summary Title Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent duplicate sidebar threads caused by legacy/UUID split and generate one summary title for the first completed turn.

**Architecture:** Keep legacy IDs at the frontend boundary and normalize backend memory writes through the conversation's `legacy_thread_id`. Suppress only the redundant frontend remap when the done event explicitly identifies the same legacy ID. Generate the first-turn title in the shared runtime finish path and update both conversation and memory metadata.

**Tech Stack:** FastAPI/Python 3.12, LangChain `BaseChatModel.invoke`, React 19, TypeScript, Vitest, pytest.

## Global Constraints

- Do not delete or rewrite unrelated pre-existing working-tree changes.
- Do not create a second thread or migrate data destructively during a normal message send.
- Title generation is best effort and must never turn a successful answer into an error.
- Do not add a `Co-Authored-By` trailer to commits.

---

### Task 1: Normalize backend memory thread identity

**Files:**
- Modify: `careercrew_api/runtime.py:425-501`
- Create: `tests/unit/test_thread_identity.py`

**Interfaces:**
- Add `CareerCrewRuntime._memory_thread_id(thread_id: str, user_id: str) -> str`.
- `_ensure_thread()` uses the normalized ID for `ThreadStore` reads/writes.

- [ ] **Step 1: Add a failing test**

  Build a `CareerCrewRuntime` with `FakeMemoryDb`, `ThreadStore`, `ConversationStore(FakeConversationDb())`, and `_initialized=True`; create one conversation from `legacy-1`, then call `_ensure_thread()` once with `legacy-1` and once with the returned conversation UUID. Assert `thread_store.list()` contains only `legacy-1`.

- [ ] **Step 2: Run the test and verify it fails**

  ```powershell
  $env:PYTHONPATH=(Get-Location).Path
  F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_thread_identity.py -q
  ```

  Expected: FAIL with two memory thread IDs.

- [ ] **Step 3: Implement normalization**

  Resolve an existing conversation through `conversation_store.get_conversation(thread_id, user_id)`. When it has a non-empty `legacy_thread_id`, use that value for `thread_store.get()` and `thread_store.upsert()`; otherwise use the input ID. Leave ownership/not-found exceptions as the input ID path so old legacy-only threads continue to work.

- [ ] **Step 4: Run the test and related thread tests**

  ```powershell
  F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_thread_identity.py tests/api/test_threads_menu_api.py tests/api/test_thread_scope_api.py -q
  ```

  Expected: PASS.

- [ ] **Step 5: Commit backend identity normalization**

  ```powershell
  git add -- careercrew_api/runtime.py tests/unit/test_thread_identity.py
  git commit -m "fix: normalize legacy and uuid thread records"
  ```

---

### Task 2: Keep the frontend on the legacy thread boundary

**Files:**
- Modify: `careercrew_web/src/store/streamStore.ts:243-280`
- Modify: `careercrew_web/src/store/streamStore.test.ts`

**Interfaces:**
- `StreamSession.doneIds.threadId` and `.legacyThreadId` continue to expose server stable IDs.
- `useThreadStore.remapLegacyThread()` remains available for responses without a matching legacy ID.

- [ ] **Step 1: Add a failing stream test**

  Feed a done event with `thread_id="uuid-1"`, `legacy_thread_id="t-1"` into a stream started with `t-1`; assert `currentThreadByModule.chat` remains `t-1` and the thread store does not call the legacy remap path.

- [ ] **Step 2: Run the test and verify it fails**

  ```powershell
  cd F:\agent_develop\CareerCrew\careercrew_web
  npm run test -- --run src/store/streamStore.test.ts
  ```

  Expected: FAIL because the current done handler always remaps to `evt.thread_id`.

- [ ] **Step 3: Guard the remap**

  Change the condition to remap only when `evt.thread_id !== threadId` and `evt.legacy_thread_id !== threadId`. When the legacy ID matches, keep session/controller/timer keys on the original ID while retaining both IDs in `doneIds`.

- [ ] **Step 4: Run the stream tests**

  ```powershell
  npm run test -- --run src/store/streamStore.test.ts
  ```

  Expected: PASS.

- [ ] **Step 5: Commit frontend identity behavior**

  ```powershell
  git add -- careercrew_web/src/store/streamStore.ts careercrew_web/src/store/streamStore.test.ts
  git commit -m "fix: preserve legacy thread ids after stream completion"
  ```

---

### Task 3: Generate and persist the first-turn summary title

**Files:**
- Modify: `careercrew_api/runtime.py:346-401`
- Create: `tests/unit/test_thread_title.py`

**Interfaces:**
- Add `CareerCrewRuntime._maybe_generate_first_title(ctx, assistant_text: str) -> None`.
- Add `CareerCrewRuntime._generate_title(user_text: str, assistant_text: str) -> str`.

- [ ] **Step 1: Add failing title tests**

  Use a fake LLM whose `invoke()` returns `AIMessage(content="目标岗位匹配与求职规划")`; create a first-turn `TurnContext` and assert both conversation title and legacy memory thread title become that text. Add a second-turn context and assert the fake LLM is not called. Add an exception fake and assert the original user title remains unchanged.

- [ ] **Step 2: Run the tests and verify they fail**

  ```powershell
  $env:PYTHONPATH=(Get-Location).Path
  F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_thread_title.py -q
  ```

  Expected: FAIL because no title-generation helper exists.

- [ ] **Step 3: Implement best-effort title generation**

  In `_finish_chat_turn`, after the normal `finish_turn()` call, invoke `_maybe_generate_first_title` only when `ctx.user_message_id` is present and the turn sequence is `1`. Fetch the original user message, redact and truncate both inputs, call `self.llm.invoke()` with a prompt requiring title-only output, normalize the first line to at most 30 characters, and skip persistence for empty output. Catch all title-generation and persistence errors with logging only.

  Update `conversation_store.rename_title(ctx.thread_id, ctx.user_id, title)` and `thread_store.upsert()` using `_memory_thread_id(ctx.thread_id, ctx.user_id)`, preserving pinned and retrieval scope.

- [ ] **Step 4: Run title and runtime regressions**

  ```powershell
  F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_thread_title.py tests/unit/test_regenerate_runtime.py tests/api/test_chat_api.py tests/api/test_stable_ids.py -q
  ```

  Expected: PASS; regenerate does not invoke title generation because it has no user message ID.

- [ ] **Step 5: Commit title generation**

  ```powershell
  git add -- careercrew_api/runtime.py tests/unit/test_thread_title.py
  git commit -m "feat: summarize first conversation title"
  ```

---

### Task 4: Full verification and boundary review

**Files:**
- Modify: none unless a scoped test exposes a regression.

- [ ] **Step 1: Run frontend tests and build**

  ```powershell
  cd F:\agent_develop\CareerCrew\careercrew_web
  npm run test -- --run
  npm run build
  ```

- [ ] **Step 2: Run backend thread/title regressions**

  ```powershell
  cd F:\agent_develop\CareerCrew
  $env:PYTHONPATH=(Get-Location).Path
  F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_thread_identity.py tests/unit/test_thread_title.py tests/api/test_threads_menu_api.py tests/api/test_thread_scope_api.py tests/api/test_chat_api.py tests/api/test_stable_ids.py -q
  ```

- [ ] **Step 3: Inspect boundaries**

  ```powershell
  git diff --check
  git status --short
  rg -n "remapLegacyThread|_memory_thread_id|_maybe_generate_first_title" careercrew_web/src/store/streamStore.ts careercrew_api/runtime.py
  ```

  Confirm unrelated pre-existing modifications remain untouched and no duplicate thread creation logic was added.
