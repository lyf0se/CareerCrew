# CareerCrew 独立 Web 前端 — 实施现状文档

> 本文档记录 CareerCrew 独立 Web 前端的完整实施现状（2026-08-11 更新）。
> 仓库: `F:\agent_develop\CareerCrew`(conda 环境 `careercrew`,Python 3.12)。
> 双服务本地开发：FastAPI(:8000) + React/Vite(:5176)，生产模式 uvicorn 单端口托管。

> **后续演进（2026-08-11，相对本文初稿）**：RAG 全面切换到 Qdrant + MinerU 多模态
> （resume 上传 PDF/docx 走 MinerU，不再用 MarkItDown）；自建 trace（TraceRecorder /
> `logs/traces.jsonl`）退役改 LangSmith，`GET /api/traces` 与前端轨迹面板已移除
> （追踪直接在 LangSmith 控制台查看）；Streamlit Dashboard 的 app/pages 已删除
> （`careercrew_ui/dashboard/data.py` 仅保留为 /api 提供数据读取）；新增知识库
> 路由与前端知识库面板；`data/knowledge` 手写 seed 移除，知识库只含 `data/uploads/`。

---

## Context（为什么做）

用户反馈 CLI(`careercrew chat`)无法粘贴跨行内容(`input()` 只取一行)，项目一直只有纯 CLI + 只读 Streamlit Dashboard。构建了独立 Web 前端，包含全部 4 个功能：求职闭环对话、面试练习、简历上传/多模态、M3 多agent会诊。

**约束**：
- 保留现有 `careercrew chat` CLI（Streamlit Dashboard 的 app/pages 后续已移除）
- 201 个现有测试不能破坏（测试用 FakeAgent + select_jd 注入）
- 分层规则: ai 层不得运行时 import core(API 是组合根可都 import)
- **git commit 不加 Co-Authored-By trailer**(用户全局规则)
- BGE-M3+Qdrant+KB 重量级初始化只做一次

---

## 1. 项目布局

```
pyproject.toml              # [web] extra + packages.find include careercrew_api
careercrew_api/             # FastAPI 后端包(与 CLI 平级的组合根)
├── __init__.py
├── main.py                 # app、CORS、/api 挂载、生产托管 careercrew_web/dist(SPA fallback)
├── runtime.py              # CareerCrewRuntime 惰性单例 + 会话级 agent 工厂 + per-thread episodic
├── sse.py                  # stream_agent(): 线程+queue → NDJSON 生成器
├── schemas.py              # pydantic 请求/响应模型
├── deps.py                 # get_runtime_dep 依赖(测试用 dependency_overrides 换 fake)
└── routers/{__init__,chat,interview,resume,consult,data,knowledge}.py
careercrew_web/             # React 前端(独立 package.json,Vite 工程)
tests/api/                  # API 测试(TestClient + FakeRuntime 注入)
data/uploads/{user_id}/     # 上传暂存(gitignore 加 data/uploads/)
```

pyproject 追加:
```toml
[project.optional-dependencies]
web = ["fastapi>=0.115", "uvicorn>=0.30", "python-multipart>=0.0.9"]
```
`[tool.setuptools.packages.find]` include 里有 `careercrew_api` / `careercrew_api.*`。

## 2. 运行时单例(careercrew_api/runtime.py)

**核心决策**:重组件(llm/embedding/store/reranker/MultimodalSearch/user_model)进程级单例；**agent 与 JobCycle 按会话(thread_id)新建**；**情景记忆按 thread 分文件**(`data/transcripts/{user_id}/{thread_id}.jsonl`)。

- `_ensure_heavy()`: 按 `careercrew_cli/app.py` 的组装逻辑复刻(去 Renderer 依赖):
  configure_langsmith(先于 create_llm) / create_embedding / create_vector_store(Qdrant) /
  create_llm(max_tokens=1024) / SiliconFlowVLReranker / MultimodalSearch /
  MultimodalIngestionPipeline(MinerU,`provider` 按配置 api|local);`store.count()==0`
  时自动入库 `data/uploads/`(知识库只含上传文档);EpisodicMemory + UserModelStore。
  无 TraceRecorder(追踪走 LangSmith)。
