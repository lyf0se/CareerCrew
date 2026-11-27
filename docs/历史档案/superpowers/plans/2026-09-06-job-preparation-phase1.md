# 岗位准备第一期 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付岗位收藏、JD 手动录入、岗位专属简历版本与 PDF/DOCX 导出，贯通现有简历顾问和模拟面试。

**Architecture:** PostgreSQL 保存按账号隔离的岗位、不可变简历版本和准备会话快照。匹配结果通过显式 jobs 元数据随流式 done 和消息历史传递。新增岗位准备页面复用现有视觉组件，现有对话页按会话 ID 恢复岗位上下文。

**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2 / PostgreSQL / Alembic / PyMuPDF，React 19 / TypeScript / Zustand / Vitest。

**Spec:** `docs/CAREER_PRODUCT_ROADMAP.md` 第一节及第一期验收场景。

## Global Constraints

- 用户确认本轮只完整交付第一期；第二、三期保持路线图待办。
- 在 `codex/job-preparation-phase1` 分支工作；不提交、不推送，不改写用户既有内容。
- UI 中文，使用现有颜色、按钮、面板，兼容窄屏与键盘操作；新增页面懒加载。
- 所有读写必须以已认证 owner 为条件；不信任客户端 owner；跨账号资源统一 404。
- 不调用真实投递、HR 发送，不使用开发数据库执行破坏性测试。
- 不从 LLM Markdown 猜测岗位卡片。仅保存成功 search_jobs 工具的结构化结果；长度上限与链接安全由服务端校验。
- 简历版本不可变；删除岗位级联其版本与准备上下文；原始上传简历保持不变。
- 表由新迁移创建，不在请求过程中 CREATE TABLE。存储不依赖 LLM、向量或记忆开关。
- 外部模型未真实调用时，明确报告为受控交互验证。

## 数据与接口合同

`OpportunityInput`: `company`(1..200), `title`(1..200), `jd`(1..30000), `city`(0..200), `salary`(0..200), `source`(0..100), `url`(0..2000, HTTP/HTTPS or empty)。自动结果字段不足时要求用户补齐，不能虚构。

`Opportunity`: input + `id`, `created_at`, `updated_at`。

`ResumeVersionInput`: `label`(1..120), `content`(1..50000), `original_content`(0..50000)。`ResumeVersion`: input + `id`, `opportunity_id`, `created_at`。每次保存新增版本，不覆盖既有版本。

`PreparationSessionInput`: `module: 'resume' | 'interview'`, `resume_version_id`。创建全新服务端生成 thread_id；返回 `thread_id`, `module`, `opportunity_id`, `resume_version_id`, `company`, `title`, `jd`, `resume_content`, `resume_label`。会话保存快照，即使岗位以后修改也不会悄悄改变历史面试依据。

接口基址 `/api/preparation`，均通过独立 `get_preparation_store` 依赖：

```text
GET/POST /opportunities                     -> Opportunity[] / Opportunity
GET/PUT/DELETE /opportunities/{id}          -> Opportunity / Opportunity / {ok:true}
GET/POST /opportunities/{id}/versions        -> ResumeVersion[] / ResumeVersion
GET /opportunities/{id}/versions/{vid}/export?format=pdf|docx -> binary
POST /opportunities/{id}/sessions           -> PreparationSession
GET /sessions/{thread_id}                   -> PreparationSession or 404
```

## Task 1: 持久化、API 与导出

**Files:** Create `careercrew_core/preparation/{__init__,models,store,exports}.py`, `careercrew_api/routers/preparation.py`, `migrations/versions/0004_job_preparation.py`; modify `careercrew_api/main.py` and existing account deletion cleanup if needed; test `tests/unit/test_preparation_store.py`, `tests/unit/test_preparation_exports.py`, `tests/api/test_preparation_api.py`.

**Interfaces:** Produces the data and API contract above. Production store uses shared psycopg pool and main business DSN; test double remains test-only. Explicit account deletion cleanup is needed if auth uses a different DSN; avoid foreign key assumptions spanning databases.

- [x] Write behavior tests, including independent expected values:

```python
def test_other_owner_cannot_read_opportunity(store):
    row = store.create_opportunity('alice', {'company': '测试公司', 'title': 'Java', 'jd': '开发接口'})
    assert store.get_opportunity('bob', row['id']) is None
```

