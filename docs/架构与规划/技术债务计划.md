# CareerCrew 工程优化执行计划（TECH_DEBT_PLAN）

> 来源：2026-08 外部评审方案，已于 2026-08-22 对照当前代码逐项复核。
> 行号以复核当日为准；后续漂移以符号名搜索定位。
> 执行方式：按优先级逐项落地，每项完成后勾选并在验收栏记录结果。

## 总览

| # | 问题 | 优先级 | 状态 | 关键文件 |
|---|------|--------|------|----------|
| 1 | CI 未跑全量单元测试 | P0 | ✅ | `.github/workflows/ci.yml` |
| 2 | Python 侧无 lint / 类型检查 | P0 | ✅ | `ci.yml`、`pyproject.toml` |
| 3 | 三种 DB 连接模式均非池化 | P1 | ✅ | `conversation/db.py`、`memory/db.py`、`auth/store.py` |
| 4 | LLM 无超时、reranker 静默失败 | P1 | ✅ | `llm_adapter.py`、`siliconflow_vl_reranker.py` |
| 9 | Auto Dream 无后台调度器 | P1 | ✅ | `main.py`、`data.py`、`consolidation.py` |
| 10 | 硬编码 `user_id="u_001"` 默认值地雷 | P1 | ✅ | `memory/*`、`workflow/job_cycle.py` 等 8 处 |
| 5 | search_jobs 每次调用 spawn 子进程爬取 | P2 | ✅ | `tools/internal/search_jobs.py` |
| 7 | supervisor 图为死代码，架构声明失真 | P2 | ✅ | `supervisor/{router,graph}.py`、DEV_SPEC |
| 8 | Schema 演进靠散落 DDL，无 Alembic | P2 | ✅ | 32 处 DDL |
| 6 | runtime.py 上帝对象（2444 行） | P3 | ✅ | `careercrew_api/runtime/` 包（9 文件） |

---

## P0 正确性

### #1 CI unit job 跑全量单元测试

**现状（已核实）**
- `ci.yml:29-33`：unit job 只跑 `test_config_loading.py` 与 `test_ai_base_factories.py`。
- `tests/unit/` 共 85+ 个文件，绝大多数仅在本地运行；CI 绿 ≠ 代码没坏。
- nightly（`ci.yml:179-215`）仅补 integration/e2e 且 `continue-on-error: true`。

**修法**
1. unit job 改为 `pytest tests/unit -q`。
2. 核对轻量依赖集：现装 langgraph/langchain/qdrant-client 等，需确认全部单测可在该集合下通过；缺的补进 install 列表（如 `psycopg[binary]`、`fastapi` 视测试 import 而定）。
3. 无法离线跑的个别测试用 marker 显式排除并留 TODO，不允许静默 skip。

**验收**：CI unit job 输出的 collected 数 ≈ 本地 `pytest tests/unit --collect-only -q` 数；PR 上全绿。

### #2 接入 ruff（+ 渐进式 mypy）

**现状（已核实）**
- `ci.yml:111-120`：typecheck job 实际只有 `python -m compileall`，仅查语法。

**修法**
1. `pyproject.toml` 加 `[tool.ruff]`：line-length 对齐现有风格，select 默认规则集（E/F/W/I/UP/B），exclude `.venv`、`careercrew_web`。
2. 全仓 `ruff check --fix` + `ruff format` 一次性治理，单独成 commit 便于 review。
3. CI typecheck job 改为 `ruff check . && ruff format --check .`。
4. （可选后续）mypy 渐进：先只覆盖 `careercrew_ai/core` 公共签名，`--ignore-missing-imports`。

**验收**：typecheck job 变红能拦住真实问题（如未定义变量）；存量代码格式化后全绿基线。

