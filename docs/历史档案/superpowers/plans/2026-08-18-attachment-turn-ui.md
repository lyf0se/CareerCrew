# One-Turn Attachments and Composer UI Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make uploaded PDFs and images usable in the current AI turn, show each turn's attachments above its user bubble, leave only the attachment action in the composer, and hide stale tooltips while resizing the composer.

**Architecture:** Keep the existing attachment upload and server-side ownership checks. Make the picker a pending-turn controller with `pick()` and `clear()` handles; each page snapshots pending attachments into its request and `ChatMessage`, while conversation metadata supplies summaries during history restore. Harden the existing attachment block resolver with correct image MIME handling and a PyMuPDF PDF fallback, and make the shared Tooltip hide on pointerdown.

**Tech Stack:** FastAPI/Python 3.12, PyMuPDF, React 19, TypeScript, Tailwind CSS, Vitest, Testing Library, pytest.

## Global Constraints

- Preserve all unrelated uncommitted user changes in the working tree.
- Do not remove backend `mentions` or `tools` request fields; remove only their current frontend entry points and payload generation.
- Attachments are sent only from the pending list captured at the beginning of the current turn; after request start the pending composer list is empty.
- Store only attachment display metadata in frontend message state; never copy attachment body text into `ChatMessage`.
- Do not add a new database table or migration.
- Do not add a `Co-Authored-By` trailer to commits.

---

### Task 1: Add attachment-context regression coverage and harden PDF/image parsing

**Files:**
- Create: `tests/unit/test_attachment_context.py`
- Modify: `careercrew_api/attachment_context.py:53-80`
- Modify: `careercrew_api/runtime.py:2080-2141`

**Interfaces:**
- `describe_image(settings, image_path, prompt="请详细描述这张图片的内容，并提取其中的文字。", mime_type=None) -> str` chooses the supplied MIME type or infers one from the file extension.
- `CareerCrewRuntime.resolve_attachment_blocks(user_id, refs) -> list[dict]` keeps returning blocks with `id`, `filename`, `kind`, and `content`.
- Add a private `extract_pdf_text(path: str) -> str` helper in `careercrew_api.attachment_context` for the PyMuPDF fallback.

- [ ] **Step 1: Write failing tests for image MIME and PDF fallback**

  Create a focused test module that uses temporary files and mocks rather than initializing the real runtime:

  ```python
  def test_describe_image_uses_jpeg_mime(monkeypatch, tmp_path):
      image = tmp_path / "photo.jpg"
      image.write_bytes(b"jpeg-bytes")
      captured = {}

      class Response:
          choices = [type("Choice", (), {"message": type("Message", (), {"content": "图中有一份简历"})()})()]

      class Completions:
          def create(self, **kwargs):
              captured.update(kwargs)
              return Response()

      class Client:
          chat = type("Chat", (), {"completions": Completions()})()

      monkeypatch.setattr("openai.OpenAI", lambda **_: Client())
      settings = type("Settings", (), {"vlm": type("Vlm", (), {
          "base_url": "https://example.test/v1", "api_key": "key", "model": "vision"
      })()})()

      assert describe_image(settings, str(image), mime_type="image/jpeg") == "图中有一份简历"
      assert captured["messages"][0]["content"][1]["image_url"]["url"].startswith(
          "data:image/jpeg;base64,"
      )

  def test_pdf_resolution_falls_back_to_pymupdf(monkeypatch, tmp_path):
      from careercrew_api import storage
      from careercrew_api.runtime import CareerCrewRuntime
      from careercrew_core.conversation.attachments import AttachmentStore, FakeAttachmentDb

      storage.L = storage.layout(tmp_path)
      runtime = CareerCrewRuntime()
      runtime._initialized = True
      runtime.attachment_store = AttachmentStore(FakeAttachmentDb())
      runtime.settings = type("Settings", (), {})()
      runtime.ingest_pipeline = type("Pipeline", (), {})()
      row = runtime.attachment_store.create(
          "t-1", "u-1", "report.pdf", "u-1/t-1/att-1", "application/pdf", 9,
          attachment_id="att-1",
      )
      path = storage.L.attachments / "u-1" / "t-1" / "att-1"
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_bytes(b"pdf-bytes")
      monkeypatch.setattr(runtime, "extract_document_text", lambda *_: (_ for _ in ()).throw(RuntimeError("MinerU unavailable")))
      monkeypatch.setattr(
          "fitz.open",
          lambda _: FakePdfDocument([FakePdfPage("PDF fallback text")]),
      )

      blocks = runtime.resolve_attachment_blocks("u-1", [{"id": "att-1"}])

      assert blocks == [{
          "id": "att-1", "filename": "report.pdf", "kind": "document",
          "content": "PDF fallback text",
      }]
  ```

  The fixture setup above uses `FakeAttachmentDb`/`AttachmentStore` and redirects `careercrew_api.storage.L` to a temporary layout, so the test verifies the real resolver branch without a real LLM or MinerU process.

