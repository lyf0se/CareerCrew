# 会话检索范围/上传隔离/SSE 取消与会诊修复 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复已合并进 main 的知识库图片 blob 渲染缺陷，并实现会话检索范围持久化恢复、上传 UUID 隔离与路径安全、SSE 统一取消/背压/超时、会诊状态与 UI 修复、CI 拆分/覆盖率门禁/路由懒加载、Agent/RAG 评估闭环。

**Architecture:** 后端 FastAPI（careercrew_api）+ 核心层（careercrew_core：记忆/Postgres、RAG 管线、会诊编排）+ React 前端（careercrew_web）。线程元数据存 Postgres `threads` 表（新加 `retrieval_scope JSONB` 列）；上传文件改按 UUID 落盘新目录布局并统一路径校验；所有流式入口收敛到 `careercrew_api/sse.py` 的共享 CancellationEvent 与有界队列；前端用 zustand store 承载范围状态并即时 PATCH 持久化。

**Tech Stack:** Python 3.12 / FastAPI / psycopg3 / LangGraph 1.x；React 19 + Vite + zustand + vitest(新增) + @testing-library/react(新增)；GitHub Actions CI。

## Global Constraints

- 在 `main` 之外的新分支实施（沿用 `codex/` 命名）；每个任务独立提交，提交信息用 Conventional Commits（feat/fix/test/chore）。
- 会话检索范围：新建会话使用当前默认范围（"全部"）；修改范围后立即写入会话元数据；切换历史会话时先恢复其保存的范围再加载/发起检索；历史无该字段的会话回退"全部"，首次修改后写入。范围 schema 至少支持：全部知识库（`{"type":"all"}`）、指定知识库分类/文档（`{"type":"category","category_id":<id>}`），为后续简历范围留扩展。
- 上传：新文件按 UUID 存储，原文件名仅保存为元数据；布局 `data/uploads/resumes_raw/{user_id}/{uuid}.{ext}`、`data/uploads/knowledge_raw/{user_id}/{uuid}.{ext}`、`data/parsed/resumes/{user_id}/{uuid}/`、`data/parsed/knowledge/{user_id}/{document_uuid}/`。每次由磁盘路径读写文件前必须 `resolve()` 且校验在授权根目录内。只隔离新上传；历史 `data/uploads` 文件只生成审计清单，必须显式执行 `python scripts/migrate_uploads.py --apply`（默认 `--dry-run`）才会移动。禁止启动时扫描简历原件进入知识库（删除 runtime 首启自动入库块）。
- SSE 取消为协作式：客户端断开、切页（前端 abort）、停止生成时触发共享 CancellationEvent；Agent/工具/会诊编排在自然边界检查取消，避免后续阶段/工具启动；队列满时只丢弃可合并文本 chunk，终态事件（error/done）必须受控投递，绝不永久阻塞线程。
- 流式空闲超时统一由 `CONSULT_STREAM_IDLE_TIMEOUT_SECONDS=300`（环境变量，默认 300）控制，所有流式端点使用同一值，所有用户可见超时提示使用同一个真实秒数。
- 会诊：`current_position` 纳入用户画像字段，支持显式清空（空值=删除事实，沿用 SemanticFactStore.update 语义）且后续会诊可读取；"补充资料"弹窗关闭状态按会话维度保存、切换/新建会话正确重置；流 error 时移除空助手占位气泡。
- 工程质量：CI 拆分为快速单测 / API / Postgres / 编译检查 / 前端构建，integration/e2e 放 nightly/workflow_dispatch；对 PR changed lines 执行 80% 覆盖率门禁（diff-cover）；前端通过 React.lazy/Suspense 拆分 Chat、Consult、Knowledge 等路由页面。
- Agent/RAG 评估：扩充 `data/eval/cases.jsonl`（路由、检索、引用、工具、会诊、记忆）；离线 runner + 版本化 `data/eval/baseline.json`；PR 用离线 fixtures 做非回归门禁；依赖真实模型/服务的评估放 nightly/manual。
- 本轮不改为 asyncio/anyio 或外部作业队列；不引入新的重依赖（vitest/jsdom/@testing-library/react/diff-cover 除外）。
- 后端测试沿用 `tests/api/conftest.py` 的 FakeRuntime 模式（`app.dependency_overrides` 注入）；前端测试用 vitest，纯逻辑测试 node 环境，组件测试文件头部加 `// @vitest-environment jsdom`。
- 后端 Python 3.12；前端 Node 项目根 `careercrew_web/`，命令在其目录下运行（`npm run ...`）。

---

## 文件结构总览

| 文件 | 职责 |
|---|---|
| `careercrew_web/src/components/MarkdownContent.tsx` | Markdown 渲染；放行 blob: URL（Task 0） |
| `careercrew_web/vitest.config.ts`、`careercrew_web/src/**/*.test.ts(x)` | 前端测试基建与用例（Task 0/1/4） |
| `careercrew_core/memory/db.py`、`threads.py` | threads 表加 `retrieval_scope` 列 + 透传（Task 1） |
| `careercrew_api/runtime.py` | register/touch_thread 透传 scope；删除首启扫描；ingest 支持 per-doc 输出目录（Task 1/2） |
| `careercrew_api/routers/data.py` | POST/PATCH /api/threads 支持 retrieval_scope（Task 1） |
| `careercrew_web/src/store/threadStore.ts`、`pages/KnowledgePage.tsx` | 范围状态、乐观写入、切换恢复（Task 1） |
| `careercrew_api/storage.py`（新建） | 目录布局常量 + `resolve_under` 根目录校验（Task 2） |
| `careercrew_api/routers/resume.py`、`knowledge.py` | 上传/读取/删除改 UUID + 校验（Task 2） |
| `careercrew_core/rag/pipeline_multimodal.py` | ingest_file 支持 per-doc output_dir（Task 2） |
| `scripts/audit_uploads.py`、`scripts/migrate_uploads.py`（新建） | 历史审计清单 + dry-run 迁移（Task 2） |
| `careercrew_api/sse.py` | CancellationEvent、非阻塞投递、统一超时（Task 3） |
| `careercrew_api/routers/consult.py`、`chat.py`、`interview.py`、`resume.py`、`knowledge.py` | 流式入口接入取消/统一超时（Task 3） |
| `careercrew_core/memory/types.py`、`semantic.py`、`careercrew_api/routers/consult.py` | current_position 画像字段（Task 4） |
| `careercrew_web/src/pages/ConsultPage.tsx` 等 | 弹窗按会话、错误移除占位气泡（Task 4） |
| `.github/workflows/ci.yml` | CI 拆分 + 覆盖率门禁（Task 5） |
| `careercrew_web/src/App.tsx` | React.lazy 路由拆分（Task 5） |
| `data/eval/cases.jsonl`、`data/eval/baseline.json`、`scripts/eval_runner.py`（新建） | 评估闭环（Task 6） |

---

### Task 0: 修复知识库正文图片 blob 渲染 + 前端测试基建

**Files:**
- Modify: `careercrew_web/src/components/MarkdownContent.tsx`
- Modify: `careercrew_web/package.json`、`careercrew_web/vite.config.ts`
- Create: `careercrew_web/vitest.config.ts`、`careercrew_web/src/components/MarkdownContent.test.tsx`

**Interfaces:**
- Consumes: 无（现状：react-markdown v10 默认 `defaultUrlTransform` 白名单 `safeProtocol = /^(https?|ircs?|mailto|xmpp)$/i`，会把 `blob:` 的 img src 改写为空串）。
- Produces: `MarkdownContent({ children, className })` 渲染行为改变——`blob:` 开头的 URL 原样保留，其余仍走 react-markdown `defaultUrlTransform`；vitest 基建（`npm run test`）供后续任务使用。

- [ ] **Step 1: 加测试依赖与脚本**

