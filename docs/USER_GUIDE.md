# CareerCrew 使用指南

> 面向本机单机部署（Windows + Docker Desktop）。最近一次更新：2026-09-16。

## 一、环境准备

| 组件 | 版本/位置 | 说明 |
| --- | --- | --- |
| Docker Desktop | 已启动 | 提供 PostgreSQL 16 与 Qdrant v1.19.0 |
| Python 环境 | uv 管理：项目内 `.venv`（`uv sync --all-extras` 建立，`uv.lock` 锁定版本） | 后端与脚本用 `uv run ...` 执行 |
| Node.js | 仓库内 `careercrew_web/node_modules` 已装好 | 前端开发/构建 |
| 配置文件 | `.env`（本机）+ `config/settings.yaml` | `.env.example` 是可提交的模板 |

## 二、启动与停止

```powershell
# 1) 只起依赖（推荐开发模式）
docker compose up -d postgres qdrant

# 2) 后端（首次会惰性初始化模型，较慢）
cd "<仓库根目录>"
uv run uvicorn careercrew_api.main:app --reload --port 8000

# 3) 前端开发服务器
cd careercrew_web
npm run dev            # http://localhost:5176（strictPort，被占用会直接报错）

# 4) 一键容器部署（含前端构建与迁移）
docker compose up -d --build      # 容器启动先跑 alembic upgrade head，再起 uvicorn
```

| 地址 | 用途 |
| --- | --- |
| http://localhost:5176 | 前端开发界面（`/api` 代理到 8000） |
| http://localhost:8000 | 后端；检测到 `careercrew_web/dist/` 时会顺带托管前端 |
| http://localhost:8000/docs | Swagger UI |
| http://localhost:8000/healthz | 存活探针（免鉴权） |
| http://localhost:8000/readyz | 就绪探针：Postgres + Qdrant 连通性 |
| http://localhost:6333/dashboard | Qdrant 控制台 |

停止：`docker compose down`（数据在命名卷里，不会丢）；`docker compose down -v` 会清空数据，慎用。

## 三、登录与账号

- 本机现有账号：`u_001`（用户名 `liyou`，管理员）。
- 全新环境：开发环境打开登录页可直接创建初始管理员（`POST /api/auth/bootstrap`），生产环境该接口禁用。
- 角色：`admin` / `user` / `quality_reviewer`（质检员可看质检台与评测用例，不碰业务数据）。

## 四、最近优化（第六至八期 + 本轮）

**知识库**

- 上传、解析、分块预览，并支持**单分块编辑**：保存后该版本整体标记为待索引，重新索引成功前旧投影不下线。
- 版本治理：同内容重复检测、来源可信度、失效日期、引用命中统计。
- 发布/下架：下架只改可见性，不动版本状态；重新索引不会把私有内容变回公开。

**长期记忆治理**

- 每条记忆可"确认正确 / 修改 / 暂时忽略 / 过期 / 冲突合并"，保留来源、置信度与变更历史。
- 治理动作是 append-only 审计；账号删除时若有审计事件则软删记录并保留审计链。

**账号与数据清理（本轮新增）**

- 删除账号现在会一并清理长期记忆行（`memory_records` 家族）与 episodic / knowledge / workspace 三处向量副本；清理失败会中止删号，可重试。

**成本与可观测性**

- 按用户/模块的 Token 用量与费用估算、日/月预算、模型降级规则、异常消耗告警。
- Prometheus 指标：请求延迟、SSE 中断、LLM 失败、Qdrant 命中率、上传积压、连接池等待。

**工作台与协作**

- 跨会话语义检索（当前为 `text_fallback`，真正的 embedding 语义检索尚未接入）、消息书签、从某条消息创建分支、回答转行动项。
- 多 Agent 会诊报告（分歧、证据、方案对比矩阵、风险清单、可保存执行计划）、简历母版管理、工具中心（状态/权限/调用记录/逐项启停）。

**运维与发布**

- 迁移纪律：已发布迁移不改，只新增（本轮新增 `0018_eval_case_updated_at`）；CI 校验修订图、校验和与真实 schema invariant。
- 备份/恢复：PostgreSQL dump + Qdrant 快照 + uploads/parsed 归档（含 SHA-256 清单）；主机没有 `pg_dump`/`pg_restore` 时自动回退到容器内执行。
- 本地发布预演：`scripts/release_acceptance.py`（只接受回环地址），含隔离恢复演练。
- **已移除**：生产环境验收（需真实生产目标 + 第三方证据验证服务）与受保护真实模型评测（需评测租户 attestation + 独占写入租约）。离线评测 `--offline` 保留。