- [ ] **Step 2: Run the focused tests and verify they fail**

  Run:

  ```powershell
  $env:PYTHONPATH=(Get-Location).Path
  F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_attachment_context.py -q
  ```

  Expected: FAIL because `describe_image` currently hard-codes `image/png` and PDF resolution reaches the invalid `storage.resolve_under` name before the fallback exists.

- [ ] **Step 3: Implement MIME-aware image descriptions and PDF fallback**

  In `careercrew_api/attachment_context.py`:

  ```python
  def _image_mime(path: str, mime_type: str | None = None) -> str:
      if mime_type and mime_type.startswith("image/"):
          return mime_type
      return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(
          Path(path).suffix.lower(), "application/octet-stream"
      )

  def describe_image(settings, image_path: str, prompt: str = "请详细描述这张图片的内容，并提取其中的文字。", mime_type: str | None = None) -> str:
      import base64
      from pathlib import Path

      image_path_obj = Path(image_path)
      if not image_path_obj.is_file():
          raise AttachmentRejected(f"图片不存在：{image_path}")
      b64 = base64.b64encode(image_path_obj.read_bytes()).decode("ascii")
      image_mime = _image_mime(image_path, mime_type)
      client = OpenAI(base_url=settings.vlm.base_url, api_key=settings.vlm.api_key)
      response = client.chat.completions.create(
          model=settings.vlm.model,
          messages=[{
              "role": "user",
              "content": [
                  {"type": "text", "text": prompt},
                  {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{b64}"}},
              ],
          }],
          temperature=0.3,
          max_tokens=1024,
          timeout=120,
      )
      return (response.choices[0].message.content or "").strip()
  ```

  Add the PyMuPDF fallback with a bounded output:

  ```python
  def extract_pdf_text(path: str) -> str:
      import fitz

      with fitz.open(path) as document:
          return _truncate("\n\n".join(page.get_text("text") for page in document).strip())
  ```

  In `CareerCrewRuntime.resolve_attachment_blocks`, import `extract_pdf_text` together with `describe_image`, use the already-imported `_storage_resolve_under` for the parsed output directory, pass `mime_type=row.get("mime_type")` into `describe_image`, and for `.pdf` catch the MinerU exception then call `extract_pdf_text`. If both PDF parsers fail, append the existing error block.

- [ ] **Step 4: Run the focused tests and verify they pass**

  Run:

  ```powershell
  $env:PYTHONPATH=(Get-Location).Path
  F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_attachment_context.py tests/unit/test_attachment_validation.py -q
  ```

  Expected: PASS, with PDF and JPEG branches covered without network calls.

- [ ] **Step 5: Commit the parser fix**

  ```powershell
  git add -- careercrew_api/attachment_context.py careercrew_api/runtime.py tests/unit/test_attachment_context.py
  git commit -m "fix: resolve pdf and image chat attachments"
  ```

---