**实际落地（2026-08-22）**
- `[tool.ruff]`：select E4/E7/E9/F/W/I/UP/B；ignore B008（FastAPI 惯用法）、UP040（PEP 695 有运行时语义）、E741（存量单字母变量，渐进清理）。line-length=100 仅作格式化参考，**不启用 E501 门禁**（中文注释按显示宽度计宽会大量误报）。
- 存量治理：autofix 215 处（import 排序/未用导入/pyupgrade），手工修复 36 处；其中揪出真 bug：
  - `tests/fixtures/mock_mcp_server.py:43` 引用未定义的 `params`（应为 `name`）；
  - `routers/auth.py` delete_user 引用未导入的名字（并发 WIP，已随最新版适配）；
  - `auth/service.py` 的 `AccountExistsError` 是被 routers 转口的重导出，改用冗余别名显式声明。
- CI typecheck job = `ruff check` + 保留 compileall。mypy 未接（可选项，后续渐进）。
- 验证：`uvx ruff check`（含 tests/scripts）全绿；本地 unit 566 passed、api 214 passed。
- ⚠️ 遗留（非本次引入）：WIP 并发开发中的 `test_admin_deletes_user_and_data` 失败——delete_user 用 `get_runtime()` 直构真实 Runtime 且业务清理先于 SelfAdmin 保护校验执行，测试环境触发 500。建议保护校验前置或恢复 DI 注入 FakeRuntime。

---

## P1 性能与安全

### #3 统一 psycopg_pool 连接池

**现状（已核实）**
- `conversation/db.py:296 _connect()`：每次 CRUD 新建连接（`with self._connect()` 数十处）。
- `memory/db.py:152-209`：进程级单条长连接 `self._conn` + `write_lock RLock`——所有用户写串行，断连无自动恢复。
- `auth/store.py:212 _connect()`：每操作一连接。

**修法**
1. 引入 `psycopg_pool.ConnectionPool`（依赖已有 `psycopg[binary]`，新增 `psycopg-pool`）。
2. 三个 store 共享一个 pool（由 API 层构造后注入），各 store 内部 `_connect()` 改为从池借还，调用点不动。
3. `MemoryDb` 保留 `write_lock` 保护事务语义不变，但去掉"独木桥"长连接；池参数：min_size=1, max_size=10, timeout=30。
4. 兼容路径：允许构造函数直接传 DSN 时内部自建 pool（脚本/测试零改动）。

**验收**：
- 单测：mock pool 验证借还逻辑；
- 集成：并发 20 写入 memory 不再串行阻塞，断连后自动重连（kill 后台 postgres 连接验证）。

**实际落地（2026-08-22）**
- 新增 `careercrew_core/pg_pool.py`：进程级共享池注册表（按 DSN 记忆化），三个 store 天然共享；min=1/max=10/checkout 超时 30s。
- `PostgresConversationDb._connect` / `AccountStore._connect` 改返回 `pool.connection()` 上下文管理器——调用点的 `with self._connect() as conn[, conn.transaction()]` 语义不变（退出提交/回滚，连接归还而非关闭），数十处调用点零改动。
- `PostgresMemoryDb`：拆除进程级单条长连接；`_synchronized` 包装器统一"持 write_lock → 借池连接 → 执行 → 归还"，方法体零改动（`_ensure()` 从 thread-local 取当前借出连接）；惰性 DDL 冻结到首次借出时执行。write_lock **有意保留**：EpisodicMemory.write 的「取 id→找父→插入」跨方法序列依赖它（外层持同一把锁可重入）；因此写路径仍串行——并发度提升需把 id 生成改为 DB sequence，属行为变更，留待后续。换来的核心收益：断坏连接由池自动重建（不再一条 socket 故障拖垮全部记忆操作）、不再有永久占用连接、嵌套调用经核实全部 commit-前-读或入口只读，无脏读窗口。
- 依赖：pyproject 与 CI 四处安装列表补 `psycopg-pool>=3.2`。
- 验证：本地真 PG 一次性库跑 tests/integration——34 passed（3 个失败经 stash 对照确认为存量 quality/eval WIP 问题，与池化无关）；unit+api 790 passed；ruff 绿。

### #4 LLM 超时保护 + reranker 失败可观测

**现状（已核实）**
- `careercrew_ai/llm/llm_adapter.py:31`：`create_llm` 未设 `timeout/max_retries`——上游挂起时 worker 干等到 SSE 层 300s 空闲超时。
- `careercrew_ai/reranker/siliconflow_vl_reranker.py:73-74`：`except Exception: return candidates` 静默吞掉失败，无日志。

