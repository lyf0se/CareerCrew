# CareerCrew 多用户账号、权限与知识库完善设计（Design Spec）

> 状态：已获用户计划文档（`已选定的默认规则`）授权，本文件为该计划的落地设计；会话已切换为自主执行（approval=never），后续按本设计直接进入 writing-plans 与实施。
> 基准：`main` @ `8995c8d`，工作目录 `F:\agent_develop\CareerCrew`。

## 1. 背景与目标

单用户时期的数据（Qdrant 向量、简历线程、元数据）已在上一阶段归属 `u_001`（迁移已 apply，changes=232/conflicts=0，幂等复跑 0/0）。本设计覆盖计划步骤 2–6：

1. 账号与刷新会话从 SQLite 迁到 Postgres（SQLite 仅保留给测试/显式本地配置）。
2. 完整管理员用户管理（后端 API + 前端 `/admin/users`）。
3. 私有/公共知识库（`owner_user_id` + `visibility`，统一访问过滤器）。
4. 旧知识目录与脚本清理。
5. 安全加固：登录限速、Cookie 接口 Origin 校验、审计日志、过期会话清理、备份文档。

## 2. 现状事实核对（计划声称 vs 代码）

| 计划声称 | 代码事实 | 处置 |
|---|---|---|
| 已有 `POST /api/auth/users`（admin，403 给普通用户） | ✅ `careercrew_api/routers/auth.py:110-120` | 保留并扩展 |
| 角色仅 admin/user | ✅ `accounts.role CHECK` + `Literal["user","admin"]` | 不变 |
| 业务 API 均要求登录 | ✅ 全部 router 声明 `CurrentUser` | 不变 |
| 账号在 Postgres / 有 AUTH_DATABASE_URL | ❌ 认证 100% SQLite（`auth/service.py` 仅 sqlite3），全仓无 `AUTH_DATABASE_URL` | 从零建设（本设计 §4.1/4.2） |
| 限速/锁定/token_version/审计/Origin 校验 | ❌ 全部缺失 | 新建（§4.5） |
| 知识库 payload 有 user_id | ✅ `user_id` 键，`_to_qid` 把 user 值织入物理 UUIDv5（`qdrant_store.py:89-96`） | 改名 `owner_user_id` + 新增 `visibility`，**物理 ID 编码函数不变**（编码只依赖 user 的*值*，不依赖键名，见 §4.7 关键不变式） |
| 统一访问过滤器 | ❌ 不存在，过滤散落在 `runtime.py`（`_make_tools` 6 处 + delete/status/asset_owned 3 处）与 `knowledge.py` | 新建统一约定（§4.7） |
| 前端有角色守卫 | ❌ 无，`AuthUser.role` 未使用；`DataPage.tsx:133/414/459` 硬编码 `u_001` | 新建（§4.10） |
| `fetch_kb.py` 只写 `data/knowledge` | ✅ 第 36 行；目录仅 1 个 md 且原件已在 `data/uploads/`、向量已归属 u_001 | 改造 + 归档（§4.11） |

其他关键事实：

- access token：HS256 JWT，claims `sub/role/type/iat/exp`；`current_user` 已做 DB role 比对（`service.py:180-195`）。
- refresh token：不透明 `secrets.token_urlsafe(48)`，库存 SHA-256 哈希，已实现轮换 + 复用即失效（`service.py:110-129`）。
- Cookie：`careercrew_refresh`，httponly、samesite=lax、path=/api/auth、secure 由配置控制。
- Argon2：argon2-cffi 默认参数（Argon2id, t=3, m=64MiB, p=4, len=32），未显式固化 → 本设计显式固化参数常量，登录侧 `check_needs_rehash` 已有。
- CORS `allow_origins` 硬编码 `localhost:5175`/`127.0.0.1:5175`（`main.py:26-32`）。
- 情景记忆集合 `careercrew_episodic_v2` payload 用 `user_id`（`memory/vector_index.py:43,62`），**本设计不改名**（记忆天然私有，无 visibility 语义），`upsert` 对两种键名都要兼容。
- 测试：`tests/api/test_auth_api.py`、`test_tenant_isolation_api.py`、`test_knowledge_api.py`、`test_upload_isolation_api.py` 等；`FakeRuntime`/`FakeVectorStore` 为主桩；前端 vitest（jsdom 文件级覆盖）+ oxlint + `tsc -b && vite build`。