## 五、常用操作

**1. 上传并治理知识**

1. 左侧「知识库」→ 上传 PDF/Markdown 等文件，等待解析完成。
2. 进入文档详情：查看分块、编辑单个分块（保存后该版本变为待索引）。
3. 点「重新索引」，等待状态回到 active；此后问答即可引用新内容。
4. 需要暂时不对外时用「下架」，需要恢复用「发布」。

**2. 知识问答**

在知识库对话里提问；答案附引用来源（文档、分块、页码）。范围可选公开/私有/全部。

**3. 记忆治理**

打开「记忆」面板，按事实/关键事件筛选；对存在疑问的条目选择确认、修改、忽略或过期；冲突项可合并。

**4. 个人 API Key**

设置页可写入自己的 DashScope Key（`PUT /api/settings/apikey`），系统只返回掩码，不返回明文。

**5. 预算与用量**

「用量/预算」页查看本人或全站 Token 与费用；管理员可设日/月预算与降级规则。

**6. 工作台**

- 跨会话检索：搜历史消息，可收藏或直接创建行动项。
- 消息分支：从某条回答出发开新分支，不影响原会话。
- 会诊报告：多 Agent 会诊后可保存为报告并导出执行计划。

**7. 备份、恢复演练与本地预演**

```powershell
# 备份（PostgreSQL + Qdrant + uploads/parsed，自动校验并写入 manifest）
uv run python -c "from scripts import backup_restore as b; print(b.create_backup(database_url=..., qdrant_url='http://127.0.0.1:6333'))"

# 本地发布预演（含隔离恢复演练）
$env:CAREERCREW_RELEASE_RESTORE_DRILL = "1"
$env:RESTORE_DATABASE_URL = "postgresql://careercrew:careercrew@localhost:5432/careercrew_restore_control"
$env:RESTORE_QDRANT_URL = "http://127.0.0.1:6333"
uv run python scripts/release_acceptance.py `
  --backup-dir data/backups/careercrew-YYYYMMDD-HHMMSS `
  --report data/reports/local-release-acceptance.json
```

**8. 自检命令**

```powershell
uv run ruff check careercrew_ai careercrew_core careercrew_api careercrew_mcp tests scripts
uv run python scripts/validate_migrations.py --static
uv run python scripts/eval_runner.py --offline --compare data/eval/baseline.json --fail-on-regression
cd careercrew_web; npm run test; npm run lint; npm run build
```

后端全量测试用一次性库（跑完自动删除）：

```powershell
uv run pytest tests -q
# 需要 PostgreSQL 集成用例时，另建一次性库并设置 POSTGRES_TEST_DSN / DATABASE_URL
```

## 六、目录与数据

| 路径 | 内容 | 是否入库 |
| --- | --- | --- |
| `data/uploads/` | 上传原件、附件、头像、简历导出 | 否 |
| `data/parsed/` | 解析产物（页面图、内容 JSON） | 否 |
| `data/backups/` | 备份 run（dump/快照/归档/manifest） | 否（gitignore） |
| `data/reports/` | 测试与验收报告 | 否（未跟踪） |
| `data/eval/` | 离线评测数据集与基线 | 是 |

## 七、排障

| 现象 | 处理 |
| --- | --- |
| 前端启动即退出 | 5176 被占用；`strictPort: true` 不会顺延，释放端口即可 |
| `/readyz` 报 qdrant/postgres 不 ok | `docker compose ps` 看容器状态，必要时 `docker compose up -d postgres qdrant` |
| 报"迁移版本不一致" | 在仓库根目录跑 `alembic upgrade head`（容器部署会自动执行） |
| 检索不到刚上传的内容 | 文档详情里确认版本为 active；必要时「重新索引」 |
| 备份失败提示缺少客户端 | 属正常回退路径：会用 PostgreSQL 容器内的 `pg_dump`；若设置了 `PGHOSTADDR`/`PGHOST` 等变量会被拒绝，清掉再跑 |
| 问答一直失败 | 检查 `.env` 的模型 Key 或设置页里的个人 Key，再看 `logs/` 与 `/api/health` |

## 八、边界

- 这是单机部署指南；没有生产验收脚本，也没有真实模型发布门禁（两者已按决策移除）。
- 跨会话检索目前是文本回退实现，语义检索需要后续接入 embedding 后再启用。