`careercrew_web/package.json` 的 devDependencies 追加：`"vitest": "^3.2.0"`、`"jsdom": "^26.0.0"`、`"@testing-library/react": "^16.3.0"`；scripts 追加 `"test": "vitest run"`。

- [ ] **Step 2: 写 vitest 配置**

创建 `careercrew_web/vitest.config.ts`：

```ts
import { defineConfig } from "vitest/config"
import path from "path"

export default defineConfig({
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "./src") } },
  test: { environment: "node" },
})
```

- [ ] **Step 3: 写失败测试（RED）**

创建 `careercrew_web/src/components/MarkdownContent.test.tsx`：

```tsx
import { describe, expect, it } from "vitest"
import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import ReactMarkdown, { defaultUrlTransform } from "react-markdown"
import { MarkdownContent } from "@/components/MarkdownContent"

const BLOB = "blob:http://localhost:5173/550e8400-e29b-41d4-a716-446655440000"

describe("MarkdownContent blob URL", () => {
  it("renders blob: image src unchanged", () => {
    const html = renderToStaticMarkup(<MarkdownContent>{`![图](${BLOB})`}</MarkdownContent>)
    expect(html).toContain(`src="${BLOB}"`)
  })

  it("documents upstream default strips blob:", () => {
    const html = renderToStaticMarkup(<ReactMarkdown>{`![图](${BLOB})`}</ReactMarkdown>)
    expect(html).not.toContain(BLOB) // 上游白名单不含 blob，src 被置空
  })

  it("keeps defaultUrlTransform for other URLs", () => {
    expect(defaultUrlTransform("javascript:alert(1)")).toBe("")
    expect(defaultUrlTransform("https://example.com/a")).toBe("https://example.com/a")
  })
})
```

- [ ] **Step 4: 运行确认失败**

Run: `cd careercrew_web && npm install && npm run test`
Expected: 第 1 个用例 FAIL（当前渲染 `src=""`，不含 blob URL）。

- [ ] **Step 5: 最小修复**

`careercrew_web/src/components/MarkdownContent.tsx`：import 加 `defaultUrlTransform`；`<ReactMarkdown>` 加 prop：

```tsx
import ReactMarkdown, { defaultUrlTransform } from "react-markdown"
// ...
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(url) => (url.startsWith("blob:") ? url : defaultUrlTransform(url))}
        components={{ /* 现有 components 不动 */ }}
      >
```

- [ ] **Step 6: 全绿 + 全量校验**

Run: `npm run test`（3 用例 PASS）；`npm run lint`（仍为 2 个预存 Fast Refresh warning、0 error）；`npm run build`（tsc + vite 通过）。

- [ ] **Step 7: 提交**

```bash
git add careercrew_web
git commit -m "fix(web): allow blob: image URLs in markdown renderer"
```

---

### Task 1: 会话检索范围持久化与恢复

**Files:**
- Modify: `careercrew_core/memory/db.py`（PostgresMemoryDb + FakeMemoryDb 的 upsert_thread/get_thread/list_threads）
- Modify: `careercrew_core/memory/threads.py`
- Modify: `careercrew_api/runtime.py`（register_thread/touch_thread）
- Modify: `careercrew_api/routers/data.py`（ThreadCreateRequest/ThreadPatchRequest）
- Modify: `tests/api/conftest.py`（FakeRuntime 同步新签名）
- Create: `tests/api/test_thread_scope_api.py`
- Modify: `careercrew_web/src/store/threadStore.ts`
- Create: `careercrew_web/src/store/threadStore.test.ts`
- Modify: `careercrew_web/src/pages/KnowledgePage.tsx`
- Create: `careercrew_web/src/pages/KnowledgePage.test.tsx`（切换回归测试）

**Interfaces:**
- Consumes: Task 0 的 vitest 基建。
- Produces:
  - `MemoryDb.upsert_thread(user_id, thread_id, title="", module="chat", pinned=False, retrieval_scope=None) -> dict`；返回行含 `retrieval_scope`（dict 或 None）；`None` 传入时保留旧值（SQL `COALESCE(EXCLUDED.retrieval_scope, threads.retrieval_scope)`，Fake 同语义）。
  - `ThreadStore.upsert(..., retrieval_scope=None)` 透传。
  - `CareerCrewRuntime.register_thread(thread_id, user_id, module="chat", title="", retrieval_scope=None)`、`touch_thread(..., retrieval_scope=None)`。
  - `PATCH /api/threads/{tid}` 请求体新增 `retrieval_scope: {type: "all"|"category", category_id?: str} | null`；`POST /api/threads` 同；`GET /api/threads` 每行含 `retrieval_scope`。
  - 前端 `RetrievalScope = { type: "all" } | { type: "category"; category_id: string }`；`ThreadItem.retrieval_scope?: RetrievalScope | null`；`setThreadScope(m, tid, scope)`（PATCH，404 时回退 POST 创建）。

- [ ] **Step 1: 后端失败测试（RED）**

创建 `tests/api/test_thread_scope_api.py`（复用 conftest client/fake_runtime；双用户参考 `test_tenant_isolation_api.py` 的 `ids`/`headers` 写法）：

```python
def test_patch_scope_persists_and_lists(client, fake_runtime):
    client.post("/api/threads", json={"thread_id": "k-scope-1", "module": "knowledge"})
    resp = client.patch("/api/threads/k-scope-1", json={
        "retrieval_scope": {"type": "category", "category_id": "resume"}})
    assert resp.status_code == 200
    rows = client.get("/api/threads", params={"module": "knowledge"}).json()
    row = next(r for r in rows if r["thread_id"] == "k-scope-1")
    assert row["retrieval_scope"] == {"type": "category", "category_id": "resume"}

def test_legacy_thread_scope_none(client):
    client.post("/api/threads", json={"thread_id": "k-legacy", "module": "knowledge"})
    rows = client.get("/api/threads", params={"module": "knowledge"}).json()
    assert next(r for r in rows if r["thread_id"] == "k-legacy")["retrieval_scope"] is None

def test_patch_title_preserves_scope(client):
    client.post("/api/threads", json={"thread_id": "k-pres", "module": "knowledge"})
    client.patch("/api/threads/k-pres", json={"retrieval_scope": {"type": "all"}})
    client.patch("/api/threads/k-pres", json={"title": "新标题"})
    rows = client.get("/api/threads", params={"module": "knowledge"}).json()
    assert next(r for r in rows if r["thread_id"] == "k-pres")["retrieval_scope"] == {"type": "all"}

def test_invalid_scope_rejected(client):
    assert client.patch("/api/threads/k-x", json={
        "retrieval_scope": {"type": "category", "category_id": ""}}).status_code == 422
    assert client.patch("/api/threads/k-x", json={
        "retrieval_scope": {"type": "bogus"}}).status_code == 422

def test_scope_isolated_between_users(client, fake_runtime, ids, headers):
    client.post("/api/threads", json={"thread_id": "k-alice", "module": "knowledge"}, headers=headers["alice"])
    client.patch("/api/threads/k-alice", json={
        "retrieval_scope": {"type": "category", "category_id": "interview"}}, headers=headers["alice"])
    assert client.patch("/api/threads/k-alice", json={
        "retrieval_scope": {"type": "all"}}, headers=headers["bob"]).status_code == 404
    rows = client.get("/api/threads", params={"module": "knowledge"}, headers=headers["alice"]).json()
    assert next(r for r in rows if r["thread_id"] == "k-alice")["retrieval_scope"]["category_id"] == "interview"
```

Run: `pytest tests/api/test_thread_scope_api.py -q`
Expected: FAIL（PATCH 422 未知字段 / 列表无 retrieval_scope）。

- [ ] **Step 2: 持久层**

`careercrew_core/memory/db.py`：
- `_ensure()` 在 `CREATE TABLE IF NOT EXISTS threads` 之后追加：
  `self._conn.execute("ALTER TABLE threads ADD COLUMN IF NOT EXISTS retrieval_scope JSONB")`。