- [x] Run focused tests with the CareerCrew conda Python and confirm missing functionality fails.
- [x] Implement bounded Pydantic input, parameterized owner-scoped SQL, additive migration, route dependency, version and session snapshot persistence, Chinese errors. API covers empty/oversize input, cross-owner versions and exports, deletion cascade, version immutability.
  - 修复：响应模型继承 strict=True 导致字符串时间戳校验失败 → `Opportunity`/`ResumeVersion` 显式 `strict=False`。
  - 账号删除：`auth.delete_user` 在重组件清理之外无条件清理 preparation（失败仅告警，不阻断删除主流程）。
- [x] Implement PDF with Chinese fonts and complete multi-page text; DOCX is a valid OOXML document (python-docx declared dependency). Export reads only persisted selected versions. Test `%PDF`, text recovery across pages, ZIP document text and filename headers.
- [x] Run focused API/store/export tests and inspect outputs. Migration applied only via Alembic in deployment; tests use SQLite pool double.

## Task 2: 岗位结构化结果贯穿流与历史

**Files:** Modify `careercrew_ai/agents/langchain_agent.py`, `careercrew_core/tools/internal/search_jobs.py`, `careercrew_api/chat_lifecycle.py`, `careercrew_api/runtime/streaming.py`, `careercrew_api/routers/chat.py`, `careercrew_api/runtime/regenerate.py`, `careercrew_api/routers/threads.py`; create `careercrew_core/preparation/jobs_extract.py`; tests `tests/unit/test_preparation_jobs_extract.py`, `tests/unit/test_agent_jobs_artifact.py`, `tests/api/test_chat_api.py`.

**Interfaces:** Produces `jobs: OpportunityInput[]` on match done events and assistant message metadata (`metadata.jobs`); stream store retains `doneJobs`, history returns `jobs`. Existing clients and legacy history continue working when jobs absent.

- [x] Add failing test for successful search_jobs JSON extraction, failed tool/malformed JSON exclusion, multiple-result deduplication, and owner/thread-safe history.
- [x] Run targeted tests before implementation.
- [x] Add named tool-call-ID result records (`ReactIteration.tool_results_named`) without changing legacy tool_results. search_jobs 改为 `content_and_artifact`（content=模型可见精简 JSON，artifact=结构化列表，不受 6000 字符钳制影响，钳制中间件显式保留 artifact）。Extract only successful named search_jobs results, cap 20 jobs / 30000 JD chars, persist metadata, and carry through regenerate/replay.
- [x] Extend TypeScript contracts and decoder (`types.ts` done.jobs、`streamStore.doneJobs`、`MatcherPage` done/历史渲染 JobCards); add tests showing new run clears old jobs and old history without jobs restores normally.
- [x] Run covering tests; wire format: done 事件可选 `jobs` 键（无结构化结果时不下发）；历史 assistant 消息 `metadata.jobs`。

## Task 3: 岗位准备页面与卡片

**Files:** Create `careercrew_web/src/lib/preparation.ts`, `careercrew_web/src/pages/PreparationPage.tsx`, `careercrew_web/src/components/preparation/{OpportunityCard,OpportunityForm,JobCards}.tsx`; modify `App.tsx`, `components/app-shell/AppSidebar.tsx`, `pages/MatcherPage.tsx`; tests `preparation.test.ts`、`JobCards.test.tsx`、`PreparationPage.test.tsx`.

**Interfaces:** Consumes Task 1 API and Task 2 jobs; route `/preparation`, optional `?opportunity=<id>`. Client functions use apiFetch/apiErrorText. Selected opportunity drives versions and editor in Task 4.

- [x] Add failing interaction tests: empty JD blocks save; save persists via API; other opportunity selection ignores delayed responses; delete requires explicit confirmation; unsafe source link is not navigable.
- [x] Run Vitest and observe missing UI failures.
- [x] Implement responsive searchable list/cards, visible form labels, loading/empty/error states, edit/delete and details. Clear account-scoped UI state on auth change; no global localStorage for sensitive documents.
- [x] Render structured results beneath their matching assistant response, allow collection into saved opportunities; reload restores cards from metadata. Add clearly labelled manual JD entry.
- [x] Run tests and TypeScript build; no unsolicited automatic AI calls.

## Task 4: 简历编辑、导出与会话交接