## 3. 总体架构

```
浏览器（token 内存 + HttpOnly refresh cookie）
  │  /api（vite proxy → uvicorn :8000）
  ▼
FastAPI
  ├─ auth 依赖：AuthService ── AccountStore（接口）
  │      ├─ PostgresAccountStore  ← AUTH_DATABASE_URL（未配置回退 DATABASE_URL）
  │      │     auth_accounts / auth_refresh_sessions / admin_audit_events / auth_login_attempts
  │      └─ SqliteAccountStore    ← 仅测试或显式 auth.backend=sqlite
  ├─ 业务路由（CurrentUser）── runtime ── QdrantStore（careercrew_mm 知识库）
  │      访问过滤统一：{"__access_user": uid} → (visibility=public) OR (owner_user_id=uid)
  ├─ 中间件：CORS（origins 取自 auth.trusted_origins）
  └─ lifespan：过期/长期吊销 refresh 会话清理任务
存储：Postgres（账号+会话+审计+限速 | 记忆 | checkpoint）· Qdrant（向量）· data/（文件，按 user_id 目录）
```

约束：SQLite 账号文件 `data/db/accounts.db` 迁移后**不删除**（改名归档备份）；运行时默认 postgres 后端，未配置 DSN 直接启动失败（生产与开发一视同仁），测试经 `AuthSettings(backend="sqlite", ...)` 显式用 SQLite。

## 4. 模块设计

### 4.1 账号存储双后端（`careercrew_api/auth/store.py` 新建）

`careercrew_api/auth/service.py` 现有 `AccountStore`（SQLite）重构为「接口 + 两个实现」，统一放 `careercrew_api/auth/store.py`：

```python
class AccountStore(ABC):
    # 查询
    has_accounts() -> bool
    account_by_username(username) -> dict | None      # 含 password_hash/status/token_version
    account_by_id(user_id) -> dict | None
    list_accounts(offset, limit) -> tuple[list[dict], int]   # 管理列表（不含哈希）
    # 写入（内部只接受已哈希密码，绝不透出）
    create_first_admin(username, password_hash) -> dict
    create_account(username, password_hash, role) -> dict
    update_account(user_id, *, role=None, status=None) -> dict
    update_password_hash(user_id, password_hash) -> None
    bump_token_version(user_id) -> int
    # 刷新会话
    create_refresh_session(token, user_id, expires_at) -> None
    rotate_refresh_session(old_token, new_token, expires_at) -> dict | None
    revoke_refresh_session(token) -> None
    revoke_all_refresh_sessions(user_id) -> int
    revoke_other_refresh_sessions(user_id, keep_token_hash) -> int
    delete_expired_refresh_sessions(revoked_older_than_days=30) -> int
    # 审计
    add_audit_event(actor_id, action, target_user_id, context: dict) -> None
    # 登录限速
    record_login_failure(key) -> tuple[bool, datetime | None]   # (is_locked, locked_until)
    clear_login_failures(key) -> None
```

- `PostgresAccountStore`（`store.py`）：psycopg 3；构造时 `CREATE TABLE IF NOT EXISTS` 四张表；写入用事务（`with conn.transaction()`）；用户名冲突映射 `psycopg.errors.UniqueViolation` → `AccountExistsError`。
- `SqliteAccountStore`（`store.py`）：从现有 `AccountStore` 迁移而来，表结构**升级**（加 status/token_version 列，`ALTER TABLE ... ADD COLUMN` 幂等），仅测试/显式配置使用。
- 兼容列名：`id, username, password_hash, role, status, token_version, created_at, updated_at`。

### 4.2 AuthSettings 扩展与后端选择（`careercrew_core/state/settings.py`）

新增字段（yaml `auth:` 段）：

```yaml
auth:
  environment: development
  backend: postgres            # postgres | sqlite（sqlite 仅测试/显式本地配置）
  database_url: "${AUTH_DATABASE_URL}"   # 空则回退 ${DATABASE_URL}；两者皆空且 backend=postgres → SettingsError
  trusted_origins: ["http://localhost:5175", "http://127.0.0.1:5175"]
  login_max_failures: 5        # 窗口内失败次数
  login_lock_minutes: 15       # 锁定时长
  login_failure_window_minutes: 15
  cleanup_interval_hours: 6    # 过期会话清理周期
  account_db_path: "./data/db/accounts.db"   # 仅 backend=sqlite 时生效
```