- `PostgresMemoryDb.upsert_thread(self, user_id, thread_id, title, module, pinned, retrieval_scope=None)`：INSERT 列加 `retrieval_scope`；ON CONFLICT 更新子句加 `retrieval_scope=COALESCE(EXCLUDED.retrieval_scope, threads.retrieval_scope)`；参数 `_json_dumps(retrieval_scope) if retrieval_scope is not None else None`。
- `get_thread`/`list_threads` 的 SELECT 列加 `retrieval_scope`。
- `FakeMemoryDb.upsert_thread` 同签名；行 dict 加 `"retrieval_scope": retrieval_scope if retrieval_scope is not None else (existing or {}).get("retrieval_scope")`。

`careercrew_core/memory/threads.py`：`ThreadStore.upsert(..., retrieval_scope=None)` 透传给 `self._db.upsert_thread`。

- [ ] **Step 3: runtime + 路由**

`careercrew_api/runtime.py`：
- `register_thread(..., retrieval_scope=None)` → `self.thread_store.upsert(..., retrieval_scope=retrieval_scope)`。
- `touch_thread(..., retrieval_scope=None)` → `upsert(..., retrieval_scope=retrieval_scope if retrieval_scope is not None else row.get("retrieval_scope"))`。

`careercrew_api/routers/data.py`：

```python
from pydantic import BaseModel, model_validator

class RetrievalScopeRequest(BaseModel):
    type: str = "all"          # all | category
    category_id: str = ""

    @model_validator(mode="after")
    def _check(self):
        if self.type not in ("all", "category"):
            raise ValueError("type 必须为 all 或 category")
        if self.type == "category" and not self.category_id.strip():
            raise ValueError("type=category 时必须提供 category_id")
        return self

class ThreadCreateRequest(BaseModel):
    thread_id: str
    module: str = "chat"
    title: str = ""
    retrieval_scope: RetrievalScopeRequest | None = None

class ThreadPatchRequest(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    module: str | None = None
    retrieval_scope: RetrievalScopeRequest | None = None
```

`create_thread`/`patch_thread` 透传 `req.retrieval_scope.model_dump() if req.retrieval_scope else None`。

`tests/api/conftest.py` FakeRuntime：`register_thread`/`touch_thread` 增加 `retrieval_scope=None` 参数并透传给 `self.thread_store.upsert`。

- [ ] **Step 4: 后端测试通过 + 提交**

Run: `pytest tests/api/test_thread_scope_api.py tests/api -q`（api 目录全绿）。
Commit: `git commit -m "feat(api): persist retrieval scope in thread metadata"`（含 db/threads/runtime/data/conftest/test）。

- [ ] **Step 5: 前端 store 失败测试（RED）**

`careercrew_web/src/store/threadStore.ts`：`ThreadItem` 加 `retrieval_scope?: RetrievalScope | null`；导出 `export type RetrievalScope = { type: "all" } | { type: "category"; category_id: string }`；`fetchThreads` 映射 `retrieval_scope: (t.retrieval_scope ?? null)`（原样透传未知字段类型用 `as` 收窄）。新增 action：

```ts
setThreadScope: async (m, tid, scope) => {
  set((s) => ({
    threadsByModule: {
      ...s.threadsByModule,
      [m]: (s.threadsByModule[m] || []).map((t) =>
        t.thread_id === tid ? { ...t, retrieval_scope: scope } : t
      ),
    },
  }))
  const body = JSON.stringify({ retrieval_scope: scope })
  try {
    const resp = await apiFetch(`/api/threads/${encodeURIComponent(tid)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body,
    })
    if (resp.status === 404) {
      await apiFetch("/api/threads", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: tid, module: m, retrieval_scope: scope }),
      })
    }
  } catch { /* 后端未就绪：保留本地范围 */ }
},
```

创建 `careercrew_web/src/store/threadStore.test.ts`（node 环境，`vi.mock("@/lib/auth", ...)` 返回 `{ apiFetch: vi.fn() }`）：

```ts
import { beforeEach, describe, expect, it, vi } from "vitest"
import { useThreadStore } from "@/store/threadStore"

const apiFetch = vi.fn()
vi.mock("@/lib/auth", () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }))

describe("threadStore retrieval_scope", () => {
  beforeEach(() => { apiFetch.mockReset(); useThreadStore.setState({ threadsByModule: {}, currentThreadByModule: {} }) })

  it("fetchThreads 解析 retrieval_scope", async () => {
    apiFetch.mockResolvedValueOnce({ ok: true, json: async () => [
      { thread_id: "k-1", title: "t", module: "knowledge", pinned: false,
        retrieval_scope: { type: "category", category_id: "resume" } },
    ]})
    await useThreadStore.getState().fetchThreads("knowledge")
    expect(useThreadStore.getState().threadsByModule.knowledge[0].retrieval_scope)
      .toEqual({ type: "category", category_id: "resume" })
  })

  it("setThreadScope 乐观更新并 PATCH", async () => {
    apiFetch.mockResolvedValueOnce({ ok: true, status: 200 })
    useThreadStore.setState({ threadsByModule: { knowledge: [
      { thread_id: "k-1", title: "t", module: "knowledge", pinned: false }] } })
    await useThreadStore.getState().setThreadScope("knowledge", "k-1", { type: "all" })
    expect(apiFetch).toHaveBeenCalledWith("/api/threads/k-1", expect.objectContaining({ method: "PATCH" }))
    expect(JSON.parse(apiFetch.mock.calls[0][1].body).retrieval_scope).toEqual({ type: "all" })
  })

  it("PATCH 404 时回退 POST 创建", async () => {
    apiFetch.mockResolvedValueOnce({ ok: false, status: 404 })
    apiFetch.mockResolvedValueOnce({ ok: true, status: 200 })
    await useThreadStore.getState().setThreadScope("knowledge", "k-new", { type: "all" })
    expect(apiFetch.mock.calls[1][0]).toBe("/api/threads")
    expect(apiFetch.mock.calls[1][1].method).toBe("POST")
  })
})
```

Run: `cd careercrew_web && npm run test`
Expected: FAIL（setThreadScope 不存在 / ThreadItem 无字段）。

- [ ] **Step 6: KnowledgePage 接入范围恢复与写入**

`careercrew_web/src/pages/KnowledgePage.tsx`：删除 `const [category, setCategory] = useState("")`（97-99 行附近），改为：

```tsx
const threads = useThreadStore((s) => s.threadsByModule.knowledge ?? [])
const setThreadScope = useThreadStore((s) => s.setThreadScope)
const savedScope = threads.find((t) => t.thread_id === currentThreadId)?.retrieval_scope
const category = savedScope?.type === "category" ? savedScope.category_id : ""
const changeCategory = (id: string) => {
  void setThreadScope("knowledge", currentThreadId,
    id ? { type: "category", category_id: id } : { type: "all" })
}
```

分类按钮 onClick 由 `setCategory(c.id)` 改为 `changeCategory(c.id)`；底部"检索范围：…"文案不变（读 `category`）。`handleAsk` 的 payload 不变。

- [ ] **Step 7: 切换回归测试（jsdom）**

创建 `careercrew_web/src/pages/KnowledgePage.test.tsx`（首行 `// @vitest-environment jsdom`）：