**修法**
1. `create_llm` 增加 `timeout=60, max_retries=2`（ChatOpenAI 原生参数）。
2. reranker：`logger.warning("vl_rerank failed, fallback to original order", exc_info=e)`；超时从 60s 降到 15s。
3. 同步检查文本版 `siliconflow_reranker.py` 是否同样处理。

**实际落地（2026-08-22）**
- `create_llm` 加 `timeout=60, max_retries=2`；两个 reranker 统一 `_RERANK_TIMEOUT_S=15` + 失败 `logger.warning`（含 model/候选数/exc_info）。
- 新增测试：VL reranker 超时上界断言、失败 warning+原序回退、top_k 截断语义；文本 reranker 失败留痕；create_llm timeout/max_retries 契约。相关测试 21 passed。

### #9 Auto Dream 后台调度

**现状（已核实）**
- 合并入口唯一：`POST /memory/consolidate`（`data.py:203` → `runtime.py:1967`）。README 宣称"后台定期合并"，实际无任何调度。

**修法**
1. `main.py` lifespan 启动 daemon 线程（不引 APScheduler，标准库足够）：每日低峰（如 04:30 local）对活跃用户调 `Consolidator.consolidate`。
2. 门控复用现有条件（min_interval_hours/min_sessions，`consolidation.py:50` 幂等检查已具备）。
3. 配置项 `memory.dream_schedule`（cron 表达式或 "off"），默认 off 保持行为不变，部署侧显式开启。

**验收**：单测注入假时钟触发一次 consolidate；off 时无线程启动。

**实际落地（2026-08-22）**
- 新增 `careercrew_api/dream.py`：`parse_schedule`（"off"/"HH:MM" 解析）、`dream_due`（到点判定，当日幂等）、`run_dream_cycle`（枚举账号逐个 consolidate，失败计数不中断）、`start_dream_scheduler`（60s 轮询守护线程）。
- 配置项 `memory.consolidation.dream_schedule`（默认 "off"，行为不变；部署侧设 "04:30" 等开启）。
- 接入 `main.py` lifespan，与既有 refresh-session-cleanup 线程共用 stop event。
- 门控复用 `Consolidator.should_run`（min_interval_hours/min_sessions），未到即 gate_not_met 跳过。
- 验证：新增 `tests/unit/test_dream.py` 5 例全过；全仓 ruff 绿。

### #10 消除 user_id="u_001" 默认值

**现状（已核实，非脚本代码 8 处）**
- `workflow/job_cycle.py:27,131`
- `memory/episodic.py:37`、`memory/compaction.py:38`、`memory/semantic.py:30`、`memory/vector_index.py:27`
- `supervisor/consult.py:45`、`tools/internal/profile_update.py:13`

当前所有调用方都显式传参（runtime 注入真实 user_id），暂安全；但任一新代码漏传即静默跨用户读写记忆。

**修法**：以上 8 处默认值改必填（删默认值）；`scripts/` 下 CLI 工具保留默认（人工场景合理）。全仓编译 + 单测兜底。

**实际落地（2026-08-22）**
- 8 处签名全部改必填；`JobCycle.__init__/run`、`Compactor.__init__` 因参数顺序将 user_id 前移。
- 深挖发现并拆除两处比默认值更深的结构性隐患：
  - `CareerCrewRuntime.episodic/fact_store` 全局实例绑定 u_001 且 episodic 从未被读、fact_store 仅一处消费 → 删除死属性，改为按请求构造（cycle 创建处、data/consult 路由），对齐仓库既有模式（runtime:1626/1916、conftest:646）；
  - `MemoryInjector(episodic=...)` 构造参数同样只存不读，生产路径不再传入。
- 顺带修复遗留坏调用：`scripts/eval_langsmith.py` 把 transcript 路径当 db 传给 EpisodicMemory（JSONL→FakeMemoryDb 重写）。
- FakeRuntime / 相关单测同步清理失效赋值与导入。
- 验证：compileall 通过；ruff 全绿；unit + api 共 **785 passed / 0 failed**。