`AuthSettings` 增加 `database_url: str = ""`、`backend: str = "sqlite"`、`trusted_origins: list[str]`、限速三参数、`cleanup_interval_hours: int = 6`。

`load_auth_settings()` 校验：
- `backend == "postgres"` 时 `database_url` 必须非空（`${AUTH_DATABASE_URL}` 未设置会替换为空串，再回退读 `os.environ["DATABASE_URL"]`），否则 `SettingsError`。
- `backend == "sqlite"` 仅允许在 `environment ∈ {development, test}` 显式声明时使用；生产环境 backend 必须为 postgres。
- `trusted_origins` 非空；生产校验维持（jwt_secret≥32、cookie_secure=true）。

### 4.3 认证服务扩展（`careercrew_api/auth/service.py`）

保持现有端点行为不变的前提下新增：

- **Argon2 参数显式固化**：`PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, type=Type.ID)`。
- **JWT 增加 `tv` claim**（`_token_response` 里写入 `"tv": account["token_version"]`）；`current_user()` 校验：
  `claims["type"]=="access" and user["status"]=="active" and user["role"]==claims["role"] and user["token_version"]==claims["tv"]`，任一不符 → `AuthenticationError`。`account_by_id` 需返回 `status/token_version`。
- **登录**：先限速检查（§4.5），账号不存在与密码错误**统一计数**并返回同一错误文案；成功清零计数；`status=="disabled"` 直接拒绝。
- **刷新**：`rotate_refresh_session` JOIN 账号并加 `status='active'` 条件 → 禁用用户无法刷新。
- **用户管理**（service 层方法）：
  - `list_users(page, page_size)`（仅 admin 调用）
  - `update_user(actor, user_id, role=None, status=None)`：守卫规则——不能改自己；不能把最后一名 active admin 改为非 admin 或 disabled（事务内 `SELECT COUNT(*) WHERE role='admin' AND status='active'`）；改动后 `bump_token_version`；禁用时 `revoke_all_refresh_sessions`；角色变更同样 `bump_token_version`（旧 access 立即失效）。
  - `admin_reset_password(actor, user_id, new_password)`：置新哈希 + `bump_token_version` + `revoke_all_refresh_sessions`。
  - `change_own_password(user, old_password, new_password)`：校验旧密码 → 置新哈希 + `bump_token_version` + `revoke_other_refresh_sessions(user_id, keep=当前会话哈希)`。
  - 所有 admin 管理动作写 `admin_audit_events`（action ∈ `user.create/user.update/user.reset_password`），context 仅含脱敏字段（如 `{"role": "user", "fields": ["status", "role"]}`），**绝不写密码/令牌/请求体**。
- 不提供硬删除。

### 4.4 认证 HTTP 端点（`careercrew_api/routers/auth.py` + `schemas.py`）

| 端点 | 鉴权 | 请求 | 响应 | 错误 |
|---|---|---|---|---|
| `POST /api/auth/token`（既有） | 公开 | CredentialsRequest | TokenResponse + refresh cookie | 401 / **429（锁定）** |
| `POST /api/auth/refresh`（既有） | cookie | — | TokenResponse + 新 cookie | 401 |
| `POST /api/auth/logout`（既有） | cookie | — | 204 | — |
| `GET /api/auth/me`（既有） | user | — | PublicUser | 401 |
| `POST /api/auth/users`（既有） | admin | CreateUserRequest | PublicUser | 403/409 |
| `GET /api/auth/users` | admin | `page`, `page_size`（默认 1/20，上限 100） | `{items: [AccountListItem], total, page, page_size}` | 403 |
| `PATCH /api/auth/users/{user_id}` | admin | `UserPatchRequest{role?, status?}`（至少一项） | AccountListItem | 400/403/404/409 |
| `POST /api/auth/users/{user_id}/reset-password` | admin | `PasswordResetRequest{password}` | `{"ok": true}` | 403/404 |
| `POST /api/auth/password` | user | `ChangePasswordRequest{old_password, new_password}` | `{"ok": true}` | 400/401 |