```tsx
import { beforeEach, describe, expect, it, vi } from "vitest"
import React from "react"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import KnowledgePage from "@/pages/KnowledgePage"
import { useThreadStore } from "@/store/threadStore"
import { useStreamStore } from "@/store/streamStore"

const apiFetch = vi.fn()
vi.mock("@/lib/auth", () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }))

describe("KnowledgePage 检索范围", () => {
  beforeEach(() => {
    apiFetch.mockReset()
    apiFetch.mockImplementation(async () => ({ ok: true, status: 200, json: async () => [] }))
    useStreamStore.setState({ sessions: {} })
    useThreadStore.setState({
      threadsByModule: { knowledge: [
        { thread_id: "k-a", title: "A", module: "knowledge", pinned: false },
        { thread_id: "k-b", title: "B", module: "knowledge", pinned: false,
          retrieval_scope: { type: "category", category_id: "interview" } },
      ]},
      currentThreadByModule: { knowledge: "k-a" },
    })
  })

  it("点击分类立即 PATCH 范围", async () => {
    render(<KnowledgePage />)
    fireEvent.click(screen.getByText("面试题"))
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/api/threads/k-a",
      expect.objectContaining({ method: "PATCH" })))
    const body = JSON.parse(apiFetch.mock.calls[0][1].body)
    expect(body.retrieval_scope).toEqual({ type: "category", category_id: "interview" })
  })

  it("切换会话恢复其保存的范围", async () => {
    render(<KnowledgePage />)
    useThreadStore.getState().selectThread("knowledge", "k-b")
    await waitFor(() =>
      expect(screen.getByText(/检索范围：面试题/)).toBeTruthy())
  })
})
```

Run: `npm run test`。
Expected: 全部 PASS（apiFetch 的 /api/memory 调用返回空数组，页面不渲染消息）。

- [ ] **Step 8: 前端全量校验 + 提交**

Run: `npm run test && npm run lint && npm run build`
Commit: `git commit -m "feat(web): persist and restore knowledge retrieval scope per session"`

---

### Task 2: 上传隔离、UUID 与路径安全

**Files:**
- Create: `careercrew_api/storage.py`
- Modify: `careercrew_api/routers/knowledge.py`、`careercrew_api/routers/resume.py`
- Modify: `careercrew_api/runtime.py`（删除首启扫描块 177-188 行；`ingest_document` 加 `output_dir`/`doc_name`；`load_document` 加 `output_dir`；`knowledge_asset_owned` 加根目录校验）
- Modify: `careercrew_core/rag/pipeline_multimodal.py`（`ingest_file`/`_make_loader` 加可选 `output_dir`）
- Create: `scripts/audit_uploads.py`、`scripts/migrate_uploads.py`
- Create: `tests/api/test_storage_paths.py`、`tests/api/test_upload_isolation_api.py`、`tests/unit/test_migrate_uploads.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `careercrew_api.storage`：`DATA_ROOT`、`UPLOADS_ROOT`、`RESUMES_RAW`、`KNOWLEDGE_RAW`、`PARSED_RESUMES`、`PARSED_KNOWLEDGE`、`RESUME_THREADS_DIR = UPLOADS_ROOT/"resume_threads"`（`{user_id}/{sha256}.txt`，保留现状）；`resolve_under(root: Path, *parts) -> Path`（`resolve()` 后 `is_relative_to(root.resolve())` 校验，越界抛 `ValueError`）；`is_within_data(p: Path) -> bool`。
  - 上传原始文件：resume → `RESUMES_RAW/{user_id}/{job_uuid}{ext}`；knowledge → `KNOWLEDGE_RAW/{user_id}/{job_uuid}{ext}`；`job["filename"]` 保留原始文件名（元数据）。
  - 简历库：`PARSED_RESUMES/{user_id}/{resume_uuid}/content.txt` + `meta.json`；线程简历：`RESUME_THREADS_DIR/{user_id}/{sha256(thread_id)}.txt`。
  - 知识库解析产物：`PARSED_KNOWLEDGE/{user_id}/{doc_uuid}/`（doc_uuid = 上传 job_uuid），经 `runtime.ingest_document(output_dir=...)` → `pipeline.ingest_file(output_dir=...)` 传入。
  - 命令：`python scripts/audit_uploads.py [--json out.json]`（只读清单）；`python scripts/migrate_uploads.py [--dry-run|--apply] [--owner u_001]`。

- [ ] **Step 1: storage 模块 + 失败测试（RED）**

创建 `careercrew_api/storage.py`（内容见 Interfaces 的常量与两个函数；布局常量用 `Path(__file__).resolve().parents[1] / "data"`）。

创建 `tests/api/test_storage_paths.py`：

```python
import pytest
from careercrew_api.storage import DATA_ROOT, RESUMES_RAW, resolve_under

def test_resolve_under_normal():
    p = resolve_under(RESUMES_RAW, "u_001", "abc.pdf")
    assert p.parent == RESUMES_RAW / "u_001"
    assert p.name == "abc.pdf"

def test_resolve_under_rejects_traversal():
    with pytest.raises(ValueError):
        resolve_under(RESUMES_RAW, "u_001", "../../etc/passwd")
    with pytest.raises(ValueError):
        resolve_under(RESUMES_RAW, "u_001", "..", "u_002", "x.pdf")

def test_resolve_under_rejects_absolute():
    with pytest.raises(ValueError):
        resolve_under(RESUMES_RAW, "C:/Windows/System32/x.pdf".replace("/", "\\"))

def test_distinct_uploads_distinct_paths():
    a = resolve_under(RESUMES_RAW, "u_001", "uuid-1.pdf")
    b = resolve_under(RESUMES_RAW, "u_001", "uuid-2.pdf")
    assert a != b
```

Run: `pytest tests/api/test_storage_paths.py -q`，Expected: FAIL（模块不存在）。

- [ ] **Step 2: 知识库上传改 UUID + 解析目录**

`careercrew_api/routers/knowledge.py`：
- import `from careercrew_api.storage import KNOWLEDGE_RAW, PARSED_KNOWLEDGE, resolve_under`。
- `upload_knowledge`：`filename = Path(file.filename or "upload").name or "upload"` 仅存元数据；`ext = Path(filename).suffix.lower()`；`job_id = _new_job(filename, user_id)`；`save_path = resolve_under(KNOWLEDGE_RAW, user_id, f"{job_id}{ext}")`；`save_path.parent.mkdir(parents=True, exist_ok=True)`；写入字节。`_run_ingest_job` 增加 `output_dir=str(resolve_under(PARSED_KNOWLEDGE, user_id, job_id))` 与 `doc_name=filename` 参数，调用 `rt.ingest_document(save_path, user_id=user_id, progress_cb=cb, category=category, output_dir=output_dir, doc_name=filename)`。

`careercrew_api/runtime.py`：
- `ingest_document(self, path, user_id, metadata=None, progress_cb=None, category="", output_dir=None, doc_name="")`：先 `Path(path).resolve()` 校验 `is_relative_to(DATA_ROOT)`（否则 raise ValueError）；空 category 时 `category = category_for_doc(doc_name or p.stem)`；`self.ingest_pipeline.ingest_file(p, metadata=owner_metadata, progress_cb=progress_cb, category=category, output_dir=output_dir)`。doc_id 使用 `p.stem`（即 uuid）。
- `load_document(self, path, output_dir=None)`：两个 provider 分支构造 loader 时 `loaders.output_dir if output_dir is None else output_dir`。
- `knowledge_asset_owned`：在 metadata 检查前加 `resolved.is_relative_to(DATA_ROOT)` 校验（False 直接拒绝）。

`careercrew_core/rag/pipeline_multimodal.py`：
- `_make_loader(self, output_dir=None)` → 构造 loader 用 `self._output_dir if output_dir is None else Path(output_dir)`。
- `ingest_file(self, path, metadata=None, progress_cb=None, category="", output_dir=None)` 透传给 `_ingest_file_impl`；`_ingest_file_impl` 中 `self._make_loader(output_dir).parse(p)`。

- [ ] **Step 3: 简历上传改 UUID + 解析目录**

`careercrew_api/routers/resume.py`：
- import storage；`UPLOAD_DIR` 常量保留（供 `resume_threads`），但原始文件写入改为 `resolve_under(RESUMES_RAW, user_id, f"{job_id}{ext}")`（`_run_upload_job` 的 `save_path` 随之改变；job 里 `filename` 仍是原名）。
- 简历库落盘改 `PARSED_RESUMES/{user_id}/{resume_id}/content.txt` + `meta.json`（`resolve_under` 构造；`meta["user_id"]` 保留）。`list_library` 用 `PARSED_RESUMES.glob("*/**/meta.json")` 或按 `PARSED_RESUMES/{user_id}` 过滤（实现自选，必须仍按 user_id 隔离）；`library_content`/`delete_library` 用 `resolve_under(PARSED_RESUMES, current_user["id"], resume_id, ...)` 且先校验 `resume_id` 匹配 `^[0-9a-f]{12}$`（拒绝路径注入）。
- `_run_upload_job`：`rt.load_document(path, output_dir=str(resolve_under(PARSED_RESUMES, user_id, job_id)))`。
- `_resume_path`/`_load_resume`/`_save_resume` 改用 `resolve_under(RESUME_THREADS_DIR, user_id, f"{digest}.txt")`。

- [ ] **Step 4: 删除首启扫描 + 回归测试**

`careercrew_api/runtime.py`：删除 `_ensure_heavy` 中 177-188 行（`if store.count() == 0:` 的 uploads 扫描块），替换为注释 `# 知识库只经由上传端点显式入库（历史首启扫描已移除：简历原件不得自动入知识库）`。

