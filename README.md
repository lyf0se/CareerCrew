# CareerCrew

多智能体职业顾问团队系统 —— 由 LangGraph supervisor 编排职位匹配、简历定制、面试模拟、薪资谈判、职业规划等多个 AI 顾问 agent，围绕自建多模态 RAG、三层记忆与 HITL 人工确认闸门，长期陪跑用户整个求职周期。

> 架构设计、ADR 与排期详见 [docs/DEV_SPEC.md](docs/DEV_SPEC.md)；备份运维见 [docs/OPS_BACKUP.md](docs/OPS_BACKUP.md)。

## 功能介绍

**多智能体协作**

- 6 个专职 agent：职位匹配官 JobMatcher、简历顾问 ResumeAdvisor、面试官 Interviewer、薪资谈判师 SalaryNegotiator、职业规划师 CareerPlanner、知识库顾问 KnowledgeAdvisor
- LangGraph supervisor 按阶段路由（intent / planning / match / resume / interview / negotiate / apply / track / review），agent 节点内由 LangChain 1.x `create_agent` 执行 LLM + 工具循环
- 多顾问会诊：LLM 编排器多轮并行分派 → 各顾问独立意见 → 综合结论；缺少背景资料时可向前端下发资料填写表单
- HITL 闸门：高风险动作（`submit_application` 投递简历等）默认拦截等待用户确认；apply 阶段直接终止图执行
- 内部工具集：`rag_query` / `memory_search` / `memory_write` / `profile_update` / `search_jobs`（CDP 接管 Boss直聘 + 猎聘真实岗位，库缓存优先）/ `salary_query` / `read_image` / `submit_application`

**自建多模态 RAG**

- 文档解析：md / txt 直读；PDF、图片、docx、pptx、xlsx 走 MinerU（默认云端 API 精准解析，可切本地子进程），产出页面 Markdown 与对象裁剪图
- 分块：递归分块（800/100）；可选 Contextual Chunking（LLM 为每块生成文档级上下文前缀，`rag.chunking.contextual` 开启，默认关闭——每块一次 LLM 调用，摄取成本较高）
- 检索：BGE-M3 本地三合一向量（dense + sparse + colbert）写入 Qdrant → 混合召回 → 客户端 RRF 融合 → 硅基流动 rerank 精排（多模态走 Qwen3-VL-Reranker）
- 回答：VLM（GLM-4.5V）看页面/裁剪图作答并返回引用来源；Agentic RAG 支持 kb/web/memory 路由与多跳子查询分解

**三层记忆（仿 Hermes）**

- 短期：Context Window 按 token 裁剪与压缩（compaction，压缩前先抽取关键信息入语义事实）
- 情景：append-only 事件树（Postgres 存储 + Qdrant 向量索引）
- 长期：语义事实 / 用户画像，由 LLM 路由按预算注入每次对话；后台 "Auto Dream" 定期合并去重（记忆系统默认关闭，需显式开启）

**Web 应用（React + FastAPI，NDJSON 流式）**

- 页面：求职规划对话、岗位匹配、模拟面试（逐题评分反馈）、简历定制与简历库、知识库问答与管理、多顾问会诊、**岗位准备**（收藏岗位 / JD 快照 / 简历版本与 PDF·DOCX 导出 / 可解释匹配 / 一键带入简历定制与模拟面试）、**求职中心**（进度看板 / 行动计划 / 项目素材库 / HR 跟进 / Offer 对比 / 面试复盘 / 效果统计 / 数据导出与删除）、个人设置、用户管理（admin）、质检工作台（quality_reviewer）
- 对话体验：流式渲染、停止 / 重新生成（版本切换）、@ 引用知识库文档与简历、聊天附件（≤25MB，每会话 ≤5 个，7 天过期，可转存知识库）、会话置顶 / 重命名 / 清空 / 导出 MD·JSON、会话内搜索、明暗主题；匹配结果以结构化岗位卡片呈现并可一键收藏
- 岗位准备闭环：手动录入或从匹配结果收藏岗位（保存 JD 快照）→ 关联不可变简历版本 → 准备会话（r-prep- / i-prep- 线程自动注入 JD + 简历）→ 看板推进（待准备 / 已投递 / 沟通中 / 面试中 / 收到Offer / 已结束）→ 全程数据 owner 隔离
- 认证与多租户：argon2 密码哈希、JWT access token + HttpOnly 刷新 Cookie 轮换、登录失败锁定、admin / user / quality_reviewer 三种角色、数据按 owner 隔离
- 质量闭环：消息点赞点踩 → bad-case 归因复盘（脱敏快照 + 诊断元数据）→ 升级为评测 case → 离线评测回归门禁