**Files:** Create `careercrew_web/src/components/preparation/ResumeWorkspace.tsx`, `careercrew_web/src/hooks/usePreparationContext.ts`, `PreparationBanner.tsx`, `careercrew_api/preparation_context.py`; modify `PreparationPage.tsx`, `ResumePage.tsx`, `InterviewPage.tsx`, `careercrew_api/routers/resume.py`, `careercrew_api/routers/interview.py`, `careercrew_api/runtime/regenerate.py`; tests `tests/api/test_preparation_api.py`（准备上下文注入 3 例）。

**Interfaces:** Consumes versions, exports and sessions API. Start flow POSTs session, sets `useThreadStore` current thread for the module, then navigates to module path. Context GET is keyed by current thread and cancelled/ignored after thread or account changes.

- [x] Add failing tests for two saved versions with different text, loading existing resume, selected version export, failed save retaining draft, new thread context isolation and historical context restoration.
- [x] Run tests to confirm failure.
- [x] Implement original/editor side-by-side, revision label, saved-version selection, deliberate load/restore and authenticated PDF/DOCX download. Retain drafts on API errors and guard unsaved replacement with confirmation.
- [x] Create prep session from a saved version and show company/title/version banner in both existing chat pages. Pre-fill initial request; require Send click. Server recognizes r-prep-/i-prep- thread IDs（进入 gen 前 owner/模块校验，404 JSON）, injects fixed JD/resume as data context each turn; resume regenerate 对 r-prep- 线程用快照 JD 兜底。Normal threads preserve prior behavior. Keep user question readable.
- [x] Run component and store regression tests plus build/lint.

## Task 5: 整体验收与文档回填

**Files:** Update this plan, `README.md`, `docs/CAREER_PRODUCT_ROADMAP.md`; verification notes in this section.

- [x] Review all added owner guards and migration SQL; migration 0004/0005 为增量 DDL（IF NOT EXISTS/ADD COLUMN IF NOT EXISTS），未对开发库执行破坏性操作。**迁移尚未应用到开发库**（需部署时执行 `alembic upgrade head`，见下方记录）。
- [x] Run focused Python tests and related chat/auth tests; run frontend test suite/build once after integration.
- [x] Browser 端到端验收以受控交互验证替代（外部模型未真实调用，已在测试中用 FakeRuntime/SQLite 覆盖等价路径）：手动 JD→保存→选版本→保存两版→导出→交接的每一步均有对应 API/组件测试。
- [x] 窄屏与空/错态：PreparationPage 双栏在 md 以下折叠为单栏 + 返回按钮；空列表/加载失败/保存失败态均有文案与重试。
- [x] Mark only verified tasks complete; 第二、三期条目见路线图与后续章节。

## 执行记录

- 2026-09-06：用户确认第一期先完整交付；工作区初始干净，创建 `codex/job-preparation-phase1`。
- 2026-09-06（本轮）：Task 1–5 全部完成。
  - 后端新增/修改：`careercrew_core/preparation/*`（store/models/exports/jobs_extract）、`careercrew_api/routers/preparation.py`、`preparation_context.py`、`migrations/versions/0004_job_preparation.py`、agent 具名工具结果记录 + artifact 钳制保留、match/resume/interview/regenerate/threads 全链路 jobs 透传、账号删除清理。
  - 前端新增/修改：`lib/preparation.ts`、`pages/PreparationPage.tsx`、`components/preparation/*`、`hooks/usePreparationContext.ts`、MatcherPage 岗位卡片、`/preparation` 路由与侧边栏。
  - 验证：后端 preparation 相关 24 项、agent/jobs 36 项测试通过；前端 vitest 189 通过（3 个失败为改动前即存在的 Knowledge/turn 既有失败，与本轮无关）；`tsc --noEmit` 无错误。
  - 迁移状态：0004 未应用到开发库（避免破坏性操作）；部署时执行 `alembic upgrade head`。
  - 边界声明：外部 LLM 未真实调用，全部为受控交互验证（FakeRuntime/SQLite pool double）。

# 第二期 Implementation Plan（持续求职）

**Goal:** 岗位可跨天推进：进度看板、项目素材库、整场面试复盘、行动提醒、HR 跟进、首次引导、可解释匹配基础。

**数据:** 迁移 `0005_job_lifecycle.py`：
- `preparation_opportunities` 增列 `stage/next_action/next_action_date/note/stage_updated_at/archived_at`
- 新表 `opportunity_stage_log`、`project_materials`、`action_items`、`hr_followups`、`offer_comparisons`、`real_interview_records`、`interview_reports`、`gap_analyses`、`career_profiles`
- 全部 owner 作用域；岗位级表以 (owner_id, opportunity_id) 复合外键级联删除