---

## P2 架构债

### #5 jobs 表 + URL 去重 + 增量采集

**现状（已核实）**
- `tools/internal/search_jobs.py` 经 MCP 子进程爬取，单次 1~2 分钟（salary_query 侧 timeout=180s 可佐证）。

**修法**
1. Postgres 加 `jobs` 表（url 唯一索引去重、title/company/city/salary/raw_json/crawled_at/source）。
2. `JobMatcher` 改为读库；采集器独立负责更新（手动 CLI 起步，后台增量采集后续接）。
3. MCP 爬取保留为"采集器实现之一"，与查询路径解耦。

**验收**：重复搜索同一关键词不再触发子进程；库内命中直接返回。

**实际落地（2026-08-22）**
- 新增 `careercrew_core/jobs/store.py`：`JobsStore` 契约 + `FakeJobsStore`（单测）+ `PostgresJobsStore`（复用共享池）。
  - 指纹主键：mcp-jobs 返回无 URL，以 sha1(source|title|company|city) 为去重键；`url` 列留给后续 patchright/CDP 后端。
  - upsert：ON CONFLICT 合并 keywords 数组、刷新 crawled_at/salary/jd；schema 首用时独立小事务建表。
  - search：title/company/jd ILIKE + keywords 精确，默认 7 天新鲜窗口，新采集优先。
- `make_search_jobs_tool(store)` 工厂替换 matcher 的模块级工具注册（runtime `_ensure_heavy` 按 backend 建 store，fake 后端零 PG 依赖）：库内命中直接返回（重复搜索零子进程）；未命中才爬取并入库；MCP 失败/入库失败均有降级路径；store=None 保持旧行为。工具名保持 `search_jobs` 不破坏 HITL/有效工具清单。
- 新增采集器 CLI `scripts/ingest_jobs.py`（手动预热岗位库）。
- 测试：unit 7 例（指纹稳定/去重合并/双路召回/缓存命中不爬/未命中爬取入库/失败降级/None 直连）；PG 集成 3 例（真库去重、召回路径、新鲜度窗口）全过。
- 全量回归：802 passed + ruff 绿（1 个 auth delete 失败为并发 WIP 区偶发，隔离跑通过）。

### #7 supervisor 图定位澄清

**现状（已核实）**
- `supervisor/graph.py build_graph` 仅被 `__init__.py` 导出与单测引用；生产路由实为「HTTP 端点即阶段」。

**决策（采纳方案 a）**：承认现状并让声明变真——
1. DEV_SPEC 明确"端点即编排；supervisor 图用于多阶段自动流转"。
2. 在 `job_cycle` 中真实接入图：match 完成 → 图驱动流转到 resume 生成，作为第一个真实场景。

**验收**：build_graph 出现在生产 import 链路；E2E 走通 match→resume 自动流转。

**实际落地（2026-08-22）**
- `workflow/job_cycle.py::run` 重写为图驱动：以 `{job_matcher, resume_advisor}` 两节点构建 supervisor 图（`build_graph`），match 节点按 select_jd 结果改写 `state.stage`（"resume" 或 "done"，done 不在 STAGE_AGENT_MAP → route 回退 END），条件路由驱动下一跳——「端点即编排」之外第一条真实的多阶段自动流转链路。run_match/run_resume 的历史携带、画像 preamble、空输出兜底全部复用，既有单测/E2E 契约零变更。
- DEV_SPEC 双处更新：特性区与 §3.1.1 增加「编排模式说明」——生产单阶段对话走端点直连；supervisor 图用于多阶段自动流转（M1 闭环已接入），不再让声明悬空。
- 验收达成：build_graph 进入生产 import 链路（runtime→JobCycle.run→supervisor.graph）；tests/e2e/test_match_resume_loop 走通 match→resume 图流转；unit+e2e 585 passed / ruff 绿。

### #8 Alembic baseline