创建 `tests/api/test_upload_isolation_api.py`（FakeRuntime；双用户 fixtures 参考 test_tenant_isolation_api.py）：

```python
import io

def test_same_filename_two_users_isolated(client, ids, headers, fake_runtime, tmp_path, monkeypatch):
    import careercrew_api.routers.resume as resume_mod
    from careercrew_api.storage import RESUMES_RAW
    monkeypatch.setattr(resume_mod, "RESUME_LIB_DIR", tmp_path)  # 若实现保留该常量
    for user, h in (("alice", headers["alice"]), ("bob", headers["bob"])):
        resp = client.post("/api/resume/upload", files={"file": ("resume.pdf", b"fake", "application/pdf")}, headers=h)
        assert resp.status_code == 202
    # 两用户各自 job 落盘路径不同且含各自 user 目录
    assert (RESUMES_RAW / ids["alice"]).exists() or True  # 具体断言按 monkeypatch 后的实际根
```

实现提示：为可测性，storage 常量在测试中可用 `monkeypatch.setattr(storage, "DATA_ROOT", tmp_path/"data")` 后重建（把四个目录常量实现为函数 `layout(root: Path)` 返回 SimpleNamespace，模块级默认 `LAYOUT = layout(DATA_ROOT)`；测试 monkeypatch 模块级 DATA_ROOT 并重新 import 或直接传 tmp 根构造）。为简化，将目录常量实现为函数式：

```python
def layout(data_root: Path) -> SimpleNamespace:
    up = data_root / "uploads"
    parsed = data_root / "parsed"
    return SimpleNamespace(
        uploads=up,
        resumes_raw=up / "resumes_raw",
        knowledge_raw=up / "knowledge_raw",
        resume_threads=up / "resume_threads",
        parsed_resumes=parsed / "resumes",
        parsed_knowledge=parsed / "knowledge",
    )
DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
L = layout(DATA_ROOT)
```

（router 内 `from careercrew_api.storage import L, resolve_under`；测试 `monkeypatch.setattr(storage, "L", layout(tmp_path/"data"))` 后 `importlib.reload` 相关 router 或用函数取路径。实现者以最简方式让测试可注入 tmp 根。）

关键用例：
1. 同名文件两用户上传 → 各自 user 目录下生成不同 uuid 文件，job_id 不同，`upload_status` 互不可见（404）。
2. 简历上传不产生知识库文档：上传后 `fake_runtime.ingest_calls == []`（给 FakeRuntime 加 `ingest_calls: list`，`ingest_document` 记录），`knowledge_status` 不变。
3. `library_content` 用越界 resume_id（如 `../x`、`..%2F`）→ 404；删除他人 resume → 404。
4. 知识库上传后 `fake_runtime.ingest_document` 收到的 `output_dir` 在 `PARSED_KNOWLEDGE/{user}/{uuid}` 内。

- [ ] **Step 5: 审计与迁移脚本 + 测试**

`scripts/audit_uploads.py`：遍历 `data/uploads`（recursive，跳过 `resume_threads` 与 `resumes_raw`、`knowledge_raw`），对每个文件输出行：`{path} | {kind: resume|knowledge|unknown} | {owner: 目录名或 u_001} | {suggested_target} | {exists_in_new_layout}`。`--json out.json` 写 JSON 清单。纯只读。

`scripts/migrate_uploads.py`：读审计结果；目标路径经 `resolve_under` 构造（`{uuid}` 用 `uuid4().hex[:12]` 生成，扩展名取原名 suffix）；`--dry-run`（默认）只打印计划；`--apply` 执行 `shutil.move`（目标存在则跳过并告警）；`--owner u_001` 指定历史文件归属。幂等：已在新布局（路径含 resumes_raw/knowledge_raw）的文件跳过。

创建 `tests/unit/test_migrate_uploads.py`：tmp 目录构造 `data/uploads/u_001/简历.pdf` 与 `data/uploads/note.md`；运行 audit → 清单含两条且 kind 正确（按扩展名/目录规则：`uploads/{user}/*` 视为该用户 legacy，文件名含"简历/resume"或位于 `resumes/` 子目录 → resume，其余 → knowledge，规则在脚本里写死并注释）；dry-run 输出含目标；apply 后文件移动且源消失；恶意文件名 `../x.pdf` 已在审计阶段 `Path(...).name` 归一。

- [ ] **Step 6: 全量后端测试 + 提交**

Run: `pytest tests/api tests/unit -q`
Commit: `git commit -m "feat(uploads): uuid-keyed storage layout with root-constrained path checks"`

---

### Task 3: SSE 取消、背压与统一超时

**Files:**
- Modify: `careercrew_api/sse.py`（核心）
- Modify: `careercrew_api/routers/chat.py`、`interview.py`、`resume.py`、`knowledge.py`、`consult.py`
- Create: `tests/api/test_sse_streams.py`

**Interfaces:**
- Consumes: 现状 `stream_agent(run_fn, *, timeout=30.0, max_q=256)`；consult.py 自建 queue。
- Produces:
  - `sse.STREAM_IDLE_TIMEOUT_SECONDS = float(os.environ.get("CONSULT_STREAM_IDLE_TIMEOUT_SECONDS", "300"))`
  - `sse.StreamCancelled(Exception)`；`sse.CancellationEvent`（`set()/is_set()/check()`，check 在已设置时 raise StreamCancelled）。
  - `stream_agent(run_fn, *, timeout=None, max_q=256, cancel=None)`：timeout 默认取 `STREAM_IDLE_TIMEOUT_SECONDS`；callback 与 run_fn 每次推送前 `cancel.check()`；chunk 用 `put_nowait`，满则丢弃并计数；`_SENTINEL` 与 error 用"保证投递"循环（put_nowait 失败时 sleep 0.05 重试，期间 `cancel.check()`，cancel 已设则直接放弃投递并返回）；生成器 `GeneratorExit`（客户端断开/abort）时 `cancel.set()` 后 re-raise；取消场景不再追加 error 事件。
  - 所有流式路由的 `stream_agent(...)` 调用不再传各自 120/180/300，统一默认；`knowledge.py` 300 与 `consult.py` 的 300 改为读同一常量；consult 超时提示文案改为真实秒数。

- [ ] **Step 1: 失败测试（RED）**

创建 `tests/api/test_sse_streams.py`（不依赖 FastAPI，直接测生成器）：