### Task 2: Add message attachment metadata and history restoration

**Files:**
- Modify: `careercrew_web/src/types.ts:25-39`
- Modify: `careercrew_web/src/lib/historyRestore.ts:16-77`
- Modify: `careercrew_web/src/components/conversation/UserMessage.tsx:12-134`
- Modify: `careercrew_web/src/components/conversation/TurnSection.tsx:9-35`
- Create: `careercrew_web/src/components/conversation/UserMessage.test.tsx`
- Modify: `careercrew_web/src/lib/historyRestore.test.ts:1-120`

**Interfaces:**
- `MessageAttachment = { id: string; filename: string; sizeBytes?: number; mimeType?: string; kind?: string }`.
- `ChatMessage.attachments?: MessageAttachment[]`.
- `RestoredMessage.attachments?: MessageAttachment[]`.
- `UserMessage` accepts `attachments?: MessageAttachment[]` and renders them above the bubble.
- `TurnSection` accepts `userAttachments?: MessageAttachment[]` and passes them to `UserMessage`.

- [ ] **Step 1: Write failing tests for summary parsing and bubble layout**

  Add a history parser case:

  ```ts
  it("从用户消息 metadata 恢复附件摘要，不带附件正文", () => {
    const rows = [{
      role: "user",
      content: "请分析",
      metadata: {
        attachments: [{ id: "a-1", filename: "report.pdf", kind: "document", content: "secret body" }],
      },
    }]
    expect(parseThreadMessages(rows)).toEqual([{
      role: "user",
      content: "请分析",
      messageId: undefined,
      turnId: undefined,
      runId: undefined,
      metadata: rows[0].metadata,
      attachments: [{ id: "a-1", filename: "report.pdf", kind: "document" }],
      raw: rows[0],
    }])
  })
  ```

  Add a Testing Library case:

  ```tsx
  it("把附件渲染在用户消息气泡上方", () => {
    render(<UserMessage content="请分析" attachments={[{ id: "a-1", filename: "report.pdf", kind: "document" }]} />)
    const attachment = screen.getByTestId("message-attachment")
    const bubble = screen.getByTestId("user-message-bubble")
    expect(attachment.compareDocumentPosition(bubble) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(attachment).toHaveTextContent("report.pdf")
  })
  ```

- [ ] **Step 2: Run the focused frontend tests and verify they fail**

  ```powershell
  cd F:\agent_develop\CareerCrew\careercrew_web
  npm run test -- --run src/lib/historyRestore.test.ts src/components/conversation/UserMessage.test.tsx
  ```

  Expected: FAIL because the restored/message types and attachment DOM do not exist.

- [ ] **Step 3: Add summary extraction and message fields**

  Add a small parser in `historyRestore.ts` that accepts only object entries with a non-empty `id` and filename from `filename` or `original_filename`, and intentionally drops `content`, `text`, and unknown fields. Call it from both `parseThreadMessages` and `parseMemoryEntries` when metadata is available.

  Add the type in `types.ts` and add the optional field to the existing `ChatMessage` interface after `runId`:

  ```ts
  export interface MessageAttachment {
    id: string
    filename: string
    sizeBytes?: number
    mimeType?: string
    kind?: string
  }

  export interface ChatMessage {
    attachments?: MessageAttachment[]
  }
  ```

  Update `UserMessage` to render a right-aligned flex column. Each attachment chip appears before the existing bubble, uses a file/image icon based on `kind`/extension, truncates long filenames, and has `data-testid="message-attachment"`; the original operation row remains attached to the bubble root.

  Update `TurnSection` to pass `userAttachments` through.

- [ ] **Step 4: Run the focused frontend tests and verify they pass**

  ```powershell
  cd F:\agent_develop\CareerCrew\careercrew_web
  npm run test -- --run src/lib/historyRestore.test.ts src/components/conversation/UserMessage.test.tsx
  ```

  Expected: PASS.