Schema（`schemas.py` 新增）：

```python
class AccountListItem(BaseModel):
    id: str; username: str; role: Literal["user","admin"]
    status: Literal["active","disabled"]; created_at: str; updated_at: str

class UserPatchRequest(BaseModel):
    role: Literal["user","admin"] | None = None
    status: Literal["active","disabled"] | None = None
    # validator: 至少一项；禁止 admin 用该接口改自己（service 层双保险）

class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=12, max_length=256)

class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)
```

`PublicUser` 保持 `{id, username, role}` 不变（前端 token 响应不破坏）；管理接口用 `AccountListItem`。

### 4.5 安全加固

1. **登录限速**（`careercrew_api/auth/rate_limit.py` 新建）：
   - 键：`login:{username}` 与 `login:{client_ip}` 双键；任一命中锁定即 429，`Retry-After` 头返回剩余秒数。
   - 存储：`auth_login_attempts(key TEXT PK, failures INT NOT NULL DEFAULT 0, window_start TIMESTAMPTZ, locked_until TIMESTAMPTZ NULL, updated_at TIMESTAMPTZ)`，Postgres 内 `INSERT ... ON CONFLICT (key) DO UPDATE` 原子增减；SQLite 实现同语义（测试用）。
   - 判定：`failures >= login_max_failures` 且距 window_start 在窗口内 → 置 `locked_until = now + login_lock_minutes` 并返回锁定；超过窗口则重置计数；成功登录清空两键。
   - 不锁定账号实体（仅限速），与 `status=disabled` 正交。
2. **Cookie 接口 Origin 校验**（`careercrew_api/auth/middleware.py` 新建，HTTP 中间件）：
   - 仅对 `POST /api/auth/refresh`、`POST /api/auth/logout` 生效；若请求带 `Origin` 头且值不在 `auth.trusted_origins` → 403；无 Origin 头（非浏览器客户端/同源 GET）放行。
   - CORS `allow_origins` 改用 `auth.trusted_origins`（`main.py`）。
3. **审计**：`admin_audit_events`（§4.1）写入 admin 动作；提供 `GET /api/auth/users` 之外不暴露查询接口（审计仅落库，避免过度设计）。
4. **过期会话清理**：`main.py` lifespan 启动 asyncio 任务，每 `cleanup_interval_hours` 调用 `delete_expired_refresh_sessions()`（删除 expires_at 已过，或 revoked_at 超过 30 天的行）。清理函数独立可测。
5. **备份/恢复文档**：`docs/OPS_BACKUP.md`（新建）：accounts（Postgres 表清单 + `pg_dump` 示例）、Qdrant snapshot 创建/恢复、记忆/checkpoint Postgres、`data/` 文件目录、恢复演练步骤。

### 4.6 SQLite → Postgres 账号迁移（`scripts/migrate_accounts_postgres.py` 新建）

- 参数：`--sqlite-db`（默认 `data/db/accounts.db`）、`--postgres-dsn`（默认读 settings 的 `auth.database_url`）、`--apply`；默认 dry-run 只打印统计。
- 行为：读 SQLite `accounts`（含 status/token_version 列缺失时按默认 active/0）→ 逐条 `INSERT ... ON CONFLICT (id) DO NOTHING` 到 `auth_accounts`；**不迁移 refresh_sessions**（计划明确：旧刷新令牌全部失效，迁移后全员重新登录）。
- 幂等：目标已存在同 id 且 username/role 一致 → skip；不一致 → conflict 计数，不覆盖。
- 迁移成功后打印提示并建议把 `data/db/accounts.db` 改名归档（脚本加 `--archive-sqlite` 选项做 `accounts.db → accounts.db.pre-postgres-<ts>.bak`，仅 apply 时执行）。
- 验收：u_001 密码原哈希原样迁移，旧 refresh cookie 失效（未迁移会话），登录后正常。

### 4.7 知识库 payload 与统一访问过滤器（`careercrew_ai/vector_store/qdrant_store.py`）

**payload 约定（知识库集合 `careercrew_mm`）**：

| 键 | 含义 |
|---|---|
| `owner_user_id` | 上传者（替代 `user_id`） |
| `visibility` | `"private"` \| `"public"`（默认 private） |
| `doc / source / category / page / type / image_path / bbox / _id / text` | 不变 |