- `_get_episodic(thread_id, user_id)`: **per-thread EpisodicMemory**(懒创建，dict 缓存)
- `get_cycle(thread_id, user_id)`: dict[thread_id, JobCycle] LRU 缓存，agent 注入 per-thread episodic
- `run_match_stream(thread_id, user_id, intent, cb)`: 跑匹配 + **存 user_message/agent_response 到 episodic** + **首条消息时 LLM 生成 thread_title**;agent 用 `max_iterations=15` + 超轮次兜底结论
- `run_resume_stream(thread_id, user_id, jd_text, cb)`: 同理
- `get_threads(user_id)`: 扫描 transcripts 目录列出线程(**跳过空文件**)，标题优先 thread_title → user_message → 首条内容
- 工厂: `new_job_matcher(cb, episodic)` / `new_resume_advisor` / `new_interviewer` / `new_consult_agent` —— 全部注入 `stream_callback=cb` 和 per-thread episodic
- 直通: `score_answer` / `record_interview_qa` / `read_image` / `load_document` / `ingest_document` / `delete_document` / `knowledge_status` / `consult_stream`
- 模块级 `get_runtime()` 双检锁惰性单例;`reset_runtime()` 测试用
- FastAPI 集成: `deps.py` 提供 `get_runtime_dep`;**测试用 `app.dependency_overrides[get_runtime_dep] = lambda: FakeRuntime()`**

## 3. SSE 流式协议

**NDJSON**(fetch + ReadableStream 读 NDJSON,每行一事件,text 内换行由 JSON 转义)。`stream_agent(run_fn, timeout=120.0)` 线程+queue。