**可观测性**：LangSmith 全链路追踪（LLM / 工具 / ReAct / HITL / RAG / 记忆），默认脱敏上传（截断 + 手机号 / 邮箱 / 薪资打码）

## 技术栈

| 分类 | 技术 |
|------|------|
| Backend | Python 3.12、FastAPI、Uvicorn、Pydantic v2 |
| Agent / 编排 | LangGraph ≥1.2（supervisor + interrupt/Command）、LangChain ≥0.3（`create_agent`）、langgraph-checkpoint-postgres、MCP（`mcp` SDK） |
| AI / LLM | 硅基流动 OpenAI 兼容 API（默认模型 `zai-org/GLM-4.5V`）、BGE-M3 本地 embedding（FlagEmbedding）、`bge-reranker-v2-m3` / `Qwen3-VL-Reranker-8B` 远程精排、MinerU 云端文档解析 |
| Database | PostgreSQL 16（账号 / 会话 / 记忆 / checkpointer 唯一关系库）、Qdrant（唯一向量库，混合 dense+sparse） |
| Frontend | React 19、TypeScript、Vite 8、Tailwind CSS 3.4、Zustand、react-router-dom 7、react-markdown |
| Testing | pytest（pytest-mock / pytest-check / pytest-cov）、Vitest + Testing Library、diff-cover 变更行覆盖率门禁 |
| DevOps | GitHub Actions 分层 CI |
| 第三方服务 | 硅基流动（LLM / rerank / VLM）、MinerU（文档解析）、LangSmith（追踪，可选）、阿里云 OSS（头像存储，可选）、Exa（仅 `scripts/fetch_kb.py` 抓语料用） |

## 项目结构

```text
CareerCrew/
├── careercrew_ai/        # LLM 适配 / BGE-M3 embedding / reranker / Qdrant vector_store / splitter / create_agent 执行链 / prompts
├── careercrew_core/      # supervisor + agents + 三层记忆 + 工具注册表 + 自建 RAG + 会话存储 + 配置加载
├── careercrew_api/       # FastAPI 应用（auth / routers / NDJSON 流式 / 附件 / OSS 头像）
├── careercrew_mcp/       # 自建 MCP server「careercrew-mm-rag」（multimodal RAG 工具）
├── careercrew_web/       # React + Vite 前端（生产构建产物 dist/ 由后端托管）
├── config/
│   ├── settings.yaml        # 主配置（${VAR} 占位符从环境变量替换）
│   └── settings.docker.yaml # 容器部署变体（production 环境口径，与主配置字段保持对齐）
├── scripts/              # 知识入库 / 数据迁移 / 评估 / 清理脚本
├── tests/                # pytest：unit / integration / e2e / api
├── docs/                 # 设计文档（DEV_SPEC / RAG / LangSmith / 前端方案 / 运维备份）
├── data/                 # 运行时数据（uploads / parsed / db / eval；大部分已 gitignore）
├── migrations/           # Alembic 迁移（0001_baseline 为 pg_dump 全量 schema 快照）
├── alembic.ini           # schema migration 唯一入口
├── Dockerfile            # 多阶段构建（builder 装依赖到独立 venv，runtime 携带产物）
├── docker-compose.yml    # 一键编排：postgres + qdrant + app
├── .github/workflows/    # CI 流水线（含 docker build 冒烟 / pip-audit / dependabot）
├── pyproject.toml        # Python 依赖、ruff 与 pytest 配置
└── .env                  # 本地密钥（已 gitignore，勿提交；模板见 .env.example）
```

依赖方向：`careercrew_ai` → `careercrew_core` → `careercrew_api`（单向）。

## 环境要求

- **Python 3.12**（`requires-python = ">=3.12,<3.13"`，其他版本不可用）
- **Node.js 22**（CI 使用版本；前端开发构建需要）
- **Docker**（运行 PostgreSQL 16 与 Qdrant 官方镜像）
- **BGE-M3 本地模型权重**（约 2GB；本地开发修改 `config/settings.yaml` 的 `embedding.model_path`，Docker 部署设置 `CAREERCREW_EMBEDDING_MODEL_DIR`）
- **API Key**：硅基流动（必需）；MinerU（文档解析 `rag.loaders.provider=api` 时必需）
- **Google Chrome**（岗位匹配实时抓取需要：通过 CDP 调试端口接管已登录的 Boss直聘 与 猎聘）