**关键不变式**：`_to_qid` 编码函数与两个 namespace 常量**不变**；物理 ID 只依赖 owner 的*值*，键名改名不影响已有 215 点的物理 ID（同一 `u_001` 值 → 同一 UUID）。`upsert` 的 owner 解析改为：

```python
owner = str(payload.get("owner_user_id") or payload.get("user_id") or "")
```

（情景记忆集合继续写 `user_id`，upsert 兼容两种键名。）

**访问过滤器统一约定**：在 `_filter_expr` 与 `FakeVectorStore._matches` 中支持保留键 `"__access_user": <uid>`：

```
可见 ⟺ (visibility == "public") OR (owner_user_id == <uid>)
```

- `_filter_expr`：`__access_user` 键不进入 must，单独构造 `should=[FieldCondition(visibility=MatchValue("public")), FieldCondition(owner_user_id=MatchValue(uid))]`，与其余 must 键合并（Qdrant `Filter(must=..., should=...)` 语义 = must AND should）。
- `base_vector_store._matches` 同步实现该保留键语义，供 FakeVectorStore 使用。
- 显式范围过滤直接写普通键：公共库 `{"visibility": "public"}`；个人库 `{"owner_user_id": uid}`；与 `__access_user` 不混用（文档约定）。

**新增/调整方法**：

- `_ensure_collection`：载荷索引追加 `owner_user_id`、`visibility`（try/except 幂等）。
- `set_payload_by_filter(payload: dict, filters: dict) -> int`：scroll 收集点 id → `set_payload`（发布/下架用）。Fake 同步实现。
- `list_docs(filters)`：聚合键改为 `(doc, visibility)`，条目增加 `visibility`、`owner_user_id` 字段（公共/私有同名文档不再合并）。
- `delete_by_metadata` / `metadata_exists` / `count` 不变（过滤语义由 `_filter_expr` 升级）。

**调用点改造**（`careercrew_api/runtime.py`）：

- `ingest_document(..., visibility: str = "private")`：`owner_metadata = {**(metadata or {}), "owner_user_id": user_id, "visibility": visibility}`。
- `_make_tools` 6 处 `filters={"user_id": user_id}` → `filters={"__access_user": user_id}`。
- `knowledge_status(user_id, scope="all")`：`{"__access_user": user_id}` / `{"visibility": "public"}` / `{"owner_user_id": user_id}`。
- `delete_document(user_id, doc_id, is_admin=False)`（防文档名探测：先按访问过滤器取可见条目，再判定）：
  1) `entries = store.list_docs(filters={"__access_user": user_id, "doc": doc_id})`（只见公共 + 本人私有）；
  2) `entries` 为空 → 0（路由返回 404；他人私有文档名不泄露存在性）；
  3) 存在公共条目且非 admin → 403（路由层语义）；
  4) 私有条目（必为本人）→ `delete_by_metadata({"owner_user_id": user_id, "doc": doc_id, "visibility": "private"})`；
  5) 公共条目且 admin → 追加 `delete_by_metadata({"doc": doc_id, "visibility": "public"})`。
- `publish_document(user_id, doc_id)` / `unpublish_document(user_id, doc_id)`（仅 admin 调用）：`set_payload_by_filter({"visibility": "public"|"private"}, {"owner_user_id": user_id, "doc": doc_id})`；影响点数 0 → 404。
- `knowledge_asset_owned(user_id, path)`：改为 `metadata_exists({"__access_user": user_id, "image_path": path})`（公共图所有人可读，私有图仅 owner）。

### 4.8 知识库 HTTP 端点（`careercrew_api/routers/knowledge.py`）