- [ ] **Step 5: Commit the shared message changes**

  ```powershell
  git add -- careercrew_web/src/types.ts careercrew_web/src/lib/historyRestore.ts careercrew_web/src/lib/historyRestore.test.ts careercrew_web/src/components/conversation/UserMessage.tsx careercrew_web/src/components/conversation/UserMessage.test.tsx careercrew_web/src/components/conversation/TurnSection.tsx
  git commit -m "feat: show attachment summaries on user turns"
  ```

---

### Task 3: Make AttachmentPicker pending-turn scoped

**Files:**
- Modify: `careercrew_web/src/components/prompt/AttachmentPicker.tsx:39-124`
- Modify: `careercrew_web/src/components/prompt/AttachmentPicker.test.tsx:45-146`

**Interfaces:**
- `AttachmentPickerHandle.pick(): void` remains available.
- Add `AttachmentPickerHandle.clear(): void`.
- `AttachmentPicker` calls `onAttachmentsChange([])` after `clear()`.

- [ ] **Step 1: Write the failing clear test**

  Render the picker with a ref, upload one mocked file, call `ref.current?.clear()`, then assert the chip is absent and the last `onAttachmentsChange` call is `[]`.

  ```tsx
  it("clear 清空当前轮待发送附件", async () => {
    uploadAttachment.mockResolvedValue(att())
    const onChange = vi.fn()
    const handle = createRef<AttachmentPickerHandle>()
    render(<AttachmentPicker ref={handle} threadId="t-1" onAttachmentsChange={onChange} />)
    fireEvent.change(screen.getByTestId("attachment-file-input"), {
      target: { files: [new File(["x"], "报告.pdf", { type: "application/pdf" })] },
    })
    await screen.findByTestId("attachment-chip")
    handle.current?.clear()
    expect(screen.queryByTestId("attachment-chip")).toBeNull()
    expect(onChange).toHaveBeenLastCalledWith([])
  })
  ```

- [ ] **Step 2: Run the picker test and verify it fails**

  ```powershell
  cd F:\agent_develop\CareerCrew\careercrew_web
  npm run test -- --run src/components/prompt/AttachmentPicker.test.tsx
  ```

  Expected: FAIL because `clear()` is not exposed and the component rehydrates the whole server list.

- [ ] **Step 3: Implement pending-only behavior**

  Use `useImperativeHandle` to expose both commands:

  ```ts
  useImperativeHandle(ref, () => ({
    pick: () => { if (!disabled) inputRef.current?.click() },
    clear: () => emit([]),
  }), [disabled, emit])
  ```

  Remove the mount-time `listAttachments(threadId)` refresh used to repopulate pending chips. Reset local attachments and emit `[]` when `threadId` changes. Keep upload, delete, status, save-to-knowledge, and error behavior for files uploaded during the current pending period.

- [ ] **Step 4: Run the picker tests and verify they pass**

  ```powershell
  cd F:\agent_develop\CareerCrew\careercrew_web
  npm run test -- --run src/components/prompt/AttachmentPicker.test.tsx
  ```

  Expected: PASS. Update only tests whose old expectation specifically depends on server-list rehydration.

- [ ] **Step 5: Commit pending picker behavior**

  ```powershell
  git add -- careercrew_web/src/components/prompt/AttachmentPicker.tsx careercrew_web/src/components/prompt/AttachmentPicker.test.tsx
  git commit -m "fix: scope composer attachments to the current turn"
  ```

---

### Task 4: Simplify PromptComposer and wire one-turn attachments across pages

**Files:**
- Modify: `careercrew_web/src/components/prompt/PromptComposer.tsx:1-335`
- Modify: `careercrew_web/src/pages/ChatPage.tsx`
- Modify: `careercrew_web/src/pages/MatcherPage.tsx`
- Modify: `careercrew_web/src/pages/InterviewPage.tsx`
- Modify: `careercrew_web/src/pages/ConsultPage.tsx`
- Modify: `careercrew_web/src/pages/KnowledgePage.tsx`
- Modify: `careercrew_web/src/pages/ResumePage.tsx`