**现状（已核实）**
- 散落 DDL 现 32 处（较上轮评审 44 处有收敛）：conversation/db.py×16、auth/store.py×8、memory/db.py×5、attachments.py×1 及 scripts/tests 各 1。

**修法**
1. 引入 Alembic；将现有惰性 DDL 冻结为 baseline migration（autogenerate 后人工核对）。
2. 之后新增字段一律走 migration；`_ensure` 保留为开发兜底，生产配置关闭。

**验收**：空库 `alembic upgrade head` 建出的 schema 与运行时惰性建表一致（对比测试）。

**实际落地（2026-08-22）**
- 脚手架：根目录 `alembic.ini`（连接串从 DATABASE_URL 读，注释用英文——configparser 在 Windows GBK 下读中文崩）+ `migrations/env.py`（postgresql:// → postgresql+psycopg:// 方言映射）。
- baseline 生成法：对一次性库触发五个 store 的全部惰性建表 → 容器内 `pg_dump --schema-only` 导出权威快照（24 表 / 68 条 DDL）→ 清洗后冻结进 `migrations/versions/0001_baseline.py`。清洗三件事：剔 `\restrict` psql 元命令；剔 `set_config('search_path','',false)`（会话级清空 search_path 会连带弄挂 alembic 自己的 version 表）；upgrade() 先剔除注释行再按分号切分（pg_dump 注释内含分号）。
- **一致性守卫测试** `tests/integration/test_alembic_baseline.py`：一次性双库对照（src=运行时惰性 DDL，dst=`alembic upgrade head`），逐列比对 information_schema（类型/可空/默认值）+ p/u/f 约束指纹（排除 alembic_version）。今后改惰性 DDL 不同步出 migration 会被它拦下。
- 约定：新增字段一律走新 migration；`_ensure` 保留为开发兜底。依赖补 `alembic>=1.13`。
- 验证：真库 upgrade head 成功；一致性测试 passed；全量 803 passed / ruff 绿。临时源库已清理。

---

## P3 重构

### #6 runtime.py 拆分

**现状（已核实）**：`careercrew_api/runtime.py` 2160 行，单类承担重组件初始化、多模块流式编排、线程生命周期、HITL 等。

**拆法（纯搬家不改行为）**
1. `_ensure_heavy` → `HeavyResources` 容器类。
2. chat/match/resume/interview/knowledge/consult 各 `run_*_stream_impl` 按 module 拆 mixin 或独立模块。
3. 线程管理抽 `ThreadService`。目标单文件 ≤600 行。

**约束**：拆分期间对外接口（路由调用的方法签名）零变更；每步搬家跑全量 api 测试。

**实际落地（2026-08-23）**
- `careercrew_api/runtime.py`（2444 行，含另一 agent 并行加入的两级初始化）包化为 `careercrew_api/runtime/`，9 个文件全部 ≤526 行：

| 文件 | 职责 | 行数 |
|------|------|------|
| `__init__.py` | 组装 CareerCrewRuntime（mixin 多继承）+ get_runtime/reset_runtime 单例 + 兼容导出 | 71 |
| `common.py` | 模块级 helper + 异常 + logger | 138 |
| `heavy.py` | HeavyInitMixin：两级惰性初始化（_ensure_stores/_ensure_heavy/_init_heavy_locked） | 181 |
| `lifecycle.py` | TurnLifecycleMixin：轮次生命周期 + 标题 + 线程 CRUD（ThreadService 职责并入） | 291 |
| `streaming.py` | StreamingMixin：match/resume/planner/knowledge 流式 | 526 |
| `regenerate.py` | RegenerateMixin | 298 |
| `tools_agents.py` | ToolsAgentsMixin：agent 工厂/工具装配/HITL/effective tools | 358 |
| `services.py` | ServicesMixin：记忆操作 + consult + health | 163 |
| `knowledge.py` | KnowledgeDocsMixin：文档摄取/知识库/上下文资源/mentions | 304 |