**事件协议**(所有流式端点统一):
```
{type:"stage", stage:"match"|"resume"|"questions"|"consult"|"synthesis"}
{type:"chunk", text:"...", agent?: "job_matcher"}     # agent 字段仅会诊用
{type:"agent_start", agent} / {type:"agent_end", agent}  # 仅会诊
{type:"done", content:"最终文本", opinions?: {...}}     # 最后一个事件
{type:"error", message:"..."}                           # 任一点失败
```
响应头 `Content-Type: application/x-ndjson`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`。**每个事件以 `\n` 结尾**(NDJSON 行分隔)。

## 4. API 端点(前缀 /api)

所有 body 里 `user_id` 默认 `u_001`。

| Method | Path | Body | 流式 | 说明 |
|---|---|---|---|---|
| GET | /api/health | – | 否 | status/model/embedding/vector_store/ready,不触发重初始化 |
| GET | /api/config | – | 否 | 复用 get_settings_summary() |
| GET | /api/threads | ?user_id | 否 | **列所有线程**(跳过空文件),标题=thread_title/user_message/首条 |
| DELETE | /api/threads/{thread_id} | ?user_id | 否 | **删除线程记忆文件** |
| GET | /api/profile | ?user_id | 否 | 复用 get_user_model() |
| PUT | /api/profile | {fields} | 否 | **更新画像字段**(白名单约束) |
| GET | /api/memory | ?user_id&thread_id&type | 否 | **不传 thread_id=读所有线程合并按时间排序**;传=读单线程 |
| POST | /api/chat/match | {intent, thread_id?, user_id?} | **是** | cycle.run_match(intent) |
| POST | /api/chat/resume | {jd_text, thread_id?, user_id?} | **是** | cycle.run_resume(jd_text) |
| POST | /api/interview/questions | {topic?, user_id?} | **是** | Interviewer 出题（批量模式） |
| POST | /api/interview/chat | {topic?, messages?, user_id?} | **是** | **对话式模拟面试**：一轮一问；用户回答后 done 事件携带 score/feedback |
| POST | /api/interview/score | {question, answer, max_score?} | 否 | score_answer() |
| POST | /api/interview/record | {entries:[{q,a,score}]} | 否 | record_interview_qa() |
| POST | /api/resume/upload | multipart file | 否 | 图片→read_image;txt/md→MarkdownLoader;pdf/doc/docx→**MinerU**(provider=api 云端或 local 本地);>200k 截断;解析失败返回 doc_type=error 不崩 |
| POST | /api/resume/generate | {user_resume, jd?, thread_id?, user_id?} | **是** | 简历顾问流式优化 |
| POST | /api/consult | {question, agents?, user_id?} | **是** | 并行观点→synthesis |
| GET | /api/knowledge | – | 否 | 库状态{points, docs}(store.count/list_docs) |
| POST | /api/knowledge/upload | multipart file | 否 | **异步上传入库**：202 返回 {job_id}，后台入库；PDF/图片/docx/pptx/xlsx 走 MinerU |
| GET | /api/knowledge/upload/{job_id} | – | 否 | 任务进度 {status, stage, progress, error, result}（前端轮询渲染真实进度条） |
| DELETE | /api/knowledge/{doc_id} | – | 否 | 删除文档全部向量点 |

> 追踪查看**无 HTTP 接口**：`GET /api/traces` 已移除，直接在 LangSmith 控制台查看，
> `scripts/langsmith_smoke.py --list` 只读列根 run。

## 5. 前端(careercrew_web/src)

- **技术**: Vite + React 19 + TypeScript + Tailwind CSS v3 + shadcn/ui(button/card/textarea/tabs/skeleton/badge/input)+ zustand + react-router-dom v6 + react-markdown(remark-gfm)+ @fontsource/space-grotesk(本地字体,不依赖 Google Fonts)
- `vite.config.ts` 加 `server.proxy["/api"] = "http://localhost:8000"`(dev)
- **App.tsx 侧边导航**:深色侧边栏 + 品牌 SVG + 导航 + **对话历史线程列表**(可点击跳转/删除带确认)+ 健康指示灯;**所有页面保持挂载用 CSS hidden 切换**(流式不中断)
- **核心 hook useChatStream(endpoint)**: fetch POST + ReadableStream + TextDecoder 按行解析 NDJSON;返回 {status, streamingText, agentChunks, stage, doneContent, opinions, errorMsg, thinking, initializing, start, stop};thinking=2 秒无新 chunk(工具调用),initializing=流式开始未收内容(重组件加载)
- **agent 身份色系**: job_matcher=青/ resume_advisor=琥珀/ interviewer=玫红/ salary_negotiator=紫/ career_planner=蓝;出现在消息标签、圆点、会诊选择器
- **页面**:
  - **ChatPage**: 消息气泡(用户右/agent 左带身份标签),**MarkdownContent 渲染 markdown**(表格/标题/列表),JD 选择器(**仅内容含"匹配度/0.\d/公司"关键词才显示**),新对话按钮,流式完成后 bumpProfileNonce/bumpThreadNonce 通知看板/侧边栏刷新
  - **InterviewPage**: 对话式模拟面试（面试官一轮一问 → 作答 → 自动评分+黄金范例 → 追问；结束面试出总结；已评分条目保存到记忆）
  - **ResumePage**: 拖拽上传(整框可点击)→预览→AI 优化
  - **ConsultPage**: 并行 agent 观点卡 + 综合结论
  - **DataPage**: 画像(**可编辑**,PUT /api/profile,空值清空)/记忆(格式化展示,所有线程)/知识库(上传/列表/删除,**GET/POST/DELETE /api/knowledge**)
- **关键组件**:
  - MultilineInput: auto-grow textarea,Enter 发送/Shift+Enter 换行,**多行粘贴原样保留(核心痛点)**
  - MarkdownContent: react-markdown + remark-gfm,表格横向滚动
  - InitIndicator / ThinkingPulse: 初始化/工具调用等待提示
  - JDSelector: 匹配结果后粘贴 JD → 定制简历

## 6. 测试

- `tests/api/`: TestClient + FakeRuntime 注入,`@pytest.mark.web`,覆盖 chat / interview /
  resume / consult / data(health/config/profile/threads/memory) / knowledge 全部路由
- 全量 `pytest tests` 零回归(数量随用例演进,以 `pytest -q` 输出为准)
- FakeRuntime duck-types CareerCrewRuntime: new_job_matcher/new_resume_advisor/new_interviewer/
  new_consult_agent/score_answer/record_interview_qa/read_image/load_document/ingest_document/
  delete_document/knowledge_status/run_match_stream/run_resume_stream/health_info/get_cycle/llm
  (无 list_runs/get_run_detail——读取接口已移除)

## 7. 生产模式

- `cd careercrew_web && npm run build` → FastAPI StaticFiles 托管 `/assets` + catch-all SPA fallback 到 index.html
- 开发模式: uvicorn :8000 + vite :5176(`/api` 代理到 8000)

---

## 联调发现的后端问题(已修复)

> 以下为历史修复记录。其中 1（markitdown）后续已被 MinerU 取代、2（TraceRecorder）
> 随自建 trace 退役（LangSmith 接管）、6 的 Milvus 已换 Qdrant、9 的 max_iterations
> 在 API 路径为 15（CLI 仍为 8）。

1. **markitdown PDF 依赖缺失**: 上传 PDF 报 `FileConversionException` → 装 `markitdown[pdf]` + 上传路由 try/except 返回 `doc_type=error`
2. **TraceRecorder ts 是 float**: `time.time()` 存 Unix 时间戳,前端 `.slice()` 崩溃白屏 → 前端 `typeof ts === "number"` 转换
3. **/api/memory 默认读 m1 空线程**: 运行时 touch 出空 m1.jsonl,记忆面板永远空白 → 不传 thread_id 时读所有线程
4. **agent 输出 markdown 未渲染**: react-markdown 默认不支持 GFM 表格 → 加 remark-gfm
5. **ReAct 工具调用停顿无反馈**: search_jobs/rag_query 期间不流 chunk → ThinkingPulse(2 秒无 chunk)
6. **初始化 10-30s 无提示**: BGE-M3+Milvus 懒加载 → InitIndicator + stream_agent timeout 30s→120s
7. **NDJSON 换行缺失**: 事件间无 `\n` 前端按行解析错乱 → 所有事件末尾加换行
8. **画像空值字段不保存**: 前端 `if(field)` 跳过空字段,后端白名单约束 → 前端始终发送全字段(""/[]/null 对应清空)
9. **max_iterations=8 超限截断**: agent 反复搜索找不到完美匹配,8 轮耗尽返回半截话 → 提到 15 + prompt 限制最多搜 2-3 轮 + 超轮次兜底补结论

## 真实岗位数据源(mcp-jobs,只抓猎聘)

**方案演进**: 最初用 mock 数据(search_jobs 硬编码 8 岗)。验证了 4 个真实方案后**只保留猎聘**:
- **mcp-jobs**(Playwright 爬猎聘): ✅ 稳定返回 40+ 条真实岗位 → **采用**
- **Boss直聘**(mcp-jobs zhipin + bb-browser 登录态): ❌ headless 被反爬挡(0 岗),bb-browser 登录 tab 不稳定 → 弃
- **牛客网直接抓取**(requests 解析 `__INITIAL_STATE__`): ❌ 连续请求后被 IP 限流 → 弃
- **Exa 语义搜索**(已有 exa_jd.md): ✅ 可靠 → 知识库补充用

**现状**:
- `careercrew_core/tools/jobs/mcp_jobs.py`: Python 封装调用 mcp-jobs(每次现连现调,约 1-2 分钟/次,返回真实岗位)
- `mcp-servers/run-mcp-jobs.js`: 包装器,**屏蔽 stdout 日志污染 MCP 协议 + 把 jobSearchUrls 裁成只留猎聘**
- `search_jobs` 工具: 调 mcp_jobs 实时搜索猎聘,**mock 数据(_MOCK_JOBS)已删除**
- `mcp-servers/`: mcp-jobs Node 服务器;node_modules gitignore
- agent prompt 更新: 搜索约 1-2 分钟,一次给足方向;限制搜 2-3 轮

## 知识库整理(只留最新/非通用)

按"LLM 已知道的知识不算知识库"原则,手写 seed 文档已全部移除:
- **第一轮删除 8 个**: exa_jd / exa_llm_fundamentals / exa_langgraph / exa_chunking /
  exa_negotiation / exa_agent_interview / exa_resume / exa_java_llm(均为 LLM 已知通用知识)
- **第二轮(2026-08-11)删除剩余 3 个**: exa_rag_interview / exa_career_planning /
  exa_interview_experience —— `data/knowledge/` 目录整体不再参与入库,
  **知识库只含 `data/uploads/`**(用户上传 + Web/MCP 上传的 PDF/图片/docx 等)

> ⚠️ **重建索引**: 删除文档后需清空 Qdrant collection(`careercrew_mm`)或删点后重传,
> 运行时 `_ensure_heavy` 只在 `store.count()==0` 时自动入库,不清空则旧向量仍在。

## 前端相对 spec 的增强(基于用户反馈)

- 对话历史管理: 侧边栏线程列表 + LLM 标题生成 + 删除确认
- 页面保持挂载: 流式跨导航不中断
- JD 选择器: 改为纯输入框(匹配结果是 markdown 表格,拆候选不可行)
- 数据看板: 格式化展示 + 画像编辑 + 知识库上传/列表/删除(原轨迹面板已移除)
- agent 身份色系 + 本地字体打包