## 快速开始

### 1. 克隆项目

```bash
git clone <repository-url>
cd CareerCrew
```

### 2. 配置环境变量

```bash
cp .env.example .env   # 然后按需填写
```

本地开发必填 `DASHSCOPE_API_KEY` 与 `DATABASE_URL`；容器部署由 compose 注入 `DATABASE_URL`，另需 `AUTH_JWT_SECRET`（≥32 字符）。各变量含义见 [配置说明](#配置说明)，完整模板见 [.env.example](.env.example)。

> `.env` 已被 `.gitignore` 排除（含 `!.env.example` 否定规则），**绝不提交真实密钥**。

### 3. Docker 全栈启动（推荐）

PostgreSQL、Qdrant 和 Web/API 全部由 Compose 管理：

```bash
# .env 至少配置 DASHSCOPE_API_KEY、AUTH_JWT_SECRET，以及模型目录
# CAREERCREW_EMBEDDING_MODEL_DIR=F:/AI_models/BAAI--bge-m3/snapshots/master

docker compose up -d --build
docker compose ps
curl http://localhost:8000/readyz
```

启动完成后访问 <http://localhost:8000>。三个服务应全部为 `healthy`，`/readyz` 应返回：

```json
{"status":"ready","checks":{"postgres":"ok","qdrant":"ok"}}
```

完整部署、持久化卷、更新与迁移说明见 [容器部署](#容器部署)。

### 4. 本地开发：安装依赖

```bash
# 后端（核心依赖 + Web + 测试工具链；含 FlagEmbedding/torch 等重 ML 栈，首次安装体积较大）
pip install -e ".[dev,web]"
# 可选：答案级评估（Ragas）
pip install -e ".[eval]"
```

```bash
# 前端
cd careercrew_web
npm install
cd ..

# （可选）启动 Chrome CDP 调试实例以开启 Boss直聘/猎聘 真实岗位实时抓取：
# 运行脚本自动调起 Chrome，并在打开的页面中分别登录 Boss直聘 与 猎聘 即可：
powershell -ExecutionPolicy Bypass -File scripts/start_chrome_cdp.ps1

# Docker 部署时后端在容器里，无法代你启动宿主机浏览器：请新建一个 Chrome 快捷方式，
# 位置填下面这行，用它打开并登录一次（登录态保存在专用数据目录，只需一次）。
# 详见 docs/OPS_DOCKER_MIGRATION.md §8。
# "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\ChromeDevData https://www.zhipin.com https://www.liepin.com
```

### 5. 本地开发：启动基础服务

本地开发需要从宿主机连接数据库；完整三服务 compose 只让数据库在容器网络内互通，不向宿主机发布端口。因此本地开发可继续用独立容器启动依赖：

```bash
docker run -d --name postgres --restart unless-stopped -p 5432:5432 \
  -e POSTGRES_USER=careercrew -e POSTGRES_PASSWORD=careercrew -e POSTGRES_DB=careercrew \
  -v postgres-data:/var/lib/postgresql/data postgres:16

docker run -d --name qdrant --restart unless-stopped -p 6333:6333 -p 6334:6334 qdrant/qdrant
```

> 完整三服务一键编排见 [容器部署](#容器部署)；已有 PostgreSQL/Qdrant 数据迁移见 [docs/OPS_DOCKER_MIGRATION.md](docs/OPS_DOCKER_MIGRATION.md)。

### 6. 本地开发：启动项目

```bash
# 后端 API（首个请求触发重组件惰性加载，约 10–30 秒属正常）
uvicorn careercrew_api.main:app --reload --port 8000
```

```bash
# 前端开发模式（端口固定 5176，/api 代理到 8000）
cd careercrew_web
npm run dev
```

打开 <http://localhost:5176>，首次使用在登录页创建初始管理员（仅 development 环境可用，对应 `POST /api/auth/bootstrap`）。

**生产模式**：前端构建后由后端单端口托管：

```bash
cd careercrew_web && npm run build   # 产物输出到 careercrew_web/dist
uvicorn careercrew_api.main:app --port 8000   # 检测到 dist/ 即自动托管 + SPA fallback
```

## 配置说明

加载顺序：`.env`（python-dotenv，优先级最高）→ `config/settings.yaml`（`${VAR}` 占位符做环境变量替换）→ pydantic 校验（缺关键字段 fail-fast 抛 `SettingsError`）。主配置分段见 `config/settings.yaml` 注释（llm / embedding / rerank / vector_store / rag / vlm / supervisor / memory / tools / hitl / langsmith / oss / auth）。

`.env` 环境变量：

| 变量 | 必填 | 说明 |
|------|------|------|
| `DASHSCOPE_API_KEY` | ✅ | 阿里云百炼平台密钥，通义千问 LLM / gte-rerank / qwen-vl 调用均使用 |
| `DATABASE_URL` | ✅ | PostgreSQL 连接串（账号 / 会话 / 记忆 / checkpoint 共用） |
| `MINERU_API_KEY` | 视配置 | MinerU 云端解析 token；`rag.loaders.provider: api`（默认值）时必填 |
| `AUTH_JWT_SECRET` | 生产必填 | JWT 签名密钥，生产环境要求 ≥32 字符；development 下缺省时进程内随机回退 |
| `AUTH_DATABASE_URL` | 否 | 认证库独立 DSN，未设置时回退 `DATABASE_URL` |
| `LANGSMITH_API_KEY` | 否 | LangSmith 追踪密钥，缺失时自动禁用追踪 |
| `CAREERCREW_ENV` | 否 | 运行环境覆盖（默认 development；production 会强制校验认证安全配置） |
| `CAREERCREW_AGENT_VERSION` | 否 | agent 版本标记（进入追踪与评测记录） |
| `CAREERCREW_EMBEDDING_MODEL_DIR` | Docker 按需 | 宿主机 BGE-M3 目录，只读挂载到 `/models/bge-m3`；默认 `./models/bge-m3` |
| `CAREERCREW_WEB_PORT` | 否 | Compose 对外映射的 Web/API 端口；默认 `8000` |
| `PIP_PROXY` | 构建按需 | 镜像构建时传给 pip 的代理；Docker Desktop 可填 `http://host.docker.internal:7890` |
| `OSS_ENDPOINT` / `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` / `OSS_BUCKET_NAME` | 否 | 阿里云 OSS 头像存储；任一缺失则回退本地 `data/uploads/avatars/` |
| `POSTGRES_TEST_DSN` | 测试 | 集成测试使用的数据库连接串 |

## 服务地址

| 服务 | 地址 |
|------|------|
| Frontend（开发模式） | <http://localhost:5176> |
| Backend / API（同时托管前端生产构建） | <http://localhost:8000> |
| Swagger UI | <http://localhost:8000/docs> |
| ReDoc | <http://localhost:8000/redoc> |
| OpenAPI Schema | <http://localhost:8000/openapi.json> |
| Liveness 探针（无鉴权） | <http://localhost:8000/healthz> |
| Readiness 探针（Postgres/Qdrant 连通性，无鉴权） | <http://localhost:8000/readyz> |
| 组件级健康明细（需登录） | <http://localhost:8000/api/health> |
| Qdrant（Compose 内网） | `http://qdrant:6333`（HTTP）/ 6334（gRPC） |
| PostgreSQL（Compose 内网） | `postgres:5432` |

## API 文档

FastAPI 默认文档页开启：Swagger UI `/docs`、ReDoc `/redoc`、OpenAPI Schema `/openapi.json`。所有业务路由挂在 `/api` 前缀下；流式接口统一返回 NDJSON（事件类型 `stage` / `chunk` / `agent_start` / `agent_end` / `done` / `error` / `input_request`）。

## 容器部署

仓库提供多阶段 `Dockerfile` 与一键编排 `docker-compose.yml`。Compose 同时管理：

| 服务 | 容器名 | 持久化卷 | 访问方式 |
|---|---|---|---|
| Web / API | `careercrew-app` | `careercrew_app_uploads` | 宿主机 `http://localhost:${CAREERCREW_WEB_PORT:-8000}` |
| PostgreSQL 16 | `careercrew-postgres` | `careercrew_pgdata` | Compose 内网 `postgres:5432` |
| Qdrant | `careercrew-qdrant` | `careercrew_qdrant_storage` | Compose 内网 `http://qdrant:6333` |

### 首次启动

```bash
cp .env.example .env
# 至少填写 DASHSCOPE_API_KEY、AUTH_JWT_SECRET（≥32 字符）
# 设置 CAREERCREW_EMBEDDING_MODEL_DIR 指向宿主机 BGE-M3 目录

docker compose config --quiet
docker compose up -d --build
docker compose ps
curl http://localhost:8000/readyz
```

应用镜像由三个构建阶段组成：Node 编译前端、Python 安装 CPU 版 torch 和完整 ML 依赖、运行镜像组装 venv 与前端 `dist`。构建阶段支持 pip 重试和缓存；国内网络可设置：

```dotenv
PIP_PROXY=http://host.docker.internal:7890
```

### 启动与更新

```bash
# 启动或重建应用
docker compose up -d --build

# 查看状态与日志
docker compose ps
docker compose logs -f app

# 仅重启应用
docker compose restart app

# 停止服务，保留数据卷
docker compose down
```

`docker compose down -v` 会删除 PostgreSQL、Qdrant 和上传文件卷，只能在确认不需要数据时使用。

### 数据与迁移

- PostgreSQL、Qdrant、上传文件和解析产物均落在命名卷中；容器重建不会清空数据。
- app 启动前自动执行 `alembic upgrade head`，迁移失败时容器不会半启动。
- 已有旧版独立 `postgres`、`qdrant` 容器时，不要直接删除旧卷。先按 [PostgreSQL/Qdrant 迁移说明](docs/OPS_DOCKER_MIGRATION.md) 完成逻辑备份、恢复和逐表/逐集合校验。
- 日常备份与恢复演练见 [docs/OPS_BACKUP.md](docs/OPS_BACKUP.md)。

Compose 中的数据库默认不向宿主机发布端口。需要执行 SQL 时：

```bash
docker compose exec postgres psql -U careercrew -d careercrew
```

裸 `docker build -t careercrew .` 也可单独构建镜像；生产域名、反向代理和 Cookie 安全设置见 `config/settings.docker.yaml`。

## 数据库

- **PostgreSQL 16** 是唯一关系库：账号（`auth_accounts` 等 4 张认证表）、会话（`conversations` / `conversation_turns` / `messages` / `agent_runs` 等）、记忆（情景事件 / 语义事实 / 记忆策略）、聊天附件、LangGraph checkpoint。
- **Schema 迁移统一走 Alembic**：根目录 `alembic.ini` + `migrations/`（0001_baseline 为 pg_dump 全量快照，24 表）。容器部署在应用启动前自动 `alembic upgrade head`；本地手动执行 `alembic upgrade head` 即可初始化。各 store 保留惰性建表作为开发兜底，并有双库一致性守卫测试（`tests/integration/test_alembic_baseline.py`）拦住漂移——新增字段一律走新 migration。
- **Qdrant** 集合 `careercrew_mm`（知识库）、`careercrew_episodic_v2`（情景记忆）与 `careercrew_workspace_messages`（工作区语义检索）由应用自动创建。
- Docker 数据位于 `careercrew_pgdata`、`careercrew_qdrant_storage`、`careercrew_app_uploads`；不要通过复制运行中的数据库目录迁移。
- 历史数据迁移脚本见 `scripts/migrate_*.py`（默认 dry-run，`--apply` 生效）；PostgreSQL dump、Qdrant snapshot 与文件哈希备份流程见 [docs/OPS_BACKUP.md](docs/OPS_BACKUP.md)。

## 测试

后端（pytest，marker 定义见 `pyproject.toml`）：

```bash
pytest -q tests/unit/                        # 单元测试（90 个文件，无需外部服务）
pytest -q tests/api                          # API 测试（FakeRuntime 注入，但需本机 Postgres 在跑）
pytest -q -m integration                     # 集成测试（需环境变量 POSTGRES_TEST_DSN）
pytest -q -m "integration or e2e"            # 含求职闭环 e2e
```

marker：`integration`（多组件集成）/ `e2e`（端到端）/ `slow`（慢测试）/ `web`（FastAPI 测试，CI 的 api job 每次运行）。

前端（Vitest，测试文件与源码同目录 `*.test.ts(x)`）：

```bash
cd careercrew_web
npm test          # vitest run
npm run lint      # oxlint
npm run build     # tsc -b && vite build（类型检查随构建）
```

评测回归门禁（CI 中同样执行）：

```bash
python scripts/eval_runner.py --offline --compare data/eval/baseline.json --fail-on-regression
```

## 开发规范

- **Commit**：历史提交遵循 Conventional Commits（`feat:` / `fix:` 前缀）；无正式 CONTRIBUTING 文档
- **Python 静态检查**：ruff（配置在 `pyproject.toml [tool.ruff]`，CI typecheck job 执行 `ruff check` + `compileall`）；mypy 渐进接入见 docs/TECH_DEBT_PLAN.md
- **前端**：oxlint 做 lint，`tsc -b` 随构建做类型检查
- **CI**（GitHub Actions）：push main / PR 触发 unit、api（Postgres 服务容器 + 覆盖率）、postgres-memory、typecheck、frontend、eval-sanity、docker-build（镜像构建 + 容器内 Alembic 迁移冒烟）、security-audit（pip-audit）八类任务 + diff-cover 变更行覆盖率 ≥80% 门禁；dependabot 周检五类生态依赖；nightly 定时跑 integration / e2e（阻塞口径，真实模型评测除外）
- **分支规范**：待补充

## 常见问题

| 现象 | 原因与处理 |
|------|-----------|
| `docker compose up` 报 8000 端口占用 | 本机已有服务占用 `8000`；停止旧进程，或在 `.env` 设置 `CAREERCREW_WEB_PORT=其他端口` |
| 前端启动报端口占用退出 | Vite 配置了 `strictPort: true`（5176 固定），释放端口或改 `vite.config.ts`（注意同步 `auth.trusted_origins`） |
| 后端启动即抛 `SettingsError` | `.env` 缺少必填变量（`DASHSCOPE_API_KEY` / `DATABASE_URL`），或 `config/settings.yaml` 字段非法 |
| Docker 构建下载 PyPI 超时 | 在 `.env` 设置 `PIP_PROXY=http://host.docker.internal:7890` 后重新执行 `docker compose build app` |
| 宿主机无法连接 `localhost:5432/6333` | Compose 的数据库仅在内部网络发布；使用 `docker compose exec postgres psql ...`，或按本地开发方式运行独立容器 |
| 接口返回 503「AI 服务暂不可用」 | Qdrant / Postgres 未启动，或重组件初始化失败；确认两个容器在跑后重试 |
| 首个请求卡住 10–30 秒 | 正常现象：embedding 等重组件按需惰性加载 |
| 启动时报 BGE-M3 模型路径错误 | `config/settings.yaml` 的 `embedding.model_path` 默认是开发者本机路径，改为本地实际权重路径 |
| 登录提示锁定 | 连续失败 5 次锁定 15 分钟（按用户名+IP 计数），稍后再试 |
| `search_jobs` 无法获取岗位或提示未配置 | 职位搜索优先读本地 jobs 库缓存；实时抓取使用已登录 Chrome 的 CDP 调试通道（端口 9222）。本地直跑用 `scripts/start_chrome_cdp.ps1`；Docker 部署需在宿主机用带 `--remote-debugging-port=9222` 的快捷方式启动 Chrome（见 `docs/OPS_DOCKER_MIGRATION.md` §8）。职位匹配页的采集器卡片会显示具体不可用原因 |
| 文档 / 简历解析失败 | `MINERU_API_KEY` 未配置（`provider: api` 时必需），或文件超出大小上限（简历 20MB / 知识库 50MB / 附件 25MB） |
| 国内访问百炼 / LangSmith 超时 | 配置代理（如 Clash `http://127.0.0.1:7890`）后重试 |

## 安全说明

- `.env` 已被 `.gitignore` 排除，**绝不提交**其中的 API Key、数据库密码、OSS AccessKey、JWT 密钥；SSH 私钥同理不应出现在仓库中
- 所有敏感配置通过 `${VAR}` 占位符从环境变量注入 `config/settings.yaml`，不要把真实值写进代码、配置文件或文档
- 生产部署必须：设置 `CAREERCREW_ENV=production`（启动时强制校验 `AUTH_JWT_SECRET` ≥32 字符）、`auth.cookie_secure` 改为 `true`、收紧 `auth.trusted_origins`
- 初始管理员仅能经 bootstrap 接口创建（限 development 环境）；管理员开户时密码留空则默认 `123456` 并强制首次登录修改，自定义密码则可直接登录
- 敏感信息建议统一使用环境变量或 Secret 管理服务注入，避免明文落盘

## License

`pyproject.toml` 中声明为 **MIT**；仓库根目录暂无 LICENSE 文件（待补充）。