| 端点 | 变更 |
|---|---|
| `POST /upload` | 新增 `visibility: str = Form("private")`（pattern `private|public`）；非 admin 且 `public` → 403；`_run_ingest_job` 透传 visibility |
| `GET ""` 列表 | 新增 `scope: Literal["all","public","private"] = "all"` → `rt.knowledge_status(user_id, scope)`；docs 含 `visibility/owner_user_id` |
| `DELETE /{doc_id}` | 传 `is_admin=(current_user["role"]=="admin")`，语义见 §4.7；403 当非 admin 尝试删公共 |
| `POST /{doc_id}/publish` | `AdminUser`；`rt.publish_document(...)` |
| `POST /{doc_id}/unpublish` | `AdminUser`；`rt.unpublish_document(...)` |
| `POST /ask` | `KnowledgeAskRequest` 增 `scope: Literal["all","public","private"] = "all"`；`run_knowledge_ask_stream(..., scope=...)` 在 agent 的 rag_query filters 上体现（knowledge 分支 filters = scope 映射，其余 agent 保持 `__access_user`） |
| `GET /image` | 不变（底层 `knowledge_asset_owned` 已改） |

`ask` 的 scope 流：`run_knowledge_ask_stream` 增 `scope` 参数 → `_make_tools("knowledge", ...)` 传 `access_filters=scope_filters(user_id, scope)`；`rag_query_tool` 的 `filters` 由 `_make_tools` 提供，tool 内仍按现有逻辑叠加 `category`（`rag_query.py:37-39` 不变）。

### 4.9 知识库 payload 数据迁移（`scripts/migrate_knowledge_visibility.py` 新建）

- 只处理 `careercrew_mm`；默认 dry-run。
- 对每个点：`owner = payload.get("user_id") or payload.get("owner_user_id") or "u_001"`；若 `owner_user_id` 与 `visibility` 均已存在 → skip。
- apply：`set_payload({"owner_user_id": owner, "visibility": "private"})` + 删除旧 `user_id` 键（`set_payload` 传 `points=[PointIdsList]`；删除键用 `set_payload(payload={"user_id": None}, ...)`，qdrant 删除字段语义）。
- 幂等：复跑 changes=0。迁移前后断言物理 ID 集合不变（count 与抽样 UUID 一致）。
- 不触碰 `careercrew_episodic_v2`。
- 完成后再在 `migrate_legacy_tenant.py:329-339` 补 `owner_user_id` 兼容回读（`owner = payload.get("user_id") or payload.get("owner_user_id")`），保证工具链一致。

### 4.10 前端（`careercrew_web/`）

1. **`/admin/users` 页面**：
   - 新建 `src/pages/AdminUsersPage.tsx`：列表（username/role/status/创建时间）+ 操作（启用/禁用/改角色/重置密码/新建用户），全部带确认与结果提示；密码输入不落页面状态展示；表格用现有 ui 组件 + Tailwind。
   - `src/App.tsx`：`PAGES` 增 `/admin/users`；`NAV` 增「用户管理」项，仅 `auth.user.role === "admin"` 渲染；渲染前守卫：非 admin 访问 `/admin/users` → 回退渲染 `ChatPage`（或 403 占位页，取前者，简单且不泄露入口）。
   - `src/lib/auth.ts`：无破坏性改动；增 `apiFetch` 直接支持上述端点（沿用现有封装）。
2. **知识库可见性**：
   - `src/types.ts`：`KnowledgeDoc` 增 `visibility/owner_user_id`；`KB_SCOPE` 常量 `all/public/private`；上传请求增 visibility。
   - `src/components/KnowledgePanel.tsx`：列表按「我的/公共」徽标分组展示；上传表单 admin 可见「发布到公共库」开关；删除前确认提示；非 admin 不渲染公共库的删除/发布按钮。
   - `src/pages/KnowledgePage.tsx`：检索范围选择器扩展为 全部/公共库/个人库/分类，随 thread 持久化（沿用 retrieval_scope PATCH 机制，`threadStore` 的 `RetrievalScope` 增 scope 字段）。
   - `src/pages/DataPage.tsx:133/414/459`：`"u_001"` → `auth.user.id`。
3. **前端测试**：新增 `AdminUsersPage.test.tsx`（mock `@/lib/auth`）、`KnowledgePanel` 可见性用例、scope 选择器用例（jsdom）。

### 4.11 旧目录与脚本清理