**Interfaces:**
- `PromptComposer` keeps `toolbar?: boolean` and `attachments?: ReactNode`.
- Add `onAddAttachment?: () => void`.
- Remove `activeTool`, `onToolToggle`, `mentions`, and `tools` from `PromptComposerProps`.
- Every page's send handler snapshots `attachments: Attachment[]`, adds `attachments` to the user message, sends `{ id }` refs in the request, then calls `attachRef.current?.clear()` before awaiting the stream.

- [ ] **Step 1: Write a static/component regression test for the composer toolbar**

  Add a `PromptComposer.test.tsx` if no existing test covers it. Render with `toolbar` and `onAddAttachment`, click the button named `添加附件`, and assert the callback fires. Assert `screen.queryByRole("button", { name: "提及资料" })` and `screen.queryByRole("button", { name: /工具/ })` are null.

- [ ] **Step 2: Run the frontend test and verify it fails**

  ```powershell
  cd F:\agent_develop\CareerCrew\careercrew_web
  npm run test -- --run src/components/prompt/PromptComposer.test.tsx
  ```

  Expected: FAIL because the current toolbar still renders all three actions.

- [ ] **Step 3: Reduce PromptComposer to the attachment action**

  Remove `AtSign`, `Wrench`, `activeTool`, `onToolToggle`, `mentions`, and `tools`. Keep the existing `Plus` button and call `onAddAttachment` from it. Keep the existing attachments wrapper above the composer so pending chips remain above the input.

- [ ] **Step 4: Update all six pages and message creation**

  In each page:

  - remove `MentionPicker`, `ToolPicker`, `Mention`, `activeTool`, `mentions`, `tools`, and `handleToolToggle` imports/state;
  - keep `attachRef` and `attachments` state;
  - remove `mentions`/`tools` from request body construction;
  - pass `onAddAttachment={() => attachRef.current?.pick()}`;
  - pass only the AttachmentPicker node to `PromptComposer`;
  - capture `const turnAttachments = attachments` before clearing;
  - add `attachments: toMessageAttachments(turnAttachments)` to the new user message;
  - send `attachments: turnAttachments.map(({ id }) => ({ id }))` when non-empty;
  - call `attachRef.current?.clear()` before the page's `await startStream(currentThreadId, endpoint, body)` call.

  Use a shared helper in `careercrew_web/src/lib/attachments.ts` to avoid six incompatible metadata mappings:

  ```ts
  export function toMessageAttachments(items: Attachment[]): MessageAttachment[] {
    return items.map((item) => ({
      id: item.id,
      filename: item.original_filename,
      sizeBytes: item.size_bytes,
      mimeType: item.mime_type,
    }))
  }
  ```

  When each page maps `restoreHistory` output into `ChatMessage`, copy `r.attachments`; when each page renders `TurnSection`, pass `turn.user.attachments` to `userAttachments`. ChatPage's direct `UserMessage` call passes the same field.

- [ ] **Step 5: Run frontend tests and typecheck/build**

  ```powershell
  cd F:\agent_develop\CareerCrew\careercrew_web
  npm run test -- --run
  npm run build
  ```

  Expected: PASS and a successful TypeScript/Vite build with no references to `MentionPicker`, `ToolPicker`, `activeTool`, `onToolToggle`, or the removed composer props in page files.

- [ ] **Step 6: Commit the composer/page wiring**

  ```powershell
  git add -- careercrew_web/src/components/prompt/PromptComposer.tsx careercrew_web/src/components/prompt/PromptComposer.test.tsx careercrew_web/src/pages/ChatPage.tsx careercrew_web/src/pages/MatcherPage.tsx careercrew_web/src/pages/InterviewPage.tsx careercrew_web/src/pages/ConsultPage.tsx careercrew_web/src/pages/KnowledgePage.tsx careercrew_web/src/pages/ResumePage.tsx careercrew_web/src/lib/attachments.ts careercrew_web/src/components/conversation/TurnSection.tsx
  git commit -m "feat: send attachments with individual turns"
  ```