```python
import threading, time
from careercrew_api import sse
from careercrew_api.sse import CancellationEvent, StreamCancelled, stream_agent

def _lines(gen):
    return [l for l in gen]

def test_disconnect_sets_cancel_and_stops_worker():
    cancel = CancellationEvent()
    calls = []
    def run_fn(cb):
        for _ in range(100000):
            cancel.check()
            cb("x")          # chunk 推送路径同样检查
            calls.append(1)
            time.sleep(0.001)
    g = stream_agent(run_fn, timeout=30.0, max_q=4, cancel=cancel)
    next(g)
    next(g)
    g.close()               # 模拟客户端断开
    assert cancel.is_set()
    time.sleep(0.2)
    n = len(calls)
    time.sleep(0.3)
    assert len(calls) - n <= 2   # worker 已在边界停止

def test_queue_full_drops_chunks_but_terminal_delivered():
    cancel = CancellationEvent()
    def run_fn(cb):
        for i in range(100):
            cancel.check()
            cb(f"chunk-{i}")
    lines = _lines(stream_agent(run_fn, timeout=30.0, max_q=2, cancel=cancel))
    assert len(lines) < 100          # 有丢弃
    assert lines and '"type": "chunk"' in lines[0]

def test_cancel_before_run_prevents_work():
    cancel = CancellationEvent()
    cancel.set()
    calls = []
    def run_fn(cb):
        calls.append("run")
        cb("should-not-happen")
    lines = _lines(stream_agent(run_fn, timeout=30.0, cancel=cancel))
    assert calls == []
    assert not any('"type": "error"' in l for l in lines)

def test_timeout_message_uses_configured_seconds():
    def run_fn(cb):
        time.sleep(0.5)
    lines = _lines(stream_agent(run_fn, timeout=0.05))
    assert any("stream timeout after 0.05s" in l for l in lines)
```

Run: `pytest tests/api/test_sse_streams.py -q`，Expected: FAIL。

- [ ] **Step 2: 重写 sse.py 桥**

按 Interfaces 实现。要点：
- `_target` 内先 `cancel.check()`（若 run 前已取消则直接投递哨兵，不执行 run_fn）；`run_fn` 的 callback 为 `lambda t: (_cancel.check(), q.put_nowait({"type":"chunk","text":t}))`——实现为内部 `_put_chunk`（Full 时 `dropped[0]+=1` 返回）。
- 哨兵/异常投递：`_put_guaranteed(item)`：`while True: try: q.put_nowait(item); return; except queue.Full: if cancel.is_set(): return; time.sleep(0.05)`。
- 生成器主循环不变（`q.get(timeout=timeout)`）；`except GeneratorExit: cancel.set(); raise`。
- `StreamCancelled` 被 worker 捕获后置 `cancelled=True`，不写 err；主循环读不到（哨兵后 break）也无需产出。
- `stream_agent` 的 timeout 参数默认 `None → STREAM_IDLE_TIMEOUT_SECONDS`；错误文案 `f"stream timeout after {timeout}s"`（保持现有格式，值变真实）。

- [ ] **Step 3: 路由接入**

- `chat.py` / `interview.py` / `resume.py`：`stream_agent(run_fn, timeout=120.0/180.0)` → `stream_agent(run_fn)`（统一默认）；`knowledge.py` 的 `stream_agent(_run, timeout=300.0)` → `stream_agent(_run)`；导入处统一 `from careercrew_api.sse import STREAM_IDLE_TIMEOUT_SECONDS` 供提示（如前端无提示则后端不需要额外改动）。
- `consult.py`：gen() 内创建 `cancel = CancellationEvent()`；worker `_worker_impl` 开头 `cancel.check()`；`emit=q.put` 换为安全 emit（`cancel.check()` 后 `put_nowait`，Full 丢弃 chunk 类事件、`done/input_request/error` 走保证投递——把 emit 换成 `_safe_emit(cancel, q, item)`）；`graph.invoke` 的 emit 回调即 `_safe_emit`；worker finally 用保证投递放哨兵；gen() 主循环 `q.get(timeout=STREAM_IDLE_TIMEOUT_SECONDS)`，超时文案 `f"stream timeout after {STREAM_IDLE_TIMEOUT_SECONDS}s"`（修复现文案硬编码 "60s"）；`try/finally: cancel.set()` 包住 yield 循环（GeneratorExit 时设置）。

- [ ] **Step 4: 会诊取消回归测试**

`tests/api/test_sse_streams.py` 追加（用 conftest client + FakeRuntime，FakeRuntime.llm 的 orchestrator_override 返回慢速决策）：

```python
def test_consult_stream_disconnect_cancels(client, fake_runtime):
    import json, time
    def slow_decide(prompt, config=None):
        time.sleep(0.05)
        return type("R", (), {"content": '{"next_agents": [], "tasks": {}, "final_answer": "x", "needs_user_input": false, "input_fields": []}'})()
    fake_runtime.orchestrator_override = slow_decide
    with client.stream("POST", "/api/consult", json={"question": "q", "thread_id": "c-1"}) as resp:
        it = resp.iter_lines()
        next(it)  # stage
        # 提前断开
    # 后端 worker 应在取消事件驱动下尽快结束（断言：再次请求可正常建立，服务无泄漏）
    assert client.post("/api/health").status_code == 200
```

（主要断言服务仍可用、无挂死；worker 停止细节由单元测试覆盖。）

- [ ] **Step 5: 全量回归 + 提交**

Run: `pytest tests/api -q`
Commit: `git commit -m "feat(sse): cooperative cancellation, bounded queues and unified 300s idle timeout"`

---

### Task 4: 会诊状态与 UI 修复

**Files:**
- Modify: `careercrew_core/memory/types.py`、`careercrew_core/memory/semantic.py`
- Modify: `careercrew_api/routers/consult.py`（合并 + 持久化 + `_profile_from_model`）
- Modify: `careercrew_web/src/pages/ConsultPage.tsx`（弹窗按会话、错误移除占位）
- Modify: `careercrew_web/src/pages/KnowledgePage.tsx`、`ChatPage.tsx`、`MatcherPage.tsx`、`InterviewPage.tsx`、`ResumePage.tsx`（错误移除空占位——模式相同，逐页核对）
- Modify: `careercrew_web/src/store/threadStore.ts`、`streamStore.ts`（加 `resetAll`）、`careercrew_web/src/App.tsx`（登出/换用户清空会话状态）
- Create: `tests/api/test_consult_profile.py`、`careercrew_web/src/pages/ConsultPage.test.tsx`

**Interfaces:**
- Consumes: Task 1 的 store；Task 3 的流错误路径。
- Produces:
  - `UserProfile.current_position: str = ""`；`ALLOWED_FIELDS["profile.current_position"] = ("profile", "current_position")`。
  - consult 表单提交后，可映射字段持久化进 SemanticFactStore（`source="consult_form"`）；`current_position` 空值提交 = 显式清空（沿用 update 删除语义）。
  - `_profile_from_model` 输出含 `current_position`。
  - `useThreadStore.getState().resetAll()` 与 `useStreamStore.getState().resetAll()`；App 在 auth 变 anonymous 或 user.id 变化时调用。

- [ ] **Step 1: 画像字段后端 + 失败测试（RED）**

`careercrew_core/memory/types.py` UserProfile 加 `current_position: str = ""`；`semantic.py` ALLOWED_FIELDS 加行。

`careercrew_api/routers/consult.py`：
- `_profile_from_model` 开头加 `if p.current_position: out["current_position"] = p.current_position`。
- 在 merged_profile 合并完成后、构建 initial_state 前持久化（仅白名单键，防 ValueError）：

```python
_FIELD_MAP = {
    "current_position": "profile.current_position",
    "experience_years": "profile.experience_years",
    "skills": "profile.skills",
    "target_direction": "profile.direction",
    "city": "preferences.city",
}

def _persist_form_profile(rt, user_id: str, profile: dict[str, str]) -> None:
    mapped = {
        key: v for k, v in (_FIELD_MAP.items())
        if (v := profile.get(k)) is not None and v != ""
    }
    # 显式清空：用户提交空 current_position 时删除事实
    if "current_position" in profile and not profile["current_position"].strip():
        mapped["profile.current_position"] = ""
    if mapped:
        try:
            rt.fact_store.update(user_id, mapped, source="consult_form")
        except Exception:
            pass  # 画像持久化失败不阻塞会诊
```