- 对外导入路径零变更（routers/tests 引用的 `CareerCrewRuntime/get_runtime/RuntimeInitError/ResourceNotFoundError/RegenerateConflictError/_rag_query_retrievals` 等经 `__all__` 重导出）。
- 过程教训（已记入执行方式）：首次拆分被并行 agent 的 `_ensure_stores` 改造踩踏（按旧行号清单切割导致错位）——逆向重组恢复后重做；v2 增加「只读快照 + 切割前后 sha256 校验」防并发，并基于重新清点的真实结构定边界。
- 顺带修复：`test_regenerate_runtime._make_runtime` 用 `__new__` 绕过构造器打桩，缺新属性 `_stores_ready` 导致 AttributeError——补桩。
- 验证：compileall ✓、ruff 全绿、unit+api+e2e+关键集成 **805 passed / 0 failed**。

---

## 执行记录

| 日期 | 项目 | 结果 |
|------|------|------|
| 2026-08-22 | #1 CI 全量单测 | unit job 改跑 `pytest tests/unit -m "not integration"`（563 passed/1 skipped/2 deselected 于 CI 等效环境）；补装 fastapi/PyJWT/argon2-cffi/langchain-text-splitters/pymupdf/mcp<2/psycopg/requests；smoke 测试拆分重 ML 栈断言为 skipif |
| 2026-08-22 | #2 ruff 接入 | pyproject 配置 + 存量治理 251 处 + CI typecheck job；顺带修复 fixture 与 WIP「显式密码不强制改密」语义漂移（tests/api 50 errors → 0）；ruff 全绿，779 passed / 1 failed（WIP 遗留，见 #2 备注） |
| 2026-08-22 | #4 LLM 超时 + rerank 可观测 | create_llm timeout=60/max_retries=2；两个 reranker 15s 超时 + warning 留痕；新增 5 例测试 |
| 2026-08-22 | #10 消除 u_001 默认值 | 8 处签名改必填；拆除 runtime 全局 episodic/fact_store 死属性改按请求构造；修复 eval_langsmith 遗留坏调用；785 passed |
| 2026-08-22 | #9 Auto Dream 调度 | 新增 careercrew_api/dream.py + dream_schedule 配置（默认 off）；lifespan 接入；5 例单测；790 passed / ruff 绿 |
| 2026-08-22 | #3 连接池统一 | 新增 pg_pool.py 共享池（按 DSN）；conversation/auth `_connect` 返回池上下文（调用点零改动）；memory 拆长连接改借还制、保留 write_lock 保序列原子性；真库集成 34 passed（3 失败为存量 WIP） |
| 2026-08-22 | #5 岗位库 + 查询解耦 | jobs 表（指纹去重/keywords 召回/新鲜窗口）+ make_search_jobs_tool 库优先工厂 + ingest_jobs 采集 CLI；unit 7 例 + PG 集成 3 例；802 passed / ruff 绿 |
| 2026-08-22 | #7 supervisor 图接入 | JobCycle.run 改由 build_graph 驱动 match→resume 流转（stage 条件路由）；DEV_SPEC 双处诚实声明「端点即编排 + 图驱动自动流转」；585 unit+e2e passed |
| 2026-08-22 | #8 Alembic baseline | alembic 脚手架 + pg_dump 快照冻结 0001_baseline（24 表）+ 双库一致性守卫测试；803 passed / ruff 绿 |
| 2026-08-23 | #6 runtime 拆分 | runtime.py(2444 行) → runtime/ 包 9 文件（全部 ≤526 行），mixin 组装、导入路径零变更；首次遭并行编辑踩踏，v2 以快照+hash 校验重做；805 passed / ruff 绿。**10/10 全部完成** |

---

## 附录：N 批次手动验收清单（需真实 Boss 账号，代码侧已就绪）

### N1 Boss 搜索渠道
1. 启动带调试端口的 Chrome 并登录 zhipin.com：
   chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\chrome-cdp
2. config/settings.yaml 设 	ools.search.boss_cdp_url: "http://127.0.0.1:9222"
3. 对话让 matcher 调 search_jobs（新关键词，确保库内未命中）
4. 验收：返回含 Boss 岗位（source=boss 入库）；故意触发风控页时降级猎聘 MCP 且日志有 warning