- `scripts/fetch_kb.py`：新增 `--user-id`（默认 `u_001`）、`--visibility`（默认 `private`）；输出改到 `data/uploads/knowledge_raw/{user_id}/{uuid}.md`（`storage.resolve_under(storage.L.knowledge_raw, user_id, ...)`），并直接走 `MultimodalIngestionPipeline` 入库（metadata 含 `owner_user_id/visibility`），不再写 `data/knowledge`。
- `scripts/ingest_knowledge.py`：新增 `--user-id`、`--visibility`，`ingest_file(metadata={"owner_user_id": ..., "visibility": ...})`；glob 根目录不变（离线脚本，非运行时）。
- `data/knowledge/`：校验 `langchain_v1_tools.md` 与 `data/uploads/langchain_v1_tools.md` 内容一致且 `careercrew_mm` 中 `langchain_v1_tools_*` 向量已归属 u_001 后，移动到 `data/archived/knowledge_legacy_20260815/`（保留可回滚，不物理删除）。
- 运行时确认不扫描 `data/uploads` 根目录（已核实：runtime 只走 `storage.resolve_under` 与上传端点）；不做代码改动，仅在设计/文档中固定该约束。

## 5. 错误处理约定

- 401 文案维持现有（"invalid username or password" / "invalid access token" / "invalid refresh token"），不区分用户不存在/密码错误。
- 429 锁定：`detail="too many login attempts"` + `Retry-After`。
- 管理端点 404：目标用户不存在；409：用户名冲突或守卫失败（如最后一名 admin）文案明确；403：权限不足。
- Postgres 连接失败：`get_auth_service` 抛 `SettingsError`/RuntimeError → 启动 fail-fast；运行期异常由 FastAPI 默认 500 处理（不吞）。

## 6. 测试与验收矩阵

| 计划验收标准 | 测试落点 |
|---|---|
| SQLite→PG 后原管理员密码可登录、旧 refresh 失效 | `tests/unit/test_account_migration.py`（sqlite tmp → 真实/桩 PG）+ `tests/api/test_auth_api.py` 扩展 |
| 普通用户不能调用用户管理/全局设置/发布接口 | `tests/api/test_auth_api.py` + `test_knowledge_api.py`（403 用例） |
| 管理员不能读他人私有会话/简历/知识库/图片 | `tests/api/test_tenant_isolation_api.py` 扩展（双账号 + 公共/私有矩阵） |
| 两用户同名 thread/文件/doc 不冲突 | `tests/api/test_upload_isolation_api.py` + `test_knowledge_api.py` 扩展 |
| 禁用后 access+refresh 均立即失效 | `tests/api/test_auth_api.py`（token_version/status 用例） |
| 不能禁用/降级最后一名 admin | `tests/api/test_auth_api.py`（409 用例） |
| 所有用户可检索公共资料、私有仅本人 | `tests/unit/test_qdrant_store.py`（`__access_user` 过滤）+ API 矩阵 |
| 历史 Qdrant 迁移首跑成功、复跑零变更 | `tests/unit/test_knowledge_visibility_migration.py`（:memory: Qdrant） |
| 管理页面开户/禁用/启用/重置/改角色流程 | `careercrew_web/src/pages/AdminUsersPage.test.tsx` |
| 登录限速/锁定与清零 | `tests/unit/test_login_rate_limit.py` |
| Origin 校验 | `tests/api/test_auth_api.py`（带/不带 Origin 用例） |
| 过期会话清理 | `tests/unit/test_refresh_session_cleanup.py` |
| 全套回归 | `pytest tests/unit tests/api`（fake 后端）+ 前端 `vitest run` + `oxlint` + `tsc -b && vite build` |

## 7. 关键决策记录（ADR）

1. **SQLite 只留给测试/显式配置**：运行时默认 postgres，DSN 缺失即启动失败（不再静默回退）。
2. **`_to_qid` 编码不变**：物理 ID 只依赖 owner 值；改名仅动 payload 键，历史点不重排、不复制。
3. **情景记忆集合不改名**（继续 `user_id`）：记忆天然私有；`upsert` 双键兼容。
4. **公共库只能由管理员发布/下架/删除**；普通用户检索 = 自己私有 + 公共。
5. **不硬删除账号**（只禁用），避免产生无归属业务数据。
6. **access token 短命（15min）+ token_version**：禁用/改密/改角色即时失效，无需黑名单。
7. **限速落 Postgres**（多实例一致），不锁定账号实体。
8. **Origin 校验仅在带 Origin 头时执行**，兼容非浏览器客户端。
9. **旧 refresh 会话不迁移**，迁移后全员重新登录。
10. **`data/knowledge` 归档不删除**，可回滚。