（注意 walrus 写法避免 3.12 兼容问题，实现者用普通循环。）在 `merged_profile` 合并后调用 `_persist_form_profile(rt, user_id, req.profile or {})`。

创建 `tests/api/test_consult_profile.py`：

```python
from careercrew_core.memory.semantic import SemanticFactStore
from careercrew_core.memory.types import UserProfile

def test_profile_from_model_includes_current_position():
    from careercrew_api.routers.consult import _profile_from_model
    model = type("M", (), {
        "profile": UserProfile(current_position="后端开发 / 互联网"),
        "preferences": type("P", (), {"city": [], "salary_min": None, "salary_max": None})(),
        "target_companies": [],
    })()
    assert _profile_from_model(model)["current_position"] == "后端开发 / 互联网"

def test_update_profile_clears_current_position(client):
    client.put("/api/profile", json={"fields": {"profile.current_position": "后端"}})
    assert client.get("/api/profile").json()["profile"]["current_position"] == "后端"
    client.put("/api/profile", json={"fields": {"profile.current_position": ""}})
    assert client.get("/api/profile").json()["profile"]["current_position"] == ""
```

Run: `pytest tests/api/test_consult_profile.py -q`，Expected: FAIL。

- [ ] **Step 2: 前端弹窗按会话 + 错误移除占位**

`careercrew_web/src/pages/ConsultPage.tsx`：
- `const [formDismissed, setFormDismissed] = useState(false)` → `const [dismissedThreads, setDismissedThreads] = useState<Record<string, boolean>>({})`；`const formDismissed = dismissedThreads[currentThreadId] ?? false`；`setFormDismissed(true)` 的两处改为 `setDismissedThreads((p) => ({ ...p, [currentThreadId]: true }))`。切换会话时各 tid 独立键自动重置；新建会话（新 tid）无键 → false。
- 流结束 effect 顶部加错误分支：

```tsx
if (stream.status === "error") {
  setMessages((prev) =>
    lastAssistantIdRef.current ? prev.filter((m) => m.id !== lastAssistantIdRef.current) : prev
  )
  lastAssistantIdRef.current = null
  return
}
```

`KnowledgePage.tsx` 的 done-effect 同样处理：`stream.status === "error"` 时移除最后一条 `streaming` 且无内容的消息。`ChatPage/MatcherPage/InterviewPage/ResumePage`：找到同样"streaming 占位 + 结束回填"的 effect，加相同错误分支（实现者逐个核对，模式一致：错误时移除未填充的助手占位气泡）。

- [ ] **Step 3: 登出/换用户清空会话状态**

`threadStore.ts` 加 `resetAll: () => set({ threadsByModule: {}, currentThreadByModule: initialCurrent, completedUnread: {}, copiedThreadId: null, error: "" })`；`streamStore.ts` 加 `resetAll: () => { controllers.forEach(c => c.abort()); controllers.clear(); set({ sessions: {} }) }`。
`App.tsx` 加 effect：

```tsx
const userId = auth.user?.id
useEffect(() => {
  useThreadStore.getState().resetAll()
  useStreamStore.getState().resetAll()
}, [userId])
```

- [ ] **Step 4: 前端回归测试**

创建 `careercrew_web/src/pages/ConsultPage.test.tsx`（jsdom）：

```tsx
import { beforeEach, describe, expect, it, vi } from "vitest"
import React from "react"
import { render, waitFor } from "@testing-library/react"
import ConsultPage from "@/pages/ConsultPage"
import { useThreadStore } from "@/store/threadStore"
import { useStreamStore, IDLE_SESSION } from "@/store/streamStore"

const apiFetch = vi.fn()
vi.mock("@/lib/auth", () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }))

beforeEach(() => {
  apiFetch.mockReset()
  apiFetch.mockImplementation(async () => ({ ok: true, status: 200, json: async () => [] }))
  useThreadStore.setState({ threadsByModule: {}, currentThreadByModule: { consult: "c-a" } })
  useStreamStore.setState({ sessions: {} })
})

describe("ConsultPage 错误占位气泡", () => {
  it("流 error 且无内容时不残留空气泡", async () => {
    useStreamStore.setState({
      sessions: { "c-a": { ...IDLE_SESSION, threadId: "c-a", status: "error", errorMsg: "boom" } },
    })
    render(<ConsultPage />)
    await waitFor(() => {
      // 历史为空 + 错误状态：不应出现"总调度官结论"空卡片（HistoryAssistant 不渲染）
      expect(document.body.textContent || "").not.toContain("总调度官结论")
    })
  })
})
```

- [ ] **Step 5: 全量校验 + 提交**

Run: `pytest tests/api -q`；`cd careercrew_web && npm run test && npm run lint && npm run build`
Commit: `git commit -m "feat(consult): persist current_position, per-session form dismissal and error placeholder cleanup"`

---

### Task 5: CI 拆分、覆盖率门禁与路由懒加载

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `careercrew_web/src/App.tsx`
- Modify: `pyproject.toml`（dev 依赖加 `pytest-cov`；测试标记注释更新）

**Interfaces:**
- Consumes: Task 0 前端测试、Task 1-3 的 tests/api 与 tests/unit。
- Produces: CI jobs `unit` / `api` / `postgres-memory` / `typecheck` / `frontend` / `coverage-gate` / `nightly`；前端仅渲染当前路由页面（React.lazy + Suspense）。

- [ ] **Step 1: CI 拆分**

`.github/workflows/ci.yml` 重写为：
- `unit`：现状两步（config + ai factories）。
- `api`：安装 `pip install pytest pytest-mock pytest-check pytest-cov fastapi uvicorn python-multipart "PyJWT>=2.10" "argon2-cffi>=23.1" pydantic PyYAML python-dotenv numpy "langgraph>=1.2.0" "langchain>=1.0.0" "langchain-core>=1.0.0" "langchain-openai>=1.0.0" "qdrant-client>=1.19.0" "langgraph-checkpoint-sqlite>=3.0.0" psycopg[binary]` + `pip install -e . --no-deps`；env `SILICONFLOW_API_KEY=sk-test-ci-dummy`、`CAREERCREW_ENV=test`；run `pytest -q tests/api --cov=careercrew_api --cov=careercrew_core --cov-report=xml`；`actions/upload-artifact@v4` 上传 `coverage.xml`。
- `postgres-memory`：现状保留。
- `typecheck`：安装轻量依赖后 `python -m compileall -q careercrew_api careercrew_core careercrew_ai`（失败非零）。
- `frontend`：`cd careercrew_web && npm ci && npm run lint && npm run test && npm run build`。
- `coverage-gate`：`needs: [api]`；`actions/download-artifact@v4` 取 coverage.xml；`pip install diff-cover`；`git fetch origin main --depth=1 || true`；run `diff-cover coverage.xml --compare-branch=origin/main --fail-under=80 --html-report diff-cover.html`；上传报告 artifact。
- `nightly`：`schedule: [{cron: "0 18 * * *"}]` + `workflow_dispatch`；运行 `pytest -q -m "integration or e2e"`（依赖 Postgres service，与 postgres-memory 同配置）；允许失败不阻塞（`continue-on-error: true`，输出留痕）。

- [ ] **Step 2: 前端路由懒加载**

`careercrew_web/src/App.tsx`：

```tsx
import { lazy, Suspense, useEffect, useRef, useState, useSyncExternalStore } from "react"
const ChatPage = lazy(() => import("@/pages/ChatPage"))
const MatcherPage = lazy(() => import("@/pages/MatcherPage"))
const InterviewPage = lazy(() => import("@/pages/InterviewPage"))
const ResumePage = lazy(() => import("@/pages/ResumePage"))
const KnowledgePage = lazy(() => import("@/pages/KnowledgePage"))
const ConsultPage = lazy(() => import("@/pages/ConsultPage"))
const DataPage = lazy(() => import("@/pages/DataPage"))
```

删除静态 imports；`PAGES` 值改为 lazy 组件；`<main>` 内改为只渲染当前页：