### N2 投递变真
1. 同上环境；对话走到投递环节，agent 调 send_greeting(job_url=…)
2. HITL 弹确认 -> 用户确认后执行
3. 验收：Boss 页面收到招呼语气泡；情景记忆新增 type=application status=sent 条目；
   断网/改版场景产生 status=failed 记录且工具返回可重试提示

### E HR 回复监听
1. settings 开 hr_monitor.enabled=true；有 HR 回复后等待一个轮询周期
2. 验收：memory 出现 type=hr_reply 事件，后续对话顾问能引用该回复

### M5/M7（可选开启）
- crag=true：检索偏题问题时观察 LangSmith 中 trail=["incorrect","correct"] 重检链
- colbert_store+colbert_rerank=true：重建知识库索引后检索，验证排序变化（库体积会显著增大）

### 附：猎聘 MCP 渠道「空结果」排障（2026-08-23 实录）
现象：search_jobs 走猎聘渠道返回 0 条且无报错。
根因：mcp-servers 的 Playwright 包升级后浏览器二进制缺失（chromium_headless_shell-*），
爬虫启动即失败但 MCP 层只回空列表。日志在 logs/mcp-jobs.log 有明确报错。
修复：cd mcp-servers && npx playwright install chromium
经验：猎聘返回空结果先查 logs/mcp-jobs.log 再怀疑反爬/登录墙。

---

## 执行记录（2026-08-23 · 六批次全部落地）

| 批次 | 内容 | 结果 |
|---|---|---|
| A | 运维/安全/顺手 8 项（compose·探针·日志·附件清理·限流·contextual 开关·embedding 设备·前端拆分） | ✅ |
| B | N5 四条关键 E2E（match_resume/interview/apply_hitl/dogfood） | ✅ |
| C·D | N1 Boss CDP 渠道 + N2 投递变真（HITL→真实发送→验证→留痕） | ✅ |
| E | HR 回复监听闭环（hr_reply 入情景记忆） | ✅ |
| F | M7 ColBERT 本地精排 + M5 CRAG 自评估（默认关闭，开关启用） | ✅ |

### 排障实录（matcher 空产出三层根因）
1. get_runtime() 不加载 settings → 首批请求工具白名单空 → 六工具全裁（主因，已修）
2. GLM-4.5V 是视觉定位模型：文本场景输出 box token、偶发空回复 → llm 切 DeepSeek-V3.2 / vlm 看图切 Qwen3-VL-32B-Instruct
3. 多轮 ReAct 后 memory_write 收尾 → content=iterations[-1] 丢正文 → 改全量非空迭代拼接

### 遗留（明确延后，非缺陷）
- M7 Qdrant multivector（MAX_SIM 原生）路线：需重建索引；payload 方案够用
- contextual 知识库级开关：现全局开关；KB 粒度待做
- hr_monitor 多账号会话归属映射：单用户部署无影响

### 环境坑备忘
- mcp-jobs 空结果先查 logs/mcp-jobs.log：playwright 二进制缺失用 
px playwright install chromium
- Boss 已改版 /web/geek/jobs：选择器集中在 tools/browser/patterns.py 新旧兼容

### 遗留项更新（2026-08-23）
- ~~hr_monitor 多账号会话归属映射~~ ✅ 已完成：回复按 application 投递事件路由到对应账号（公司归一+职位消歧），并加 (company,message) 轮询去重
- M7 Qdrant multivector：仍延后（需重建索引）
- contextual 知识库级开关：仍延后（现全局开关）

### 遗留项清零（2026-08-23 续）
- ~~M7 Qdrant multivector~~ ✅：vector_store.colbert_multivector 开启 → text_colbert 命名向量（MAX_SIM）+ ColBERTQdrantReranker 服务端打分；顺带修复 payload 循环把 colbert 矩阵字符串化导致本地精排从未生效的历史缺陷。注意：开启后旧数据需重新摄取才有精排效果
- ~~contextual 知识库级开关~~ ✅：rag.chunking.contextual_by_category（{分类id: bool}）覆盖全局默认，摄取管线按 category 逐次判定

三项遗留全部清零。