---

### Task 5: Hide fixed Tooltip during pointer interactions

**Files:**
- Modify: `careercrew_web/src/components/ui/tooltip.tsx:35-127`
- Create: `careercrew_web/src/components/ui/tooltip.test.tsx`

**Interfaces:**
- `Tooltip` public props remain unchanged.
- The wrapper calls the existing `hide()` callback for `pointerdown` before the child drag handler runs.

- [ ] **Step 1: Write the failing Tooltip test**

  ```tsx
  it("pointerdown 时隐藏已经显示的提示气泡", async () => {
    render(<Tooltip label="拖动调整"><button>手柄</button></Tooltip>)
    fireEvent.mouseEnter(screen.getByRole("button", { name: "手柄" }))
    await waitFor(() => expect(screen.getByRole("tooltip")).toBeInTheDocument(), { timeout: 300 })
    fireEvent.pointerDown(screen.getByRole("button", { name: "手柄" }))
    expect(screen.queryByRole("tooltip")).toBeNull()
  })
  ```

- [ ] **Step 2: Run the test and verify it fails**

  ```powershell
  cd F:\agent_develop\CareerCrew\careercrew_web
  npm run test -- --run src/components/ui/tooltip.test.tsx
  ```

  Expected: FAIL because Tooltip only hides on mouseleave, click, and scroll.

- [ ] **Step 3: Add pointerdown hiding**

  Add `onPointerDown={hide}` to the Tooltip wrapper span. Do not change fixed coordinates, edge-band placement, or scroll listener behavior.

- [ ] **Step 4: Run the Tooltip test and full frontend verification**

  ```powershell
  cd F:\agent_develop\CareerCrew\careercrew_web
  npm run test -- --run src/components/ui/tooltip.test.tsx src/components/prompt/PromptComposer.test.tsx
  npm run build
  ```

  Expected: PASS and successful build.

- [ ] **Step 5: Commit the Tooltip fix**

  ```powershell
  git add -- careercrew_web/src/components/ui/tooltip.tsx careercrew_web/src/components/ui/tooltip.test.tsx
  git commit -m "fix: hide tooltip while dragging composer"
  ```

---

### Task 6: Run the final scoped regression suite and inspect the diff

**Files:**
- Modify: none unless verification exposes a regression.

**Interfaces:**
- No new interfaces; this task verifies Tasks 1–5 together.

- [ ] **Step 1: Run the complete frontend test suite**

  ```powershell
  cd F:\agent_develop\CareerCrew\careercrew_web
  npm run test -- --run
  ```

  Expected: all Vitest tests PASS.

- [ ] **Step 2: Run the frontend production build**

  ```powershell
  npm run build
  ```

  Expected: `tsc -b` and `vite build` both exit 0.

- [ ] **Step 3: Run targeted backend attachment tests**

  ```powershell
  cd F:\agent_develop\CareerCrew
  $env:PYTHONPATH=(Get-Location).Path
  F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_attachment_context.py tests/unit/test_attachment_validation.py tests/unit/test_attachment_store.py tests/api/test_attachments_api.py -q
  ```

  Expected: all selected tests PASS. If the API test module requires its existing web marker setup, run it with the repository's configured test environment and report any infrastructure skip separately from failures.

- [ ] **Step 4: Inspect the final diff and working-tree boundaries**

  ```powershell
  git diff --check
  git status --short
  git diff HEAD~5 --stat
  ```

  Confirm only the design/plan commits and scoped implementation files changed from this work; leave unrelated pre-existing modifications untouched.

- [ ] **Step 5: Commit any final scoped correction**

  ```powershell
  git add -- <only-files-verified-as-part-of-this-task>
  git commit -m "test: verify one-turn attachment workflow"
  ```