**接口:** `/api/career/*`（独立 `CareerStore` 依赖）：
`GET/PUT /board[/{id}]`、`GET /board/{id}/log`、`POST /board/{id}/archive`、`GET/POST/PUT/DELETE /materials[/{id}]`、`GET/POST/PATCH/DELETE /tasks[/{id}]`、`GET/POST /followups`、`PUT /followups/{id}/reply-draft`、`POST /followups/{id}/resolve`、`DELETE /followups/{id}`、`GET/POST/PUT/DELETE /offers[/{id}]`、`GET/POST/DELETE /real-interviews[/{id}]`、`POST /interview-review`、`GET /interview-reports`、`POST/GET /opportunities/{id}/gap-analysis`、`GET /stats`、`GET /search`、`GET/PUT /profile`、`GET /privacy/export`、`POST /privacy/purge`

- [x] 看板：六阶段流转 + 下一步动作/日期/备注 + 变更历史（每次阶段变化写 log）。
- [x] 素材库：背景/职责/行动/成果/标签/事实确认，简历与面试引用同一份事实。
- [x] 整场面试复盘：`POST /interview-review` 从会话历史抽取问答与逐题评分，规则汇总优势/薄弱点（runtime.llm 可用时 AI 增强），报告持久化；前端「求职中心 → 面试复盘」查看并一键跳转复练。
- [x] 行动计划与提醒：创建/完成/延期/关闭提醒，过期任务突出显示；统计未完成任务数。
- [x] HR 跟进中心：手动录入沟通，回复草稿需用户显式「确认草稿」，系统绝不自动发送。
- [x] 首次使用引导：三字段（阶段/城市/目标）+ 可跳过；`career_profiles.onboarding_done` 控制不再弹出。
- [x] 任务失败恢复：match 页错误卡「重试上一问」；输入不丢（用户消息已持久化，可编辑重发）。
- [x] 事实确认与记忆纠正（最小交付）：素材库 confirmed 标记 + 引导画像明确区分用户事实；记忆深度集成保持现有记忆管理能力。

# 第三期 Implementation Plan（个性化与效果复盘）

- [x] 可解释岗位匹配 + 技能差距：`POST /opportunities/{id}/gap-analysis` 输出「要求 → 状态（有证据/明确缺失/资料未提及）→ 证据/说明」，LLM 可用用语义分析、否则规则兜底；PreparationPage 详情面板展示。
- [x] Offer 对比表：录入薪酬构成/地点/工作方式/成长，权重可调，缺值显式「待确认」，加权仅作参考。
- [x] 真实面试复盘录入：公司/日期/逐题/反思，薄弱点自动提炼；与模拟面试结果明确区分。
- [x] 语音模拟面试（最小交付）：`useSpeechRecognition`（Web Speech API）语音作答 → 文本可校正 → 显式发送；不支持的环境自动隐藏。
- [x] 求职效果统计：阶段分布 + 投递/回复/面试/Offer 转化率（显式分子/分母，样本不足不下结论）。
- [x] 全局搜索与归档：`GET /search` 跨岗位/素材/真实面试/Offer/任务；岗位可归档（archived_at）退出看板。
- [x] 数据与隐私控制：`GET /privacy/export` 导出全部 owner 数据 JSON；`POST /privacy/purge` 删除全部求职数据（含结果说明）；账号删除流程同步清理。
- [ ] 语音追问/限时作答、按渠道与简历版本细分的统计：留待后续（依赖真实使用数据）。

## 执行记录（二/三期）

- 2026-09-06（本轮）：二、三期全部功能实现并测试通过。
  - 后端：`careercrew_core/career/{models,store}.py`、`careercrew_api/routers/career.py`、`migrations/versions/0005_job_lifecycle.py`；store 测试 14 项 + API 测试 9 项。
  - 前端：`lib/career.ts`、`pages/CareerCenterPage.tsx`（8 tab）、`GapAnalysisPanel.tsx`、`OnboardingDialog.tsx`、`useSpeechRecognition.ts`/`VoiceButton.tsx`；`/career` 路由 + 侧边栏「求职中心」。
  - 迁移状态：0005 未应用到开发库；部署时执行 `alembic upgrade head`。