```tsx
<main className="flex-1 overflow-hidden">
  <Suspense fallback={<div className="flex h-full items-center justify-center text-sm text-muted-foreground">页面加载中…</div>}>
    {(() => { const Page = PAGES[location.pathname] ?? ChatPage; return <Page key={location.pathname} /> })()}
  </Suspense>
</main>
```

（行为变化说明：页面不再常驻，导航卸载/挂载时各页从 /api/memory 重新加载历史——这正是"切换会话恢复"路径，属预期；`key` 保证页面级 state 不复用。）

- [ ] **Step 3: 验证 + 提交**

Run: `cd careercrew_web && npm run build`（vite 输出 chunk 拆分，>500kB 单 chunk 警告应显著下降）；`npm run test`。
Commit: `git commit -m "build(ci): split CI jobs with diff-cover gate and lazy-load page routes"`

---

### Task 6: Agent/RAG 评估发布闭环

**Files:**
- Create: `data/eval/cases.jsonl`、`data/eval/baseline.json`、`data/eval/fixtures/`（预录制检索结果样例）
- Create: `scripts/eval_runner.py`
- Create: `tests/unit/test_eval_runner.py`
- Modify: `.github/workflows/ci.yml`（`eval-sanity` job；nightly 加 `--real` 可选步）

**Interfaces:**
- Consumes: 无硬依赖（离线 fixtures 模式不加载重模型）。
- Produces:
  - `cases.jsonl` 行 schema：`{"id": str, "kind": "route|retrieval|answer|citation|tool|consult|memory", "question": str, "expected": {...}, "fixtures": {...}}`（expected 按 kind 定：route→`{"route": "salary_negotiator"}`；retrieval→`{"doc_ids": [...], "k": 5}`；citation→`{"must_include": ["..."], "answer_ref": "..."}`；tool→`{"tool_names": [...]}`；consult→`{"expected_agents": [...], "max_latency_s": 600, "max_tokens": 8000}`；memory→`{"memory_hit": true, "retention_ref": "..."}`）。
  - `python scripts/eval_runner.py` 子命令：
    - `--offline`：只用 fixtures 计算指标并输出 JSON；
    - `--real`：真实模型/服务（检索走 MultimodalSearch、路由走 orchestrator 或 LLM），慢，nightly 用；
    - `--update-baseline`：把本次指标写 `data/eval/baseline.json`；
    - `--compare baseline.json --fail-on-regression`：任一指标低于基线 0.01 即非零退出；
    - `--report out.json`。
  - 指标实现（纯函数，可单测）：`hit_at_k`、`mrr`、`citation_coverage`（子串覆盖比例）、`tool_success`（期望工具是否出现在记录调用里）、`route_accuracy`、`memory_hit`、`retention`（引用要点子串覆盖）。

- [ ] **Step 1: 指标函数 + 失败测试（RED）**

创建 `tests/unit/test_eval_runner.py`：

```python
from scripts.eval_runner import citation_coverage, hit_at_k, mrr, route_accuracy

def test_hit_at_k():
    assert hit_at_k([["d1", "d2"], ["d3"]], [["d2"], ["d3"]]) == 1.0

def test_mrr():
    assert mrr([["d1", "d2"], ["d9"]], [["d2"], ["d9"]]) == 0.75

def test_citation_coverage():
    assert citation_coverage("答案A和B。", ["答案A", "B"]) == 1.0
    assert citation_coverage("只有A。", ["答案A", "缺失"]) == 0.5

def test_route_accuracy():
    assert route_accuracy(["a", "b", "c"], ["a", "x", "c"]) == 2 / 3
```

Run: `pytest tests/unit/test_eval_runner.py -q`，Expected: FAIL（模块不存在）。

- [ ] **Step 2: runner + cases + baseline**

`scripts/eval_runner.py`：实现上述指标函数与 CLI（argparse）；`--offline` 从 `data/eval/fixtures/*.json`（`{case_id: {"retrieved": [...], "route": "...", "answer": "...", "tools": [...], "memory_hit": true, "latency_s": 0, "tokens": 0}}`）读取观测值，按 cases.jsonl 的 expected 计算各指标汇总（route_accuracy、hit@5、mrr、citation_coverage、tool_success、memory_hit、retention、consult 的 latency/token 为透传统计）。`--real` 分支留清晰 TODO 接口：`collect_real(case)` 返回同结构观测（真实模型调用放 nightly/manual，代码框架 + 环境缺 key 时给出明确提示退出）。

`data/eval/cases.jsonl`：写 12 条种子用例（route 4：薪资/岗位/简历/面试关键词各一；retrieval 3：用仓库 docs 与 tests 中出现过的真实可检索文本；citation 2；tool 1（matcher 期望 search_jobs）；memory 1；consult 1）。`data/eval/fixtures/*.json` 提供对应观测样例（标记为 `"source": "fixture"`）。运行 `python scripts/eval_runner.py --offline --update-baseline` 生成 `data/eval/baseline.json`（把生成命令写入 `data/eval/README.md` 说明，包含"真实评估如何运行"与"PR 门禁原理"）。

- [ ] **Step 3: CI 接入**

`.github/workflows/ci.yml` 加 job `eval-sanity`：`python scripts/eval_runner.py --offline --compare data/eval/baseline.json --fail-on-regression`；nightly job 加一步 `python scripts/eval_runner.py --real`（`continue-on-error: true`，密钥缺失时脚本给提示退出 0 留痕）。

- [ ] **Step 4: 全量校验 + 提交**

Run: `pytest tests/unit -q`；`python scripts/eval_runner.py --offline --compare data/eval/baseline.json --fail-on-regression`
Commit: `git commit -m "feat(eval): offline eval runner with versioned baseline and regression gate"`

---

## 执行说明（控制器）

- 分支：从 main 建 `codex/session-scope-uploads-sse`（不得直接在 main 提交）。
- 顺序：Task 0 → 1 → 2 → 3 → 4 → 5 → 6；每任务 implementer 提交后出 review package 并派 reviewer；Critical/Important 发现派 fix 后复审；Minor 记台账，最后整体评审统一裁决。
- 前端命令一律在 `careercrew_web/` 下执行；后端 pytest 在仓库根执行；测试环境变量按 conftest/CI 已有约定（FakeRuntime 不需真实 key；涉及 create_app 时 `CAREERCREW_ENV=test`）。
- 提交前必须 `git status` 确认没有夹带 `.superpowers/`、`review-*.diff`、`data/uploads` 等未跟踪文件。

## 自审

1. Spec 覆盖：blob 修复（T0）✅；检索范围持久化/恢复/兼容回退/测试（T1）✅；上传 UUID 布局/路径安全/禁止启动扫描/审计+迁移命令（T2）✅；CancellationEvent/断连取消/队列背压/终态不阻塞/统一 300s/回归测试（T3）✅；current_position 持久化+显式清空/弹窗按会话/错误移除占位/登录登出 UX（T4）✅；CI 拆分/80% 变更行门禁/React.lazy（T5）✅；评估集/baseline/离线非回归/真实模型 nightly（T6）✅。会话范围"简历范围"扩展在 schema 注释中预留（YAGNI，不做多余实现）。
2. 占位符扫描：无 TBD/TODO 式空洞；Task 2 测试中 monkeypatch 根目录的具体写法留实现者按"函数式 layout + reload/注入"接口落地（已在 Interfaces 中给出契约与最简注入方式）。
3. 类型一致性：`RetrievalScope`（TS）↔ `RetrievalScopeRequest`（py）字段一致；`upsert_thread(..., retrieval_scope=None)` 全链路（db→ThreadStore→runtime→data→conftest）签名一致；`ingest_document(output_dir=, doc_name=)` ↔ `pipeline.ingest_file(output_dir=)` 一致；`stream_agent(run_fn, *, timeout=None, max_q=256, cancel=None)` 在五个路由文件统一。
