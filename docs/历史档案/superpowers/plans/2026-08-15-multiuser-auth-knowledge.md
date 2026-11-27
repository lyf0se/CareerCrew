# CareerCrew 多用户账号、权限与知识库完善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把账号/刷新会话迁到 Postgres（SQLite 仅测试）、补齐管理员用户管理 API 与 `/admin/users` 前端、知识库引入 `owner_user_id + visibility`（私有/公共）统一访问过滤、清理旧知识目录与脚本、完成登录限速/Origin 校验/审计/过期会话清理，并满足设计文档 `docs/superpowers/specs/2026-08-15-multiuser-auth-knowledge-design.md`（下称 DESIGN）的全部验收标准。

**Architecture:** 认证层引入 `AccountStore` 抽象（`careercrew_api/auth/store.py`：SQLite 仅测试 / Postgres 运行时），JWT 增加 `tv`（token_version）claim 使禁用/改密/改角色即时生效；知识库 payload 用 `owner_user_id + visibility`，`QdrantStore` 保留键 `__access_user` 提供「公共 OR 本人私有」过滤语义，物理 ID 编码 `_to_qid` 不变（不重排历史点）。前端新增 `/admin/users` 角色守卫页面与知识库可见性 UI。

**Tech Stack:** Python 3.12 / FastAPI / PyJWT / argon2-cffi / psycopg 3 / qdrant-client / pytest；React 19 / react-router-dom 7 / TailwindCSS v3 / vitest / oxlint。

## Global Constraints

- 在 `main` 分支直接实施，commit 不带 `Co-Authored-By` trailer；每任务独立 commit，消息前缀 `feat(auth):` / `feat(kb):` / `feat(web):` / `chore:`。
- 不改动 `_to_qid` 的 namespace 常量与编码公式（历史物理 ID 不可变）；`_QID_TENANT_NS`/`_QID_LEGACY_NS` 保持 DESIGN §4.7 不变式。
- 情景记忆集合 `careercrew_episodic_v2` 继续使用 `user_id` 键；`upsert` 必须双键兼容（`owner_user_id` 优先、`user_id` 兜底）。
- 所有按用户过滤的知识库调用点统一走 `{"__access_user": <uid>}`（公共 OR 本人私有）；显式范围用 `{"visibility": "public"}` / `{"owner_user_id": <uid>}`；二者不混用。
- 密码、refresh token、密码哈希永不进入任何 API 响应与审计 context。
- 不提供账号硬删除；管理员不能改自己（通过用户管理端点）；系统不能失去最后一名 active admin。
- 运行时默认 `auth.backend=postgres`（DSN 缺失启动失败）；SQLite 仅测试或显式配置（development/test 环境）。
- 旧 refresh 会话不迁移：迁移后全员重新登录。
- `data/knowledge` 只归档不删除。
- Windows 环境命令：pytest 用 `$env:PYTHONPATH=(Get-Location).Path; F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest ...`（仓库根目录执行）；前端在 `careercrew_web` 下用 `npm run`。
- 后端 CI 可跑范围：`tests/unit` 全量 + `tests/api`（fake 后端）；需真实 Postgres/Qdrant 的用例标记 `integration` 且缺 `POSTGRES_TEST_DSN`/Qdrant 时 `pytest.skip`。
- 前端测试文件用文件级 `// @vitest-environment jsdom`（顶层环境为 node）；TS 严格（`noUnusedLocals`、`verbatimModuleSyntax`、`erasableSyntaxOnly`）。

---

## Part A：认证迁移与管理员管理

### Task A1: AuthSettings 扩展 + 后端选择与生产校验

**Files:**
- Modify: `careercrew_core/state/settings.py`（`AuthSettings` 类 `:221-245`、`load_auth_settings` `:409-443`）
- Modify: `config/settings.yaml`（`auth:` 段 `:124-131`）
- Test: `tests/unit/test_config_loading.py`（追加用例）

**Interfaces:**
- Consumes: `SettingsError`（同文件）、`_resolve_path`、`_substitute_env`。
- Produces: `AuthSettings` 新字段——`backend: str = "sqlite"`、`database_url: str = ""`、`trusted_origins: list[str]`（默认 `["http://localhost:5175", "http://127.0.0.1:5175"]`）、`login_max_failures: int = 5`、`login_failure_window_minutes: int = 15`、`login_lock_minutes: int = 15`、`cleanup_interval_hours: int = 6`。`load_auth_settings()` 保证返回的 `database_url` 已解析（postgres 后端时非空）。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_config_loading.py` 末尾追加：

```python
def test_auth_backend_postgres_falls_back_to_database_url(tmp_path, monkeypatch):
    from careercrew_core.state import settings as settings_module
    from careercrew_core.state.settings import SettingsError

    config = tmp_path / "settings.yaml"
    config.write_text(
        "auth:\n  backend: postgres\n  database_url: ''\n", encoding="utf-8"
    )
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config)
    monkeypatch.setenv("DATABASE_URL", "postgresql://careercrew:careercrew@localhost:5432/careercrew")
    auth = settings_module.load_auth_settings()
    assert auth.backend == "postgres"
    assert auth.database_url == "postgresql://careercrew:careercrew@localhost:5432/careercrew"


def test_auth_backend_postgres_without_dsn_fails(tmp_path, monkeypatch):
    from careercrew_core.state import settings as settings_module
    from careercrew_core.state.settings import SettingsError

    config = tmp_path / "settings.yaml"
    config.write_text(
        "auth:\n  backend: postgres\n  database_url: ''\n", encoding="utf-8"
    )
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AUTH_DATABASE_URL", raising=False)
    with pytest.raises(SettingsError, match="AUTH_DATABASE_URL"):
        settings_module.load_auth_settings()


def test_auth_backend_sqlite_rejected_in_production(tmp_path, monkeypatch):
    from careercrew_core.state import settings as settings_module
    from careercrew_core.state.settings import SettingsError

    config = tmp_path / "settings.yaml"
    config.write_text(
        "auth:\n  environment: production\n  backend: sqlite\n  jwt_secret: 'x' * 40\n  cookie_secure: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config)
    with pytest.raises(SettingsError, match="auth.backend=sqlite"):
        settings_module.load_auth_settings()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$env:PYTHONPATH=(Get-Location).Path; F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_config_loading.py -k auth_backend -v`
Expected: FAIL（`SettingsError` 未抛出 / `database_url` 为空串）。

- [ ] **Step 3: 实现 AuthSettings 扩展与校验**

`settings.py` 中 `AuthSettings`（当前 `:221-245`）替换为：

```python
class AuthSettings(BaseModel):
    """本地账号认证配置。

    开发/测试允许进程内随机回退；测试可显式配置 jwt_secret 以获得确定性。
    任何其他环境都必须显式提供足够强度的 AUTH_JWT_SECRET。
    """

    environment: str = "development"
    backend: str = "sqlite"  # postgres | sqlite（sqlite 仅测试/显式开发配置）
    database_url: str = ""   # postgres 后端 DSN；空则回退 DATABASE_URL 环境变量
    trusted_origins: list[str] = ["http://localhost:5175", "http://127.0.0.1:5175"]
    login_max_failures: int = 5
    login_failure_window_minutes: int = 15
    login_lock_minutes: int = 15
    cleanup_interval_hours: int = 6
    jwt_secret: str = ""
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = False
    account_db_path: str = "./data/db/accounts.db"  # 仅 backend=sqlite 时生效
```

`load_auth_settings()`（当前 `:409-443`）在现有生产校验之后、`account_db_path` 解析之前插入：

```python
    if auth.backend not in ("postgres", "sqlite"):
        raise SettingsError("auth.backend 必须为 postgres 或 sqlite")
    if auth.backend == "postgres":
        dsn = (auth.database_url or "").strip()
        if not dsn:
            dsn = os.environ.get("DATABASE_URL", "").strip()
        if not dsn:
            raise SettingsError(
                "auth.backend=postgres 需要 AUTH_DATABASE_URL 或 DATABASE_URL"
            )
        auth.database_url = dsn
    elif not auth.is_development:
        raise SettingsError("auth.backend=sqlite 仅允许在开发/测试环境使用")
    if not auth.trusted_origins:
        raise SettingsError("auth.trusted_origins 不能为空")
```

`config/settings.yaml` 的 `auth:` 段（`:124-131`）替换为：

```yaml
# ── 本地账号认证 ──
# 账号/刷新会话存 Postgres（backend=postgres；AUTH_DATABASE_URL 未设置时回退 DATABASE_URL）。
# SQLite 仅保留给单元测试/显式开发配置；生产环境必须 postgres。
auth:
  environment: development   # 可用 CAREERCREW_ENV 覆盖为 production
  backend: postgres
  database_url: "${AUTH_DATABASE_URL}"
  trusted_origins: ["http://localhost:5175", "http://127.0.0.1:5175"]
  login_max_failures: 5
  login_failure_window_minutes: 15
  login_lock_minutes: 15
  cleanup_interval_hours: 6
  jwt_secret: "${AUTH_JWT_SECRET}"
  access_token_minutes: 15
  refresh_token_days: 7
  cookie_secure: false       # 生产环境必须为 true
  account_db_path: ./data/db/accounts.db
```

- [ ] **Step 4: 重跑测试确认通过**

Run: 同 Step 2。
Expected: PASS；另跑 `pytest tests/unit/test_config_loading.py`（不 `-k`）确认既有用例不受影响。

- [ ] **Step 5: Commit**

```bash
git add careercrew_core/state/settings.py config/settings.yaml tests/unit/test_config_loading.py
git commit -m "feat(auth): backend selection (postgres default), dsn fallback and production guards"
```

---

### Task A2: AccountStore 接口 + SQLite/Postgres 双实现

**Files:**
- Create: `careercrew_api/auth/store.py`
- Modify: `careercrew_api/auth/service.py`（导入改为新 store；`AccountStore` 定义删除，保留 `AuthService`/异常/工具函数）
- Test: `tests/unit/test_account_store.py`（新建）、`tests/integration/test_postgres_account_store.py`（新建，缺 `POSTGRES_TEST_DSN` 时 skip）

**Interfaces:**
- Consumes: `careercrew_core.state.settings.AuthSettings`（`database_url`/`account_db_path`）；`argon2` 已哈希密码字符串；`AccountExistsError`（迁移到 `store.py` 定义，`service.py` re-export 保持兼容）。
- Produces:
  - `AccountStore(ABC)` 抽象方法（DESIGN §4.1 签名全集）。
  - `SqliteAccountStore(path)`、`PostgresAccountStore(dsn)`。
  - `create_account_store(settings: AuthSettings) -> AccountStore`：按 `settings.backend` 路由（`postgres` → PostgresAccountStore(settings.database_url)；`sqlite` → SqliteAccountStore(settings.account_db_path)）。
  - 账号行 dict 键：`id, username, password_hash, role, status, token_version, created_at, updated_at`（`account_by_id` 公开面剔除 `password_hash`）。

- [ ] **Step 1: 写失败测试（SQLite 实现，always-run）**

新建 `tests/unit/test_account_store.py`：

```python
"""AccountStore SQLite 实现：状态/令牌版本/会话撤销/限速/审计。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from careercrew_api.auth.store import AccountExistsError, SqliteAccountStore, hash_token


PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$fake$fake"


@pytest.fixture
def store(tmp_path):
    return SqliteAccountStore(str(tmp_path / "accounts.db"))


def test_create_first_admin_and_token_version_fields(store):
    assert not store.has_accounts()
    admin = store.create_first_admin("admin", PASSWORD_HASH)
    assert admin["id"] == "u_001" and admin["role"] == "admin"
    row = store.account_by_username("admin")
    assert row["status"] == "active" and row["token_version"] == 0
    assert "password_hash" not in store.account_by_id("u_001")


def test_bump_token_version_and_update_account(store):
    store.create_first_admin("admin", PASSWORD_HASH)
    assert store.bump_token_version("u_001") == 1
    updated = store.update_account("u_001", status="disabled")
    assert updated["status"] == "disabled"
    assert store.account_by_id("u_001")["token_version"] == 1


def test_revoke_other_and_all_refresh_sessions(store):
    store.create_first_admin("admin", PASSWORD_HASH)
    now = datetime.now(UTC)
    store.create_refresh_session("t1", "u_001", now + timedelta(days=1))
    store.create_refresh_session("t2", "u_001", now + timedelta(days=1))
    revoked = store.revoke_other_refresh_sessions("u_001", "t1")
    assert revoked == 1
    assert store.rotate_refresh_session("t2", "t3", now + timedelta(days=1)) is None
    assert store.rotate_refresh_session("t1", "t4", now + timedelta(days=1)) is not None
    assert store.revoke_all_refresh_sessions("u_001") == 1


def test_duplicate_username_raises(store):
    store.create_first_admin("admin", PASSWORD_HASH)
    with pytest.raises(AccountExistsError):
        store.create_account("admin", PASSWORD_HASH, "user")
```

（`hash_token` 为 store 模块级 helper：`hashlib.sha256(token.encode("utf-8")).hexdigest()`，Step 3 的 store.py 代码提供。）

- [ ] **Step 2: 运行确认失败**

Run: `$env:PYTHONPATH=(Get-Location).Path; F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_account_store.py -v`
Expected: FAIL（`ImportError: cannot import name ... from careercrew_api.auth.store`）。

- [ ] **Step 3: 实现 `careercrew_api/auth/store.py`（完整文件）**

```python
"""账号/刷新会话/审计/限速的存储抽象：SQLite（测试）与 Postgres（运行时）。

账号行 dict 键：id, username, password_hash, role, status, token_version,
created_at, updated_at。公开面（account_by_id / list_accounts / rotate）
一律剔除 password_hash。时间戳统一 ISO8601 UTC 字符串。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from pathlib import Path
import hashlib
import json
import secrets
import sqlite3
from typing import Any
from uuid import uuid4

from careercrew_core.state.settings import AuthSettings

_STATUSES = ("active", "disabled")
_ROLES = ("admin", "user")


class AccountExistsError(Exception):
    """用户名或首个管理员已存在。"""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


class AccountStore(ABC):
    """账号与可撤销刷新会话存储契约（见 DESIGN §4.1）。"""

    @abstractmethod
    def has_accounts(self) -> bool: ...

    @abstractmethod
    def account_by_username(self, username: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def account_by_id(self, user_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def list_accounts(self, offset: int, limit: int) -> tuple[list[dict], int]: ...

    @abstractmethod
    def create_first_admin(self, username: str, password_hash: str) -> dict[str, Any]: ...

    @abstractmethod
    def create_account(self, username: str, password_hash: str, role: str) -> dict[str, Any]: ...

    @abstractmethod
    def update_account(self, user_id: str, *, role: str | None = None,
                       status: str | None = None) -> dict[str, Any]: ...

    @abstractmethod
    def update_password_hash(self, user_id: str, password_hash: str) -> None: ...

    @abstractmethod
    def bump_token_version(self, user_id: str) -> int: ...

    @abstractmethod
    def create_refresh_session(self, token: str, user_id: str, expires_at: datetime) -> None: ...

    @abstractmethod
    def rotate_refresh_session(self, old_token: str, new_token: str,
                               expires_at: datetime) -> dict[str, Any] | None: ...

    @abstractmethod
    def revoke_refresh_session(self, token: str) -> None: ...

    @abstractmethod
    def revoke_all_refresh_sessions(self, user_id: str) -> int: ...

    @abstractmethod
    def revoke_other_refresh_sessions(self, user_id: str, keep_token: str) -> int: ...

    @abstractmethod
    def delete_expired_refresh_sessions(self, revoked_older_than_days: int = 30) -> int: ...

    @abstractmethod
    def add_audit_event(self, actor_id: str, action: str, target_user_id: str | None,
                        context: dict) -> None: ...

    @abstractmethod
    def record_login_failure(self, key: str, *, max_failures: int,
                             window: timedelta, lock: timedelta) -> tuple[bool, str | None]: ...

    @abstractmethod
    def clear_login_failures(self, key: str) -> None: ...

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {k: row[k] for k in ("id", "username", "role", "status",
                                    "token_version", "created_at", "updated_at")
                if k in row and row.get(k) is not None}


class SqliteAccountStore(AccountStore):
    """SQLite 实现（仅测试/显式本地配置）。旧库自动补 status/token_version/updated_at。"""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS accounts ("
                "id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, "
                "role TEXT NOT NULL CHECK(role IN ('admin', 'user')), created_at TEXT NOT NULL)"
            )
            for column, ddl in (
                ("status", "ALTER TABLE accounts ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"),
                ("token_version", "ALTER TABLE accounts ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0"),
                ("updated_at", "ALTER TABLE accounts ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in self._columns(conn, "accounts"):
                    conn.execute(ddl)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS refresh_sessions ("
                "token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL, "
                "created_at TEXT NOT NULL, FOREIGN KEY(user_id) REFERENCES accounts(id) ON DELETE CASCADE)"
            )
            if "revoked_at" not in self._columns(conn, "refresh_sessions"):
                conn.execute("ALTER TABLE refresh_sessions ADD COLUMN revoked_at TEXT")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS admin_audit_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id TEXT NOT NULL, action TEXT NOT NULL, "
                "target_user_id TEXT, context TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS auth_login_attempts ("
                "key TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0, "
                "window_start TEXT, locked_until TEXT, updated_at TEXT NOT NULL)"
            )

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _account_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data.setdefault("status", "active")
        data.setdefault("token_version", 0)
        data.setdefault("updated_at", data.get("created_at", ""))
        return data

    def has_accounts(self) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM accounts LIMIT 1").fetchone() is not None

    def create_first_admin(self, username: str, password_hash: str) -> dict[str, Any]:
        now = _utcnow().isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM accounts LIMIT 1").fetchone() is not None:
                conn.execute("ROLLBACK")
                raise AccountExistsError("an account already exists")
            conn.execute(
                "INSERT INTO accounts (id, username, password_hash, role, status, token_version, created_at, updated_at) "
                "VALUES (?, ?, ?, 'admin', 'active', 0, ?, ?)",
                ("u_001", username, password_hash, now, now),
            )
            row = conn.execute("SELECT * FROM accounts WHERE id = 'u_001'").fetchone()
            conn.execute("COMMIT")
        return self._public(self._account_row(row))

    def create_account(self, username: str, password_hash: str, role: str) -> dict[str, Any]:
        user_id = f"u_{uuid4().hex}"
        now = _utcnow().isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO accounts (id, username, password_hash, role, status, token_version, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'active', 0, ?, ?)",
                    (user_id, username, password_hash, role, now, now),
                )
                row = conn.execute("SELECT * FROM accounts WHERE id = ?", (user_id,)).fetchone()
        except sqlite3.IntegrityError as err:
            raise AccountExistsError("username already exists") from err
        return self._public(self._account_row(row))

    def account_by_username(self, username: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE username = ?", (username,)).fetchone()
        return self._account_row(row) if row else None

    def account_by_id(self, user_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (user_id,)).fetchone()
        return self._public(self._account_row(row)) if row else None

    def list_accounts(self, offset: int, limit: int) -> tuple[list[dict], int]:
        with self._connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])
            rows = conn.execute(
                "SELECT * FROM accounts ORDER BY created_at, id LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [self._public(self._account_row(r)) for r in rows], total

    def update_account(self, user_id: str, *, role: str | None = None,
                       status: str | None = None) -> dict[str, Any]:
        if role is not None and role not in _ROLES:
            raise ValueError(f"invalid role: {role}")
        if status is not None and status not in _STATUSES:
            raise ValueError(f"invalid status: {status}")
        now = _utcnow().isoformat()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise KeyError(user_id)
            data = self._account_row(row)
            conn.execute(
                "UPDATE accounts SET role = ?, status = ?, updated_at = ? WHERE id = ?",
                (role if role is not None else data["role"],
                 status if status is not None else data["status"], now, user_id),
            )
            refreshed = conn.execute("SELECT * FROM accounts WHERE id = ?", (user_id,)).fetchone()
        return self._public(self._account_row(refreshed))

    def update_password_hash(self, user_id: str, password_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE accounts SET password_hash = ?, updated_at = ? WHERE id = ?",
                (password_hash, _utcnow().isoformat(), user_id),
            )

    def bump_token_version(self, user_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT token_version FROM accounts WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise KeyError(user_id)
            version = int(row["token_version"]) + 1
            conn.execute(
                "UPDATE accounts SET token_version = ?, updated_at = ? WHERE id = ?",
                (version, _utcnow().isoformat(), user_id),
            )
        return version

    def create_refresh_session(self, token: str, user_id: str, expires_at: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO refresh_sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (hash_token(token), user_id, expires_at.isoformat(), _utcnow().isoformat()),
            )

    def rotate_refresh_session(self, old_token: str, new_token: str,
                               expires_at: datetime) -> dict[str, Any] | None:
        old_hash = hash_token(old_token)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT s.user_id, s.expires_at, a.id, a.username, a.role, a.status, a.token_version, "
                "a.created_at, a.updated_at "
                "FROM refresh_sessions s JOIN accounts a ON a.id = s.user_id "
                "WHERE s.token_hash = ? AND s.revoked_at IS NULL",
                (old_hash,),
            ).fetchone()
            if row is None or datetime.fromisoformat(row["expires_at"]) <= _utcnow():
                conn.execute("DELETE FROM refresh_sessions WHERE token_hash = ?", (old_hash,))
                conn.execute("COMMIT")
                return None
            conn.execute("DELETE FROM refresh_sessions WHERE token_hash = ?", (old_hash,))
            conn.execute(
                "INSERT INTO refresh_sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (hash_token(new_token), row["user_id"], expires_at.isoformat(), _utcnow().isoformat()),
            )
            conn.execute("COMMIT")
        account = {
            "id": row["id"], "username": row["username"], "role": row["role"],
            "status": row["status"], "token_version": row["token_version"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
        return self._public(account)

    def revoke_refresh_session(self, token: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE refresh_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                (_utcnow().isoformat(), hash_token(token)),
            )

    def revoke_all_refresh_sessions(self, user_id: str) -> int:
        now = _utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE refresh_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (now, user_id),
            )
        return cur.rowcount

    def revoke_other_refresh_sessions(self, user_id: str, keep_token: str) -> int:
        now = _utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE refresh_sessions SET revoked_at = ? "
                "WHERE user_id = ? AND token_hash != ? AND revoked_at IS NULL",
                (now, user_id, hash_token(keep_token)),
            )
        return cur.rowcount

    def delete_expired_refresh_sessions(self, revoked_older_than_days: int = 30) -> int:
        now = _utcnow()
        cutoff = (now - timedelta(days=revoked_older_than_days)).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM refresh_sessions WHERE expires_at <= ? OR (revoked_at IS NOT NULL AND revoked_at <= ?)",
                (now.isoformat(), cutoff),
            )
        return cur.rowcount

    def add_audit_event(self, actor_id: str, action: str, target_user_id: str | None,
                        context: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO admin_audit_events (actor_id, action, target_user_id, context, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (actor_id, action, target_user_id, json.dumps(context, ensure_ascii=False),
                 _utcnow().isoformat()),
            )

    def record_login_failure(self, key: str, *, max_failures: int,
                             window: timedelta, lock: timedelta) -> tuple[bool, str | None]:
        now = _utcnow()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM auth_login_attempts WHERE key = ?", (key,)).fetchone()
            if row and row["locked_until"] and datetime.fromisoformat(row["locked_until"]) > now:
                locked_until = row["locked_until"]
                conn.execute("COMMIT")
                return True, locked_until
            if not row:
                conn.execute(
                    "INSERT INTO auth_login_attempts (key, failures, window_start, locked_until, updated_at) "
                    "VALUES (?, 1, ?, NULL, ?)",
                    (key, now.isoformat(), now.isoformat()),
                )
                conn.execute("COMMIT")
                return False, None
            window_start = row["window_start"] or now.isoformat()
            if datetime.fromisoformat(window_start) < now - window:
                failures, window_start = 1, now.isoformat()
            else:
                failures = int(row["failures"]) + 1
            locked_until: str | None = None
            if failures >= max_failures:
                locked = now + lock
                locked_until = locked.isoformat()
            conn.execute(
                "UPDATE auth_login_attempts SET failures = ?, window_start = ?, locked_until = ?, updated_at = ? "
                "WHERE key = ?",
                (failures, window_start, locked_until, now.isoformat(), key),
            )
            conn.execute("COMMIT")
            return failures >= max_failures, locked_until


    def clear_login_failures(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM auth_login_attempts WHERE key = ?", (key,))


class PostgresAccountStore(AccountStore):
    """Postgres 实现（运行时默认）。所有写操作走事务。"""

    def __init__(self, dsn: str) -> None:
        import psycopg
        import psycopg.rows

        self._dsn = dsn
        with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS auth_accounts ("
                "id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, "
                "role TEXT NOT NULL CHECK (role IN ('admin','user')), "
                "status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')), "
                "token_version INTEGER NOT NULL DEFAULT 0, "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS auth_refresh_sessions ("
                "token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL "
                "REFERENCES auth_accounts(id) ON DELETE CASCADE, "
                "expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "revoked_at TIMESTAMPTZ)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS admin_audit_events ("
                "id BIGSERIAL PRIMARY KEY, actor_id TEXT NOT NULL, action TEXT NOT NULL, "
                "target_user_id TEXT, context JSONB NOT NULL DEFAULT '{}'::jsonb, "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS auth_login_attempts ("
                "key TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0, "
                "window_start TIMESTAMPTZ, locked_until TIMESTAMPTZ, "
                "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )

    def _connect(self):
        import psycopg
        import psycopg.rows

        return psycopg.connect(self._dsn, row_factory=psycopg.rows.dict_row)

    def _as_text(self, conn, row: dict | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = {k: (_iso(v) if isinstance(v, datetime) else v) for k, v in row.items()}
        return out

    def has_accounts(self) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM auth_accounts LIMIT 1").fetchone() is not None

    def create_first_admin(self, username: str, password_hash: str) -> dict[str, Any]:
        with self._connect() as conn, conn.transaction():
            if conn.execute("SELECT 1 FROM auth_accounts LIMIT 1").fetchone() is not None:
                raise AccountExistsError("an account already exists")
            row = conn.execute(
                "INSERT INTO auth_accounts (id, username, password_hash, role) "
                "VALUES ('u_001', %s, %s, 'admin') RETURNING *",
                (username, password_hash),
            ).fetchone()
        return self._public(self._as_text(conn, row))

    def create_account(self, username: str, password_hash: str, role: str) -> dict[str, Any]:
        user_id = f"u_{uuid4().hex}"
        try:
            with self._connect() as conn, conn.transaction():
                row = conn.execute(
                    "INSERT INTO auth_accounts (id, username, password_hash, role) "
                    "VALUES (%s, %s, %s, %s) RETURNING *",
                    (user_id, username, password_hash, role),
                ).fetchone()
        except Exception as err:  # psycopg.errors.UniqueViolation（避免强绑定 psycopg.errors 模块路径）
            if type(err).__name__ == "UniqueViolation":
                raise AccountExistsError("username already exists") from err
            raise
        return self._public(self._as_text(conn, row))

    def account_by_username(self, username: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM auth_accounts WHERE username = %s", (username,)
            ).fetchone()
        return self._as_text(conn, row)

    def account_by_id(self, user_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM auth_accounts WHERE id = %s", (user_id,)
            ).fetchone()
        return self._public(self._as_text(conn, row)) if row else None

    def list_accounts(self, offset: int, limit: int) -> tuple[list[dict], int]:
        with self._connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM auth_accounts").fetchone()["count"])
            rows = conn.execute(
                "SELECT * FROM auth_accounts ORDER BY created_at, id LIMIT %s OFFSET %s",
                (limit, offset),
            ).fetchall()
        return [self._public(self._as_text(conn, r)) for r in rows], total

    def update_account(self, user_id: str, *, role: str | None = None,
                       status: str | None = None) -> dict[str, Any]:
        if role is not None and role not in _ROLES:
            raise ValueError(f"invalid role: {role}")
        if status is not None and status not in _STATUSES:
            raise ValueError(f"invalid status: {status}")
        with self._connect() as conn, conn.transaction():
            existing = conn.execute("SELECT 1 FROM auth_accounts WHERE id = %s", (user_id,)).fetchone()
            if existing is None:
                raise KeyError(user_id)
            conn.execute(
                "UPDATE auth_accounts SET role = COALESCE(%s, role), status = COALESCE(%s, status), "
                "updated_at = now() WHERE id = %s",
                (role, status, user_id),
            )
            row = conn.execute("SELECT * FROM auth_accounts WHERE id = %s", (user_id,)).fetchone()
        return self._public(self._as_text(conn, row))

    def update_password_hash(self, user_id: str, password_hash: str) -> None:
        with self._connect() as conn, conn.transaction():
            conn.execute(
                "UPDATE auth_accounts SET password_hash = %s, updated_at = now() WHERE id = %s",
                (password_hash, user_id),
            )

    def bump_token_version(self, user_id: str) -> int:
        with self._connect() as conn, conn.transaction():
            row = conn.execute(
                "UPDATE auth_accounts SET token_version = token_version + 1, updated_at = now() "
                "WHERE id = %s RETURNING token_version",
                (user_id,),
            ).fetchone()
            if row is None:
                raise KeyError(user_id)
        return int(row["token_version"])

    def create_refresh_session(self, token: str, user_id: str, expires_at: datetime) -> None:
        with self._connect() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO auth_refresh_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                (hash_token(token), user_id, expires_at),
            )

    def rotate_refresh_session(self, old_token: str, new_token: str,
                               expires_at: datetime) -> dict[str, Any] | None:
        old_hash = hash_token(old_token)
        with self._connect() as conn, conn.transaction():
            row = conn.execute(
                "SELECT s.expires_at, a.id, a.username, a.role, a.status, a.token_version, "
                "a.created_at, a.updated_at "
                "FROM auth_refresh_sessions s JOIN auth_accounts a ON a.id = s.user_id "
                "WHERE s.token_hash = %s AND s.revoked_at IS NULL",
                (old_hash,),
            ).fetchone()
            if row is None or row["expires_at"] <= _utcnow():
                conn.execute("DELETE FROM auth_refresh_sessions WHERE token_hash = %s", (old_hash,))
                return None
            conn.execute("DELETE FROM auth_refresh_sessions WHERE token_hash = %s", (old_hash,))
            conn.execute(
                "INSERT INTO auth_refresh_sessions (token_hash, user_id, expires_at) "
                "VALUES (%s, %s, %s)",
                (hash_token(new_token), row["id"], expires_at),
            )
        return self._public(self._as_text(conn, row))

    def revoke_refresh_session(self, token: str) -> None:
        with self._connect() as conn, conn.transaction():
            conn.execute(
                "UPDATE auth_refresh_sessions SET revoked_at = now() "
                "WHERE token_hash = %s AND revoked_at IS NULL",
                (hash_token(token),),
            )

    def revoke_all_refresh_sessions(self, user_id: str) -> int:
        with self._connect() as conn, conn.transaction():
            cur = conn.execute(
                "UPDATE auth_refresh_sessions SET revoked_at = now() "
                "WHERE user_id = %s AND revoked_at IS NULL",
                (user_id,),
            )
        return cur.rowcount

    def revoke_other_refresh_sessions(self, user_id: str, keep_token: str) -> int:
        with self._connect() as conn, conn.transaction():
            cur = conn.execute(
                "UPDATE auth_refresh_sessions SET revoked_at = now() "
                "WHERE user_id = %s AND token_hash != %s AND revoked_at IS NULL",
                (user_id, hash_token(keep_token)),
            )
        return cur.rowcount

    def delete_expired_refresh_sessions(self, revoked_older_than_days: int = 30) -> int:
        with self._connect() as conn, conn.transaction():
            cur = conn.execute(
                "DELETE FROM auth_refresh_sessions WHERE expires_at <= now() "
                "OR (revoked_at IS NOT NULL AND revoked_at <= now() - make_interval(days => %s))",
                (revoked_older_than_days,),
            )
        return cur.rowcount

    def add_audit_event(self, actor_id: str, action: str, target_user_id: str | None,
                        context: dict) -> None:
        with self._connect() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO admin_audit_events (actor_id, action, target_user_id, context) "
                "VALUES (%s, %s, %s, %s::jsonb)",
                (actor_id, action, target_user_id, json.dumps(context, ensure_ascii=False)),
            )

    def record_login_failure(self, key: str, *, max_failures: int,
                             window: timedelta, lock: timedelta) -> tuple[bool, str | None]:
        import psycopg.rows

        now = _utcnow()
        with self._connect() as conn, conn.transaction():
            row = conn.execute(
                "SELECT * FROM auth_login_attempts WHERE key = %s FOR UPDATE", (key,)
            ).fetchone()
            if row and row["locked_until"] and row["locked_until"] > now:
                return True, _iso(row["locked_until"])
            if row is None:
                conn.execute(
                    "INSERT INTO auth_login_attempts (key, failures, window_start) "
                    "VALUES (%s, 1, %s)",
                    (key, now),
                )
                return False, None
            window_start = row["window_start"] or now
            if window_start < now - window:
                failures, window_start = 1, now
            else:
                failures = int(row["failures"]) + 1
            locked_until: datetime | None = now + lock if failures >= max_failures else None
            conn.execute(
                "UPDATE auth_login_attempts SET failures = %s, window_start = %s, "
                "locked_until = %s, updated_at = now() WHERE key = %s",
                (failures, window_start, locked_until, key),
            )
            return failures >= max_failures, _iso(locked_until) if locked_until else None

    def clear_login_failures(self, key: str) -> None:
        with self._connect() as conn, conn.transaction():
            conn.execute("DELETE FROM auth_login_attempts WHERE key = %s", (key,))


def create_account_store(settings: AuthSettings) -> AccountStore:
    if settings.backend == "postgres":
        return PostgresAccountStore(settings.database_url)
    return SqliteAccountStore(settings.account_db_path)
```

- [ ] **Step 4: 改 `service.py` 导入与实现并跑测试**

`service.py` 顶部：删除原 `AccountStore` 类定义（`class AccountStore:` `:27-133`）与 `_token_hash`（`:229-230`）；导入改为：

```python
from careercrew_api.auth.store import (
    AccountExistsError,
    AccountStore,
    SqliteAccountStore,
    create_account_store,
    hash_token,
    new_refresh_token,
)
```

保持 `service.py` 里 `AuthenticationError`、`AuthService`、`_utcnow` 原样（`AuthService` 仍构造 `self.password_hasher = PasswordHasher()`，A3 再改参数；`_new_refresh_token` 改为 `new_refresh_token` 别名 `_new_refresh_token = new_refresh_token`，`_token_hash` 改为 `_token_hash = hash_token`，避免其余代码断裂）。`AccountStore.__init__(database_path)` 的调用点（`dependencies.py:19`）同步改为 `create_account_store(settings)`。

Run: `pytest tests/unit/test_account_store.py tests/unit/test_smoke_imports.py -v`
Expected: PASS（新用例全绿，导入冒烟不破）。

- [ ] **Step 5: 补 Postgres 集成测试（缺 DSN 跳过）**

新建 `tests/integration/test_postgres_account_store.py`：

```python
"""PostgresAccountStore 集成测试：需 POSTGRES_TEST_DSN（否则 skip）。"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from careercrew_api.auth.store import AccountExistsError, PostgresAccountStore

pytestmark = pytest.mark.integration

DSN = os.environ.get("POSTGRES_TEST_DSN", "").strip()

pytestmark_skip = pytest.mark.skipif(
    not DSN, reason="POSTGRES_TEST_DSN not set"
)


@pytest.fixture
def store():
    store = PostgresAccountStore(DSN)
    with store._connect() as conn, conn.transaction():
        conn.execute("DELETE FROM auth_refresh_sessions")
        conn.execute("DELETE FROM auth_login_attempts")
        conn.execute("DELETE FROM admin_audit_events")
        conn.execute("DELETE FROM auth_accounts")
    return store


@pytest.mark.skipif(not DSN, reason="POSTGRES_TEST_DSN not set")
def test_postgres_roundtrip_and_guards(store):
    admin = store.create_first_admin("admin", "$argon2$fake")
    assert admin["id"] == "u_001"
    assert store.account_by_username("admin")["token_version"] == 0
    assert store.bump_token_version("u_001") == 1
    member = store.create_account("member", "$argon2$fake2", "user")
    with pytest.raises(AccountExistsError):
        store.create_account("member", "$argon2$fake3", "user")
    now = datetime.now(UTC)
    store.create_refresh_session("r1", member["id"], now + timedelta(days=1))
    assert store.revoke_all_refresh_sessions(member["id"]) == 1
    assert store.rotate_refresh_session("r1", "r2", now + timedelta(days=1)) is None
    store.add_audit_event("u_001", "user.create", member["id"], {"role": "user"})
    items, total = store.list_accounts(0, 10)
    assert total == 2 and items[0]["id"] == "u_001"
    assert "password_hash" not in items[0]
```

Run: `$env:POSTGRES_TEST_DSN="postgresql://careercrew:careercrew@localhost:5432/careercrew"; $env:PYTHONPATH=(Get-Location).Path; F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/integration/test_postgres_account_store.py -v`
Expected: PASS（本机 Postgres 可用）。

- [ ] **Step 6: Commit**

```bash
git add careercrew_api/auth/store.py careercrew_api/auth/service.py tests/unit/test_account_store.py tests/integration/test_postgres_account_store.py
git commit -m "feat(auth): account store abstraction with SQLite (tests) and Postgres (runtime) backends"
```

---

### Task A3: AuthService 扩展（tv claim / status / 密码 / 用户管理 / 审计）

**Files:**
- Modify: `careercrew_api/auth/service.py`（`PasswordHasher` 参数、`login`、`current_user`、新增管理方法）
- Modify: `careercrew_api/auth/dependencies.py`（`get_auth_service` 用 `create_account_store`）
- Test: `tests/unit/test_auth_service_guards.py`（新建）

**Interfaces:**
- Consumes: `AccountStore`（A2 契约）、`AuthSettings` 新字段。
- Produces（`AuthService`）：
  - `login(username, password, client_ip="") -> tuple[dict, str]`，锁定抛 `LoginLockedError(retry_after_seconds)`（service.py 新异常）。
  - `current_user(access_token)` 校验 `status=="active"` 且 `claims["tv"] == token_version`。
  - `change_own_password(user, old_password, new_password, current_refresh_token=None)`。
  - `admin_reset_password(actor, user_id, new_password)`。
  - `update_user(actor, user_id, role=None, status=None) -> dict`（守卫：不能改自己；不能失去最后 active admin）。
  - `list_users(page, page_size) -> tuple[list[dict], int]`。
  - `_audit(actor_id, action, target_user_id, context)` 私有 helper。

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_auth_service_guards.py`：

```python
"""AuthService 守卫与令牌版本语义（SQLite store，纯单元）。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from careercrew_api.auth.service import (
    AuthService,
    AuthenticationError,
    LastAdminError,
    LoginLockedError,
    SelfAdminError,
)
from careercrew_api.auth.store import SqliteAccountStore
from careercrew_core.state.settings import AuthSettings

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def service(tmp_path):
    settings = AuthSettings(
        environment="test",
        jwt_secret="test-secret-" + "x" * 40,
        account_db_path=str(tmp_path / "accounts.db"),
        backend="sqlite",
    )
    store = SqliteAccountStore(settings.account_db_path)
    svc = AuthService(settings, store)
    svc.bootstrap_admin("admin", PASSWORD)
    return svc


def _login(svc: AuthService, username: str, password: str = PASSWORD):
    payload, refresh = svc.login(username, password)
    return payload["access_token"], refresh


def test_disable_user_kills_access_and_refresh_immediately(service):
    service.create_user("member", PASSWORD, "user")
    access, _ = _login(service, "member")
    assert service.current_user(access)["username"] == "member"
    service.update_user(service.current_user(_login(service, "admin")[0]), "member", status="disabled")
    with pytest.raises(AuthenticationError):
        service.current_user(access)


def test_admin_cannot_disable_or_demote_self(service):
    admin = service.current_user(_login(service, "admin")[0])
    with pytest.raises(SelfAdminError):
        service.update_user(admin, "u_001", status="disabled")
    with pytest.raises(SelfAdminError):
        service.update_user(admin, "u_001", role="user")


def test_cannot_lose_last_active_admin(service):
    admin = service.current_user(_login(service, "admin")[0])
    service.create_user("second-admin", PASSWORD, "admin")
    member = service.current_user(_login(service, "second-admin")[0])
    # 第二个 admin 存在时，禁用自己之外的第一 admin 仍被允许（还有 1 个 active admin）
    service.update_user(member, "u_001", status="disabled")
    with pytest.raises(LastAdminError):
        service.update_user(member, "second-admin", status="disabled")


def test_change_own_password_revokes_other_sessions(service):
    access, refresh = _login(service, "admin")
    other_access, other_refresh = _login(service, "admin")
    service.change_own_password(
        service.current_user(access), PASSWORD, "new-password-123456", current_refresh_token=refresh
    )
    with pytest.raises(AuthenticationError):
        service.current_user(access)  # 旧 access 因 tv bump 失效
    with pytest.raises(AuthenticationError):
        service.refresh(other_refresh)  # 其他刷新会话被撤销
    new_access, _ = _login(service, "admin", "new-password-123456")
    assert service.current_user(new_access)["username"] == "admin"


def test_login_lock_after_repeated_failures(service):
    for _ in range(service.settings.login_max_failures):
        with pytest.raises(AuthenticationError):
            service.login("admin", "wrong-password-123")
    with pytest.raises(LoginLockedError):
        service.login("admin", PASSWORD)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/unit/test_auth_service_guards.py -v`
Expected: FAIL（`ImportError`：`LastAdminError` 等不存在）。

- [ ] **Step 3: 实现 service.py 变更**

`service.py` 顶部新增异常：

```python
class LoginLockedError(Exception):
    """登录失败过多，账号按来源/IP 被短期锁定。"""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"too many login attempts; retry after {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


class SelfAdminError(Exception):
    """管理员不能通过用户管理端点修改自己。"""


class LastAdminError(Exception):
    """操作会使系统失去最后一名有效管理员。"""
```

`AuthService.__init__` 替换 `self.password_hasher = PasswordHasher()` 为：

```python
        self.password_hasher = PasswordHasher(
            time_cost=3, memory_cost=65536, parallelism=4, hash_len=32,
        )
```

`login`（`:152-165`）替换为（`_audit` 不用于登录；限速按 `username` 与 `ip` 双键）：

```python
    def login(self, username: str, password: str, client_ip: str = "") -> tuple[dict[str, Any], str]:
        locked_keys = [f"login:u:{username.lower()}"]
        if client_ip:
            locked_keys.append(f"login:ip:{client_ip}")
        for key in locked_keys:
            locked, locked_until = self.store.record_login_failure(
                key, max_failures=self.settings.login_max_failures,
                window=timedelta(minutes=self.settings.login_failure_window_minutes),
                lock=timedelta(minutes=self.settings.login_lock_minutes),
            )
            if locked:
                raise LoginLockedError(self._retry_after(locked_until))
        account = self.store.account_by_username(username)
        if not account:
            raise AuthenticationError("invalid username or password")
        if account.get("status") != "active":
            raise AuthenticationError("invalid username or password")
        try:
            valid = self.password_hasher.verify(account["password_hash"], password)
        except (VerificationError, InvalidHashError):
            valid = False
        if not valid:
            raise AuthenticationError("invalid username or password")
        for key in locked_keys:
            self.store.clear_login_failures(key)
        if self.password_hasher.check_needs_rehash(account["password_hash"]):
            self.store.update_password_hash(account["id"], self.password_hasher.hash(password))
        user = {key: account[key] for key in ("id", "username", "role")}
        return self._token_response(user)

    def _retry_after(self, locked_until: str | None) -> int:
        if not locked_until:
            return self.settings.login_lock_minutes * 60
        try:
            delta = (datetime.fromisoformat(locked_until) - _utcnow()).total_seconds()
        except ValueError:
            delta = 0
        return max(1, int(delta))
```

`current_user`（`:180-195`）校验部分替换为：

```python
        user = self.store.account_by_id(str(claims["sub"]))
        if (
            not user
            or user["role"] != claims.get("role")
            or user.get("status") != "active"
            or int(user.get("token_version") or 0) != int(claims.get("tv") or 0)
        ):
            raise AuthenticationError("invalid access token")
        return user
```

`_token_response`（`:197-215`）编码 claims 处加 `tv`（`user` 参数是公开 dict 不含 tv，编码前按 id 查库取当前 token_version）：

```python
    def _token_response(
        self, user: dict[str, str], refresh_token: str | None = None, session_exists: bool = False
    ) -> tuple[dict[str, Any], str]:
        now = _utcnow()
        expires = now + timedelta(minutes=self.settings.access_token_minutes)
        account = self.store.account_by_id(user["id"]) or {}
        access_token = jwt.encode(
            {
                "sub": user["id"], "role": user["role"], "type": "access",
                "tv": int(account.get("token_version") or 0), "iat": now, "exp": expires,
            },
            self.settings.signing_secret(),
            algorithm="HS256",
        )
        token = refresh_token or _new_refresh_token()
        if not session_exists:
            self.store.create_refresh_session(token, user["id"], self._refresh_expiry())
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": int((expires - now).total_seconds()),
            "user": user,
        }, token
```

新增方法（`logout` 之后插入）：

```python
    def change_own_password(
        self, user: dict[str, str], old_password: str, new_password: str,
        current_refresh_token: str | None = None,
    ) -> None:
        account = self.store.account_by_username(user["username"])
        if not account:
            raise AuthenticationError("account not found")
        try:
            valid = self.password_hasher.verify(account["password_hash"], old_password)
        except (VerificationError, InvalidHashError):
            valid = False
        if not valid:
            raise AuthenticationError("invalid password")
        self.store.update_password_hash(user["id"], self.password_hasher.hash(new_password))
        self.store.bump_token_version(user["id"])
        if current_refresh_token:
            self.store.revoke_other_refresh_sessions(user["id"], current_refresh_token)
        else:
            self.store.revoke_all_refresh_sessions(user["id"])

    def admin_reset_password(self, actor: dict[str, str], user_id: str, new_password: str) -> None:
        if actor["id"] == user_id:
            raise SelfAdminError("administrators must use /api/auth/password for themselves")
        self.store.update_password_hash(user_id, self.password_hasher.hash(new_password))
        self.store.bump_token_version(user_id)
        self.store.revoke_all_refresh_sessions(user_id)
        self._audit(actor["id"], "user.reset_password", user_id, {})

    def update_user(
        self, actor: dict[str, str], user_id: str, *,
        role: str | None = None, status: str | None = None,
    ) -> dict[str, Any]:
        if actor["id"] == user_id:
            raise SelfAdminError("administrators cannot modify their own account here")
        target = self.store.account_by_id(user_id)
        if not target:
            raise KeyError(user_id)
        if role is None and status is None:
            raise ValueError("nothing to update")
        new_role = role if role is not None else target["role"]
        new_status = status if status is not None else target["status"]
        if target["role"] == "admin" and (new_role != "admin" or new_status != "active"):
            self._ensure_remaining_active_admin(excluding=user_id)
        updated = self.store.update_account(user_id, role=new_role, status=new_status)
        self.store.bump_token_version(user_id)
        if new_status == "disabled":
            self.store.revoke_all_refresh_sessions(user_id)
        self._audit(
            actor["id"], "user.update", user_id,
            {"fields": sorted({k for k in ("role", "status") if locals().get(k) is not None})},
        )
        return updated

    def _ensure_remaining_active_admin(self, excluding: str) -> None:
        items, _total = self.store.list_accounts(0, 1000)
        active_admins = sum(
            1 for a in items
            if a["id"] != excluding and a["role"] == "admin" and a["status"] == "active"
        )
        if active_admins == 0:
            raise LastAdminError("operation would remove the last active administrator")

    def list_users(self, page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
        offset = max(page - 1, 0) * page_size
        return self.store.list_accounts(offset, page_size)

    def _audit(self, actor_id: str, action: str, target_user_id: str | None, context: dict) -> None:
        self.store.add_audit_event(actor_id, action, target_user_id, context)
```

`refresh`（`:167-174`）：`rotate_refresh_session` 已由 store 层做 status 校验（A2 实现中 JOIN 后未过滤 status——**修正**：A2 的 rotate SQL 需加 `AND a.status = 'active'`；SQLite/Postgres 两处都要。在本任务里同步修 `store.py` 两处 SQL）。

`dependencies.py`：

```python
@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    settings = load_auth_settings()
    return AuthService(settings, create_account_store(settings))
```

（`from careercrew_api.auth.store import create_account_store`；移除 `AccountStore` 直接构造。）

- [ ] **Step 4: 修正 store.py rotate 的 status 过滤**

`SqliteAccountStore.rotate_refresh_session` 的 SQL 中 `WHERE s.token_hash = ? AND s.revoked_at IS NULL` 改为 `WHERE s.token_hash = ? AND s.revoked_at IS NULL AND a.status = 'active'`；`PostgresAccountStore` 同理 `WHERE s.token_hash = %s AND s.revoked_at IS NULL AND a.status = 'active'`。

- [ ] **Step 5: 运行测试**

Run: `pytest tests/unit/test_auth_service_guards.py tests/unit/test_account_store.py -v`
Expected: PASS。再跑 `pytest tests/api/test_auth_api.py -v` 确认既有 API 行为不回退（fixture 用旧 `AccountStore` 构造——见 A4 一并更新；本步只确认 service 层，若 test_auth_api.py 因导入报错，先跑 `pytest tests/api/test_auth_api.py -k "not " -v` 无效，改为运行 `python -m py_compile careercrew_api/auth/service.py careercrew_api/auth/store.py`）。

- [ ] **Step 6: Commit**

```bash
git add careercrew_api/auth/service.py careercrew_api/auth/store.py careercrew_api/auth/dependencies.py tests/unit/test_auth_service_guards.py
git commit -m "feat(auth): token_version semantics, password change/reset, admin guards and audit"
```

---

### Task A4: 管理员用户管理端点 + 登录 429 + schemas

**Files:**
- Modify: `careercrew_api/routers/auth.py`
- Modify: `careercrew_api/schemas.py`
- Modify: `careercrew_api/auth/service.py`（`create_user` 增加 actor 参数并写审计）
- Test: `tests/api/test_auth_api.py`（fixture 更新 + 新用例）

**Interfaces:**
- Consumes: A3 的 `AuthService` 方法（`list_users/update_user/admin_reset_password/change_own_password/create_user(actor,...)`）、`LoginLockedError`、`SelfAdminError`、`LastAdminError`。
- Produces:
  - `GET /api/auth/users?page=&page_size=` → `{"items": [...], "total": int, "page": int, "page_size": int}`（admin）
  - `PATCH /api/auth/users/{user_id}` body `{"role"?, "status"?}` → AccountListItem
  - `POST /api/auth/users/{user_id}/reset-password` body `{"password"}` → `{"ok": true}`
  - `POST /api/auth/password` body `{"old_password","new_password"}` → `{"ok": true}`
  - `POST /api/auth/token` 失败锁定 → 429 + `Retry-After`
  - 状态码约定：SelfAdmin → 403；LastAdmin → 409；用户不存在 → 404；pydantic 校验失败 → 422。

- [ ] **Step 1: 更新 fixture 并写失败测试**

`tests/api/test_auth_api.py` 的 `auth_client` fixture（`:10-28`）替换为：

```python
@pytest.fixture
def auth_client(tmp_path):
    from fastapi.testclient import TestClient

    from careercrew_api.auth.dependencies import get_auth_service
    from careercrew_api.auth.service import AuthService
    from careercrew_api.auth.store import create_account_store
    from careercrew_core.state.settings import AuthSettings
    from careercrew_api.main import create_app

    settings = AuthSettings(
        environment="test",
        backend="sqlite",
        jwt_secret="test-signing-secret-that-is-long-enough-for-repeatable-api-tests",
        account_db_path=str(tmp_path / "accounts.db"),
    )
    service = AuthService(settings, create_account_store(settings))
    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: service
    with TestClient(app) as client:
        yield client
```

文件末尾追加：

```python
@pytest.mark.web
def test_admin_lists_patches_and_disables_users(auth_client):
    _bootstrap(auth_client)
    admin_headers = {"Authorization": f"Bearer {auth_client.post('/api/auth/token', json={'username': 'admin', 'password': PASSWORD}).json()['access_token']}"}

    created = auth_client.post(
        "/api/auth/users", json={"username": "member", "password": PASSWORD}, headers=admin_headers
    )
    assert created.status_code == 201
    member_id = created.json()["id"]

    listed = auth_client.get("/api/auth/users", headers=admin_headers)
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 2
    assert {u["username"] for u in body["items"]} == {"admin", "member"}

    member_token = auth_client.post("/api/auth/token", json={"username": "member", "password": PASSWORD}).json()["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}

    patched = auth_client.patch(
        f"/api/auth/users/{member_id}", json={"status": "disabled"}, headers=admin_headers
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "disabled"
    # 禁用立即生效：旧 access token 失效、登录被拒
    assert auth_client.get("/api/auth/me", headers=member_headers).status_code == 401
    assert auth_client.post("/api/auth/token", json={"username": "member", "password": PASSWORD}).status_code == 401

    reenabled = auth_client.patch(
        f"/api/auth/users/{member_id}", json={"status": "active"}, headers=admin_headers
    )
    assert reenabled.status_code == 200 and reenabled.json()["status"] == "active"


@pytest.mark.web
def test_admin_self_and_last_admin_guards(auth_client):
    _bootstrap(auth_client)
    admin_token = auth_client.post("/api/auth/token", json={"username": "admin", "password": PASSWORD}).json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    assert auth_client.patch("/api/auth/users/u_001", json={"status": "disabled"}, headers=admin_headers).status_code == 403
    assert auth_client.patch("/api/auth/users/u_001", json={"role": "user"}, headers=admin_headers).status_code == 409
    assert auth_client.patch("/api/auth/users/not-exist", json={"status": "disabled"}, headers=admin_headers).status_code == 404


@pytest.mark.web
def test_reset_password_and_change_own_password(auth_client):
    _bootstrap(auth_client)
    admin_headers = {"Authorization": f"Bearer {auth_client.post('/api/auth/token', json={'username': 'admin', 'password': PASSWORD}).json()['access_token']}"}
    member_id = auth_client.post("/api/auth/users", json={"username": "member", "password": PASSWORD}, headers=admin_headers).json()["id"]

    reset = auth_client.post(
        f"/api/auth/users/{member_id}/reset-password",
        json={"password": "another-password-456"}, headers=admin_headers,
    )
    assert reset.status_code == 200 and reset.json() == {"ok": True}
    assert auth_client.post("/api/auth/token", json={"username": "member", "password": PASSWORD}).status_code == 401
    member_token = auth_client.post("/api/auth/token", json={"username": "member", "password": "another-password-456"}).json()["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}

    change = auth_client.post(
        "/api/auth/password",
        json={"old_password": "another-password-456", "new_password": "third-password-789"},
        headers=member_headers,
    )
    assert change.status_code == 200 and change.json() == {"ok": True}
    assert auth_client.post("/api/auth/token", json={"username": "member", "password": "third-password-789"}).status_code == 200
    # 普通用户不能调用管理端点
    assert auth_client.get("/api/auth/users", headers=member_headers).status_code == 403


@pytest.mark.web
def test_login_lock_returns_429_with_retry_after(auth_client):
    _bootstrap(auth_client)
    for _ in range(5):
        auth_client.post("/api/auth/token", json={"username": "admin", "password": "wrong-password-123"})
    locked = auth_client.post("/api/auth/token", json={"username": "admin", "password": PASSWORD})
    assert locked.status_code == 429
    assert locked.headers.get("retry-after")
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_auth_api.py -v`
Expected: FAIL（fixture 因 `AccountStore` 已变抽象而 ImportError/TypeError；新增端点 404）。

- [ ] **Step 3: 实现 schemas 与端点**

`schemas.py` 追加：

```python
class AccountListItem(BaseModel):
    id: str
    username: str
    role: Literal["user", "admin"]
    status: Literal["active", "disabled"]
    token_version: int
    created_at: str
    updated_at: str


class UserListResponse(BaseModel):
    items: list[AccountListItem]
    total: int
    page: int
    page_size: int


class UserPatchRequest(BaseModel):
    role: Literal["user", "admin"] | None = None
    status: Literal["active", "disabled"] | None = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.role is None and self.status is None:
            raise ValueError("至少提供 role 或 status 之一")
        return self


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=12, max_length=256)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)
```

`service.py` 的 `create_user` 改为：

```python
    def create_user(self, actor: dict[str, str], username: str, password: str, role: str = "user") -> dict[str, str]:
        created = self.store.create_account(username, self.password_hasher.hash(password), role)
        self._audit(actor["id"], "user.create", created["id"], {"role": role})
        return created
```

`routers/auth.py`：imports 增加 `Request`、新 schema 与新异常；`login` 端点替换为：

```python
@router.post("/token", response_model=TokenResponse)
@router.post("/login", response_model=TokenResponse, include_in_schema=False)
def login(
    request: CredentialsRequest,
    response: Response,
    http_request: Request,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> dict:
    """用户名密码登录；响应只返回短期 access token，刷新令牌写入 HttpOnly Cookie。"""
    client_ip = http_request.client.host if http_request.client else ""
    try:
        payload, refresh_token = auth.login(request.username, request.password, client_ip=client_ip)
    except LoginLockedError as err:
        response.headers["Retry-After"] = str(err.retry_after_seconds)
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="too many login attempts") from err
    except AuthenticationError as err:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid username or password") from err
    _set_refresh_cookie(response, auth, refresh_token)
    return payload
```

`create_user` 端点改为 `actor: Annotated[dict, Depends(require_admin)]`（原 `_`）并 `return auth.create_user(actor, request.username, request.password, request.role)`。

文件末尾新增四个端点：

```python
@router.get("/users", response_model=UserListResponse)
def list_users(
    page: int = 1,
    page_size: int = 20,
    _: Annotated[dict[str, str], Depends(require_admin)] = None,
    auth: Annotated[AuthService, Depends(get_auth_service)] = None,
) -> dict:
    """管理员分页查看账号（不含密码哈希/令牌）。"""
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    items, total = auth.list_users(page, page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.patch("/users/{user_id}", response_model=AccountListItem)
def patch_user(
    user_id: str,
    request: UserPatchRequest,
    admin: Annotated[dict[str, str], Depends(require_admin)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> dict:
    """启用/禁用或修改角色。不能改自己；不能失去最后一名有效管理员。"""
    try:
        return auth.update_user(admin, user_id, role=request.role, status=request.status)
    except SelfAdminError as err:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="administrators cannot modify their own account here") from err
    except LastAdminError as err:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="operation would remove the last active administrator") from err
    except KeyError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="account not found") from err


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: str,
    request: PasswordResetRequest,
    admin: Annotated[dict[str, str], Depends(require_admin)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> dict[str, bool]:
    """管理员重置密码：撤销该用户全部会话并使其 access token 立即失效。"""
    try:
        auth.admin_reset_password(admin, user_id, request.password)
    except SelfAdminError as err:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="administrators must use /api/auth/password for themselves") from err
    except KeyError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="account not found") from err
    return {"ok": True}


@router.post("/password")
def change_password(
    request: ChangePasswordRequest,
    user: Annotated[dict[str, str], Depends(get_current_user)],
    response: Response,
    refresh_token: Annotated[str | None, Cookie(alias=_REFRESH_COOKIE)] = None,
    auth: AuthService = Depends(get_auth_service),
) -> dict[str, bool]:
    """当前用户修改自己的密码：撤销除当前会话外的其他刷新会话。"""
    try:
        auth.change_own_password(
            user, request.old_password, request.new_password,
            current_refresh_token=refresh_token,
        )
    except AuthenticationError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="invalid password") from err
    return {"ok": True}
```

（`Annotated[..., Depends(...)] = None` 默认值写法仅用于 FastAPI 依赖注入；实现者如遇 mypy/IDE 告警可改为无默认值并调整参数顺序，但路由行为不变。）

- [ ] **Step 4: 运行测试**

Run: `pytest tests/api/test_auth_api.py -v`
Expected: PASS（全部 9 个用例，含既有 5 个不回退）。

- [ ] **Step 5: Commit**

```bash
git add careercrew_api/routers/auth.py careercrew_api/schemas.py careercrew_api/auth/service.py tests/api/test_auth_api.py
git commit -m "feat(auth): admin user management endpoints, password reset/change and login lockout 429"
```

---

### Task A5: Origin 校验中间件 + 过期会话清理 + CORS 配置化

**Files:**
- Create: `careercrew_api/auth/middleware.py`
- Modify: `careercrew_api/main.py`（中间件挂载、CORS origins、lifespan 清理任务）
- Test: `tests/api/test_auth_api.py`（Origin 用例）、`tests/unit/test_refresh_session_cleanup.py`（新建）

**Interfaces:**
- Consumes: `load_auth_settings().trusted_origins`、`get_auth_service().store.delete_expired_refresh_sessions`。
- Produces: `TrustedOriginMiddleware(app, allowed_origins: list[str])`；`main.create_app` 挂载中间件并注册 lifespan。

- [ ] **Step 1: 写失败测试**

`tests/api/test_auth_api.py` 追加：

```python
@pytest.mark.web
def test_refresh_rejects_untrusted_origin(auth_client):
    _bootstrap(auth_client)
    auth_client.post("/api/auth/token", json={"username": "admin", "password": PASSWORD})
    evil = auth_client.post("/api/auth/refresh", headers={"Origin": "http://evil.example"})
    assert evil.status_code == 403
    trusted = auth_client.post("/api/auth/refresh", headers={"Origin": "http://localhost:5175"})
    assert trusted.status_code == 200
```

新建 `tests/unit/test_refresh_session_cleanup.py`：

```python
"""过期/长期吊销刷新会话清理。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from careercrew_api.auth.store import SqliteAccountStore, hash_token


def test_delete_expired_and_old_revoked_sessions_only(tmp_path):
    store = SqliteAccountStore(str(tmp_path / "accounts.db"))
    store.create_first_admin("admin", "$argon2$fake")
    now = datetime.now(UTC)
    store.create_refresh_session("expired", "u_001", now - timedelta(minutes=1))
    store.create_refresh_session("alive", "u_001", now + timedelta(days=1))
    store.create_refresh_session("old-revoked", "u_001", now + timedelta(days=1))
    store.revoke_refresh_session("old-revoked")
    # 手动把 revoked_at 改成 60 天前（绕过刚写入的时间戳）
    with store._connect() as conn:
        conn.execute(
            "UPDATE refresh_sessions SET revoked_at = ? WHERE token_hash = ?",
            ((now - timedelta(days=60)).isoformat(), hash_token("old-revoked")),
        )
    deleted = store.delete_expired_refresh_sessions(revoked_older_than_days=30)
    assert deleted == 2
    with store._connect() as conn:
        remaining = {r["token_hash"] for r in conn.execute("SELECT token_hash FROM refresh_sessions")}
    assert remaining == {hash_token("alive")}
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_auth_api.py -k origin -v` 与 `pytest tests/unit/test_refresh_session_cleanup.py -v`
Expected: FAIL（Origin 用例现在 200 而非 403；清理测试因中间件/lifespan 尚未接入而行为不符——`create_app` 未挂 TrustedOriginMiddleware 时 Origin 用例得到 200）。

- [ ] **Step 3: 实现中间件与 lifespan**

新建 `careercrew_api/auth/middleware.py`：

```python
"""Cookie 会话接口的 Origin 校验（CSRF 纵深防御，samesite=lax 之外的第二道闸）。"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_PROTECTED_PATHS = {"/api/auth/refresh", "/api/auth/logout"}


class TrustedOriginMiddleware(BaseHTTPMiddleware):
    """对受保护 POST 校验 Origin 头（缺失放行，非浏览器客户端不受影响）。"""

    def __init__(self, app, allowed_origins: list[str]) -> None:
        super().__init__(app)
        self._allowed = set(allowed_origins)

    async def dispatch(self, request: Request, call_next):
        if request.method == "POST" and request.url.path in _PROTECTED_PATHS:
            origin = request.headers.get("origin")
            if origin and origin not in self._allowed:
                return JSONResponse({"detail": "untrusted origin"}, status_code=403)
        return await call_next(request)
```

`main.py` 改为：

```python
"""FastAPI 应用：CORS + /api 挂载 + 生产托管 careercrew_web/dist（SPA fallback）。

开发：uvicorn careercrew_api.main:app --reload --port 8000（+ vite :5175 代理 /api）
生产：npm run build -> uvicorn 单端口托管 careercrew_web/dist（SPA fallback 到 index.html）
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from careercrew_api.auth.middleware import TrustedOriginMiddleware
from careercrew_api.routers import auth, chat, consult, data, interview, knowledge, resume
from careercrew_core.state.settings import load_auth_settings

DIST = Path(__file__).resolve().parents[1] / "careercrew_web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """过期/长期吊销刷新会话清理（守护线程，避免阻塞事件循环）。"""
    from careercrew_api.auth.dependencies import get_auth_service

    stop = threading.Event()
    interval = max(get_auth_service().settings.cleanup_interval_hours, 1) * 3600

    def _loop() -> None:
        while not stop.wait(interval):
            try:
                get_auth_service().store.delete_expired_refresh_sessions()
            except Exception:
                pass  # 清理失败不中断服务；下一轮重试

    thread = threading.Thread(target=_loop, name="refresh-session-cleanup", daemon=True)
    thread.start()
    yield
    stop.set()


def create_app() -> FastAPI:
    # 重组件保持惰性初始化，但认证生产配置必须在启动时 fail-fast。
    auth_settings = load_auth_settings()
    app = FastAPI(title="CareerCrew API", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        TrustedOriginMiddleware, allowed_origins=list(auth_settings.trusted_origins)
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(auth_settings.trusted_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # /api 路由（与旧版一致）
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(data.router, prefix="/api", tags=["data"])
    app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
    app.include_router(interview.router, prefix="/api/interview", tags=["interview"])
    app.include_router(resume.router, prefix="/api/resume", tags=["resume"])
    app.include_router(consult.router, prefix="/api/consult", tags=["consult"])
    app.include_router(knowledge.router, prefix="/api/knowledge", tags=["knowledge"])

    # 生产模式：托管 careercrew_web/dist（SPA fallback）
    if DIST.exists():
        app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            file_path = DIST / full_path
            if full_path and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(str(DIST / "index.html"))

    return app


app = create_app()
```

- [ ] **Step 4: 运行测试**

Run: `pytest tests/api/test_auth_api.py tests/unit/test_refresh_session_cleanup.py -v`
Expected: PASS（注意：`auth_client` fixture 用 TestClient 且 settings 默认 trusted_origins，Origin 用例中 localhost:5175 在默认列表内）。

- [ ] **Step 5: Commit**

```bash
git add careercrew_api/auth/middleware.py careercrew_api/main.py tests/api/test_auth_api.py tests/unit/test_refresh_session_cleanup.py
git commit -m "feat(auth): trusted origin middleware, configurable CORS and refresh session cleanup task"
```

---

### Task A6: SQLite → Postgres 账号迁移脚本 + 本机执行迁移

**Files:**
- Create: `scripts/migrate_accounts_postgres.py`
- Test: `tests/unit/test_account_migration.py`（纯逻辑）+ `tests/integration/test_account_migration_postgres.py`（缺 `POSTGRES_TEST_DSN` skip）
- 执行：对本机 `data/db/accounts.db` → `postgresql://careercrew:careercrew@localhost:5432/careercrew` 实施迁移并归档 SQLite

**Interfaces:**
- Consumes: `AuthSettings.database_url`（经 settings 解析）、SQLite accounts 表（A2 升级后含 status/token_version/updated_at）。
- Produces: `scripts/migrate_accounts_postgres.py` CLI——`--sqlite-db`（默认 `data/db/accounts.db`）、`--postgres-dsn`（默认读 settings）、`--archive-sqlite`、`--apply`；默认 dry-run。模块级 `sqlite_accounts(path) -> list[dict]`、`build_plan(sqlite_rows, pg_rows) -> tuple[list[dict], list[dict]]`、`apply_migration(dsn, rows) -> int`。

- [ ] **Step 1: 写失败测试（纯逻辑，无需 DB）**

新建 `tests/unit/test_account_migration.py`：

```python
"""账号迁移计划逻辑：幂等、保留哈希、冲突检测。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from migrate_accounts_postgres import build_plan  # noqa: E402

PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$abc$def"

ADMIN = {
    "id": "u_001", "username": "liyou", "password_hash": PASSWORD_HASH,
    "role": "admin", "status": "active", "token_version": 0,
}


def test_plan_inserts_new_accounts():
    to_insert, conflicts = build_plan([ADMIN], [])
    assert to_insert == [ADMIN]
    assert conflicts == []


def test_plan_skips_identical_accounts():
    to_insert, conflicts = build_plan([ADMIN], [dict(ADMIN)])
    assert to_insert == [] and conflicts == []


def test_plan_reports_conflicting_accounts():
    changed = dict(ADMIN, role="user")
    to_insert, conflicts = build_plan([changed], [dict(ADMIN)])
    assert to_insert == []
    assert conflicts == [("u_001", "role")]


def test_plan_keeps_existing_hash_and_missing_columns_defaulted():
    legacy = {"id": "u_001", "username": "liyou", "password_hash": PASSWORD_HASH, "role": "admin"}
    normalized = build_plan([legacy], [])[0][0]
    assert normalized["status"] == "active" and normalized["token_version"] == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/unit/test_account_migration.py -v`
Expected: FAIL（`ModuleNotFoundError`/脚本不存在）。

- [ ] **Step 3: 实现脚本**

新建 `scripts/migrate_accounts_postgres.py`：

```python
"""SQLite → Postgres 账号迁移（幂等；refresh 会话与登录限速/审计不迁移）。

默认 dry-run；--apply 才写 Postgres。--archive-sqlite 在 apply 成功后把
SQLite 文件改名为 <原名>.pre-postgres-<时间戳>.bak（默认启用于 apply）。
迁移完成后所有旧 refresh token 失效（未迁移会话），全员需重新登录。
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SQLITE = PROJECT_ROOT / "data" / "db" / "accounts.db"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def sqlite_accounts(path: str | Path) -> list[dict]:
    db = Path(path)
    if not db.is_file():
        raise FileNotFoundError(f"sqlite accounts db not found: {db}")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, username, password_hash, role, "
            "COALESCE(status, 'active') AS status, COALESCE(token_version, 0) AS token_version "
            "FROM accounts ORDER BY created_at, id"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r["id"], "username": r["username"], "password_hash": r["password_hash"],
            "role": r["role"], "status": r["status"], "token_version": int(r["token_version"]),
        }
        for r in rows
    ]


def build_plan(sqlite_rows: list[dict], pg_rows: list[dict]) -> tuple[list[dict], list[tuple]]:
    """返回 (to_insert, conflicts)。同 id 且 username/role/password_hash 全等 → skip；
    任一不同 → conflict（不覆盖）。"""
    existing = {r["id"]: r for r in pg_rows}
    to_insert: list[dict] = []
    conflicts: list[tuple] = []
    for row in sqlite_rows:
        target = existing.get(row["id"])
        if target is None:
            to_insert.append(row)
            continue
        for field in ("username", "role", "password_hash"):
            if target.get(field) != row.get(field):
                conflicts.append((row["id"], field))
                break
    return to_insert, conflicts


def apply_migration(dsn: str, rows: list[dict]) -> int:
    import psycopg

    inserted = 0
    with psycopg.connect(dsn) as conn:
        for row in rows:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO auth_accounts (id, username, password_hash, role, status, token_version) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                    (row["id"], row["username"], row["password_hash"], row["role"],
                     row["status"], row["token_version"]),
                )
            inserted += 1
    return inserted


def _pg_accounts(dsn: str) -> list[dict]:
    import psycopg
    import psycopg.rows

    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        rows = conn.execute(
            "SELECT id, username, password_hash, role, status, token_version FROM auth_accounts"
        ).fetchall()
    return [dict(r) for r in rows]


def _auth_dsn() -> str:
    sys.path.insert(0, str(PROJECT_ROOT))
    from careercrew_core.state.settings import load_auth_settings

    settings = load_auth_settings()
    if settings.backend != "postgres" or not settings.database_url:
        raise SystemExit("auth.backend 不是 postgres 或 DSN 缺失；先检查 config/settings.yaml")
    return settings.database_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-db", default=str(_DEFAULT_SQLITE))
    parser.add_argument("--postgres-dsn", default="", help="默认读 settings 的 auth.database_url")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--archive-sqlite", action="store_true", default=True)
    parser.add_argument("--no-archive-sqlite", action="store_false", dest="archive_sqlite")
    args = parser.parse_args(argv)

    dsn = args.postgres_dsn or _auth_dsn()
    rows = sqlite_accounts(args.sqlite_db)
    if not rows:
        print("SQLite 账号表为空，无需迁移")
        return 0
    to_insert, conflicts = build_plan(rows, _pg_accounts(dsn))
    print(f"mode={'APPLY' if args.apply else 'DRY-RUN'} accounts={len(rows)} "
          f"to_insert={len(to_insert)} conflicts={len(conflicts)}")
    for source_id, field in conflicts:
        print(f"- CONFLICT account {source_id}: {field} differs")
    for row in to_insert:
        print(f"- {'APPLY' if args.apply else 'DRY-RUN'} insert {row['id']} ({row['username']}, {row['role']})")
    if args.apply and to_insert:
        apply_migration(dsn, to_insert)
        print(f"inserted={len(to_insert)}")
    if args.apply and args.archive_sqlite and to_insert:
        db = Path(args.sqlite_db)
        stamp = _utcnow().strftime("%Y%m%d-%H%M%S")
        backup = db.with_name(f"{db.name}.pre-postgres-{stamp}.bak")
        db.rename(backup)
        print(f"ARCHIVE sqlite -> {backup}")
        print("注意：旧刷新令牌未迁移，全部用户需要重新登录。")
    return 1 if conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行单元测试与集成测试**

Run: `pytest tests/unit/test_account_migration.py -v`
Expected: PASS。

新建 `tests/integration/test_account_migration_postgres.py`：

```python
"""真实 Postgres 迁移集成测试（缺 POSTGRES_TEST_DSN 跳过）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

DSN = os.environ.get("POSTGRES_TEST_DSN", "").strip()

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from migrate_accounts_postgres import apply_migration  # noqa: E402

pytestmark = pytest.mark.integration

pytestmark = pytest.mark.skipif(not DSN, reason="POSTGRES_TEST_DSN not set")


@pytest.fixture
def clean_pg():
    import psycopg

    with psycopg.connect(DSN) as conn, conn.transaction():
        conn.execute("DELETE FROM auth_refresh_sessions")
        conn.execute("DELETE FROM auth_accounts")
    yield
    with psycopg.connect(DSN) as conn, conn.transaction():
        conn.execute("DELETE FROM auth_refresh_sessions")
        conn.execute("DELETE FROM auth_accounts")


def test_apply_and_idempotent_rerun(clean_pg):
    row = {
        "id": "u_001", "username": "liyou", "password_hash": "$argon2id$fake",
        "role": "admin", "status": "active", "token_version": 0,
    }
    assert apply_migration(DSN, [row]) == 1
    assert apply_migration(DSN, [row]) == 1  # ON CONFLICT DO NOTHING 幂等
```

Run: `$env:POSTGRES_TEST_DSN="postgresql://careercrew:careercrew@localhost:5432/careercrew"; pytest tests/integration/test_account_migration_postgres.py -v`
Expected: PASS。

- [ ] **Step 5: 本机实施迁移（真实数据）**

```powershell
$env:PYTHONPATH=(Get-Location).Path
# 1) 先 dry-run 核对（应看到 u_001/liyou/admin，conflicts=0）
F:\Python_develop\miniconda3\envs\careercrew\python.exe scripts\migrate_accounts_postgres.py
# 2) apply + 归档 SQLite
F:\Python_develop\miniconda3\envs\careercrew\python.exe scripts\migrate_accounts_postgres.py --apply
# 3) 复跑确认幂等（accounts=0 或全部 skip，conflicts=0）
F:\Python_develop\miniconda3\envs\careercrew\python.exe scripts\migrate_accounts_postgres.py
```

验证 SQL：用 A2 的 `PostgresAccountStore` 确认 `u_001` 的 `password_hash` 与 SQLite 原值逐字节相等（`scripts/migrate_accounts_postgres.py --sqlite-db <归档bak>` 的 dry-run conflicts 必须为 0，等价于哈希一致）。

- [ ] **Step 6: Commit**

```bash
git add scripts/migrate_accounts_postgres.py tests/unit/test_account_migration.py tests/integration/test_account_migration_postgres.py
git commit -m "feat(auth): idempotent sqlite-to-postgres account migration script"
```

---

## Part B：私有/公共知识库（owner_user_id + visibility）

### Task B1: QdrantStore / FakeVectorStore 支持 owner_user_id + visibility + `__access_user`

**Files:**
- Modify: `careercrew_ai/vector_store/base_vector_store.py`（`ACCESS_USER_KEY` 常量、`_matches`、Fake upsert owner、Fake list_docs、Fake set_payload_by_filter）
- Modify: `careercrew_ai/vector_store/qdrant_store.py`（upsert owner 双键、`_filter_expr`、索引、list_docs、set_payload_by_filter）
- Test: `tests/unit/test_qdrant_store.py`（追加用例）

**Interfaces:**
- Consumes: `VectorRecord.metadata` 的 `owner_user_id`/`user_id`/`visibility` 键。
- Produces:
  - `base_vector_store.ACCESS_USER_KEY = "__access_user"`（qdrant_store 复用导入）。
  - `_matches(metadata, filters)`：`ACCESS_USER_KEY` 语义 `visibility=="public" or owner_user_id==v`。
  - `QdrantStore.set_payload_by_filter(payload: dict, filters: dict) -> int`（payload 值为 `None` 表示删除该键）；Fake 同名实现。
  - `QdrantStore.list_docs`/Fake：条目含 `visibility`、`owner_user_id`，聚合键 `(doc, visibility)`。
  - `upsert`：`owner = str(payload.get("owner_user_id") or payload.get("user_id") or "")`（物理 ID 编码公式不变）。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_qdrant_store.py` 追加（复用文件内 `_store(valid_config_data, collection)` helper）：

```python
def _record(doc: str, owner: str, visibility: str, text: str = "t"):
    return VectorRecord(
        id=f"{doc}-p0", dense=[0.1] * 1024,
        text=text,
        metadata={"doc": doc, "source": f"{doc}.pdf", "category": "knowledge",
                  "owner_user_id": owner, "visibility": visibility},
    )


def test_access_filter_sees_public_and_own_private_only(valid_config_data):
    store = _store(valid_config_data)
    store.upsert([
        _record("mine", "u_001", "private"),
        _record("public-doc", "u_002", "public"),
        _record("theirs", "u_002", "private"),
    ])
    hits = store.query([0.1] * 1024, top_k=10, filters={"__access_user": "u_001"})
    docs = {h.id for h in hits}
    assert docs == {"mine-p0", "public-doc-p0"}


def test_access_filter_key_does_not_leak_into_must(valid_config_data):
    store = _store(valid_config_data)
    store.upsert([_record("cat-doc", "u_001", "private")])
    hits = store.query(
        [0.1] * 1024, top_k=10,
        filters={"__access_user": "u_001", "category": "knowledge"},
    )
    assert {h.id for h in hits} == {"cat-doc-p0"}


def test_list_docs_separates_same_name_by_visibility(valid_config_data):
    store = _store(valid_config_data)
    store.upsert([
        _record("same", "u_001", "private"),
        _record("same", "u_002", "public"),
    ])
    docs = store.list_docs(filters={"__access_user": "u_001"})
    by_vis = {d["visibility"] for d in docs}
    assert by_vis == {"private", "public"}
    public = next(d for d in docs if d["visibility"] == "public")
    assert public["owner_user_id"] == "u_002"


def test_set_payload_by_filter_toggles_visibility(valid_config_data):
    store = _store(valid_config_data)
    store.upsert([_record("publish-me", "u_001", "private")])
    n = store.set_payload_by_filter(
        {"visibility": "public"},
        {"owner_user_id": "u_001", "doc": "publish-me"},
    )
    assert n == 1
    assert store.count(filters={"doc": "publish-me", "visibility": "public"}) == 1
    assert store.count(filters={"doc": "publish-me", "visibility": "private"}) == 0
    # None 值删除键
    store.set_payload_by_filter({"visibility": None}, {"doc": "publish-me"})
    assert store.count(filters={"doc": "publish-me"}) == 1


def test_upsert_reads_owner_from_owner_user_id_first(valid_config_data):
    store = _store(valid_config_data)
    store.upsert([VectorRecord(
        id="legacy-id", dense=[0.1] * 1024,
        metadata={"doc": "d", "user_id": "u_001"},
    )])
    store.upsert([VectorRecord(
        id="legacy-id", dense=[0.1] * 1024,
        metadata={"doc": "d", "owner_user_id": "u_001"},
    )])
    # 两种键名必须映射到同一物理 ID：合计只有 1 个点
    assert store.count(filters={"doc": "d"}) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/unit/test_qdrant_store.py -k "access_filter or list_docs or set_payload or owner" -v`
Expected: FAIL（`__access_user` 目前被当作普通字段过滤 → 检索为空；`set_payload_by_filter` 不存在 AttributeError）。

- [ ] **Step 3: 实现**

`base_vector_store.py`：

```python
ACCESS_USER_KEY = "__access_user"


def _matches(metadata: dict, filters: dict) -> bool:
    for k, v in filters.items():
        if k == ACCESS_USER_KEY:
            visible = (
                metadata.get("visibility") == "public"
                or metadata.get("owner_user_id") == v
            )
            if not visible:
                return False
            continue
        if metadata.get(k) != v:
            return False
    return True
```

`FakeVectorStore.upsert` 的 owner 行改为：

```python
            owner = str((r.metadata or {}).get("owner_user_id")
                        or (r.metadata or {}).get("user_id") or "")
```

`FakeVectorStore.list_docs` 主体替换为：

```python
    def list_docs(self, limit: int = 1000, filters: dict | None = None) -> list[dict]:
        docs: dict[tuple, dict] = {}
        for record in self._records.values():
            if filters and not _matches(record.metadata, filters):
                continue
            doc = str(record.metadata.get("doc") or record.id)
            visibility = str(record.metadata.get("visibility", "private"))
            key = (doc, visibility)
            entry = docs.setdefault(key, {
                "doc": doc,
                "source": record.metadata.get("source", ""),
                "points": 0,
                "category": record.metadata.get("category", ""),
                "visibility": visibility,
                "owner_user_id": str(record.metadata.get("owner_user_id", "")),
            })
            entry["points"] += 1
            if len(docs) >= limit:
                break
        return list(docs.values())

    def set_payload_by_filter(self, payload: dict, filters: dict) -> int:
        count = 0
        for record in self._records.values():
            if filters and not _matches(record.metadata, filters):
                continue
            for k, v in payload.items():
                if v is None:
                    record.metadata.pop(k, None)
                else:
                    record.metadata[k] = v
            count += 1
        return count
```

`qdrant_store.py`：
- 顶部：`from careercrew_ai.vector_store.base_vector_store import ACCESS_USER_KEY, BaseVectorStore, QueryResult, VectorRecord`（追加 `ACCESS_USER_KEY`）。
- `_ensure_collection` 索引循环列表（`:78`）改为 `("doc", "type", "page", "source", "category", "user_id", "owner_user_id", "visibility", "image_path")`。
- `upsert`（`:158`）改为 `owner = str(payload.get("owner_user_id") or payload.get("user_id") or "")`。
- `_filter_expr` 替换为：

```python
    @staticmethod
    def _filter_expr(filters: dict | None):
        from qdrant_client.models import (
            FieldCondition,
            Filter,
            MatchAny,
            MatchValue,
        )

        if not filters:
            return None
        must = []
        should = None
        for k, v in filters.items():
            if k == ACCESS_USER_KEY:
                should = [
                    FieldCondition(key="visibility", match=MatchValue(value="public")),
                    FieldCondition(key="owner_user_id", match=MatchValue(value=str(v))),
                ]
                continue
            if isinstance(v, list):
                must.append(FieldCondition(key=k, match=MatchAny(any=list(v))))
            elif isinstance(v, (str, int, float, bool)):
                must.append(FieldCondition(key=k, match=MatchValue(value=v)))
        return Filter(must=must, should=should) if (must or should) else None
```

- `list_docs`（`:279-303`）替换为（与 Fake 同构）：

```python
    def list_docs(self, limit: int = 1000, filters: dict | None = None) -> list[dict]:
        """按 payload.doc 聚合列出已入库文档（知识库管理用）；同名单按 visibility 分开。"""
        docs: dict[tuple, dict] = {}
        offset = None
        while True:
            points, offset = self._client.scroll(
                self._collection, limit=1000, offset=offset,
                scroll_filter=self._filter_expr(filters),
                with_payload=True, with_vectors=False,
            )
            for p in points:
                payload = p.payload or {}
                doc = payload.get("doc") or payload.get("_id", "")
                visibility = str(payload.get("visibility", "private"))
                key = (doc, visibility)
                entry = docs.setdefault(key, {
                    "doc": doc,
                    "source": payload.get("source", ""),
                    "points": 0,
                    "category": payload.get("category", ""),
                    "visibility": visibility,
                    "owner_user_id": str(payload.get("owner_user_id", "")),
                })
                entry["points"] += 1
            if offset is None or len(docs) >= limit:
                break
        return list(docs.values())
```

- 新增 `set_payload_by_filter`（`delete_by_metadata` 之后）：

```python
    def set_payload_by_filter(self, payload: dict, filters: dict) -> int:
        """按过滤条件更新 payload（值为 None 表示删除该键）；返回命中点数。"""
        from qdrant_client.models import PointIdsList

        flt = self._filter_expr(filters)
        if not flt:
            return 0
        qids: list[str] = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                self._collection, scroll_filter=flt, limit=1000,
                offset=offset, with_payload=False, with_vectors=False,
            )
            qids.extend(p.id for p in points)
            if offset is None or len(qids) > 10000:
                break
        if qids:
            self._client.set_payload(
                self._collection, payload=payload,
                points=PointIdsList(points=qids),
            )
        return len(qids)
```

- [ ] **Step 4: 运行测试**

Run: `pytest tests/unit/test_qdrant_store.py -v` 与 `pytest tests/unit/test_memory_search.py tests/unit/test_hybrid_search_rrf.py tests/unit/test_multimodal_search.py -v`
Expected: PASS（既有用例 + 新增用例全绿；情景记忆 `user_id` 路径经双键兼容不回归）。

- [ ] **Step 5: Commit**

```bash
git add careercrew_ai/vector_store/base_vector_store.py careercrew_ai/vector_store/qdrant_store.py tests/unit/test_qdrant_store.py
git commit -m "feat(kb): owner_user_id + visibility payload with unified __access_user filter"
```

---

### Task B2: runtime 可见性改造 + FakeRuntime 桩升级

**Files:**
- Modify: `careercrew_api/runtime.py`
- Modify: `tests/api/conftest.py`（FakeRuntime 知识库桩）
- Test: `tests/api/test_knowledge_api.py`、`tests/api/test_tenant_isolation_api.py` 同步适配（B3 补新用例）

**Interfaces:**
- Consumes: B1 的 `ACCESS_USER_KEY` 约定与 `set_payload_by_filter`。
- Produces:
  - `ingest_document(..., visibility: str = "private")`（metadata 写 `owner_user_id`/`visibility`）。
  - `knowledge_status(user_id, scope: str = "all")`（scope ∈ all/public/private）。
  - `delete_document(user_id, doc_id, is_admin: bool = False) -> tuple[int, bool]`：返回 `(deleted_points, public_blocked)`；`public_blocked=True` 表示「存在公共条目且非 admin」→ 路由 403。
  - `publish_document(user_id, doc_id) -> int`、`unpublish_document(user_id, doc_id) -> int`（admin 对自己名下文档做 `set_payload_by_filter`）。
  - `run_knowledge_ask_stream(..., scope: str = "all")`。
  - `_make_tools(..., knowledge_access_filters: dict | None = None)`：knowledge 分支 filters 使用 `knowledge_access_filters or {"__access_user": user_id}`；其余 5 个分支 `filters={"__access_user": user_id}`。
  - `knowledge_asset_owned` 改访问语义（公共图任何登录用户可读）。

- [ ] **Step 1: 改 runtime.py**

`ingest_document`（`:1072-1106`）签名加 `visibility: str = "private"`，`owner_metadata` 行改为：

```python
        if visibility not in ("private", "public"):
            raise ValueError(f"invalid visibility: {visibility}")
        owner_metadata = {**(metadata or {}), "owner_user_id": user_id, "visibility": visibility}
```

`knowledge_status`（`:1113-1117`）替换为：

```python
    def knowledge_status(self, user_id: str, scope: str = "all") -> dict:
        """知识库状态：总点数 + 文档列表。scope: all（公共+本人私有）/public/private。"""
        self._ensure_heavy()
        docs = self.store.list_docs(filters=self._knowledge_scope_filters(user_id, scope))
        return {"points": sum(int(doc.get("points", 0)) for doc in docs), "docs": docs}

    @staticmethod
    def _knowledge_scope_filters(user_id: str, scope: str) -> dict:
        if scope == "public":
            return {"visibility": "public"}
        if scope == "private":
            return {"owner_user_id": user_id}
        return {"__access_user": user_id}
```

`delete_document`（`:1108-1111`）替换为：

```python
    def delete_document(self, user_id: str, doc_id: str, is_admin: bool = False) -> tuple[int, bool]:
        """删除知识文档向量点。返回 (deleted, public_blocked)。

        非 admin 只能删本人私有；admin 可额外删除公共条目。
        """
        self._ensure_heavy()
        visible = self.store.list_docs(filters={"__access_user": user_id, "doc": doc_id})
        if not visible:
            return 0, False
        has_public = any(d.get("visibility") == "public" for d in visible)
        if has_public and not is_admin:
            return 0, True
        deleted = self.store.delete_by_metadata(
            {"owner_user_id": user_id, "doc": doc_id, "visibility": "private"}
        )
        if has_public and is_admin:
            deleted += self.store.delete_by_metadata({"doc": doc_id, "visibility": "public"})
        return deleted, False

    def publish_document(self, user_id: str, doc_id: str) -> int:
        self._ensure_heavy()
        return self.store.set_payload_by_filter(
            {"visibility": "public"}, {"owner_user_id": user_id, "doc": doc_id}
        )

    def unpublish_document(self, user_id: str, doc_id: str) -> int:
        self._ensure_heavy()
        return self.store.set_payload_by_filter(
            {"visibility": "private"}, {"owner_user_id": user_id, "doc": doc_id}
        )
```

`knowledge_asset_owned`（`:1119-1127`）最后一行改为：

```python
        return bool(self.store.metadata_exists({"__access_user": user_id, "image_path": str(resolved)}))
```

`_make_tools`（`:716-799`）：6 处 `filters={"user_id": user_id}` 全部改为 `filters={"__access_user": user_id}`；knowledge 分支改用 `filters=knowledge_access_filters or {"__access_user": user_id}`，签名加 `knowledge_access_filters: dict | None = None` 参数（位置放在 `rag_category=None` 之后）。

`run_knowledge_ask_stream`（`:528-546`）与 `_run_knowledge_ask_stream_impl`（`:552` 起）加 `scope: str = "all"` 参数并透传：`impl` 内构造 `access_filters = self._knowledge_scope_filters(user_id, scope)`，在构造 knowledge agent 工具的路径上把 `knowledge_access_filters=access_filters` 传给 `_make_tools`（按 `new_knowledge_advisor` 现有参数链路逐层透传；consult 会诊内的 knowledge 工具保持 `__access_user` 默认）。

- [ ] **Step 2: 升级 FakeRuntime 桩（tests/api/conftest.py）**

`ingest_document` 桩：签名加 `visibility: str = "private"`，`ingest_calls` 记录 visibility，`knowledge_docs_by_user[user_id]` 条目加 `"owner_user_id": user_id, "visibility": visibility`。

`knowledge_status` 桩替换为：

```python
    def knowledge_status(self, user_id: str, scope: str = "all") -> dict:
        all_docs: list[dict] = []
        for owner, docs in self.knowledge_docs_by_user.items():
            for doc in docs:
                entry = dict(doc)
                entry.setdefault("owner_user_id", owner)
                entry.setdefault("visibility", "private")
                all_docs.append(entry)
        if scope == "public":
            all_docs = [d for d in all_docs if d["visibility"] == "public"]
        elif scope == "private":
            all_docs = [d for d in all_docs if d["owner_user_id"] == user_id]
        else:
            all_docs = [
                d for d in all_docs
                if d["visibility"] == "public" or d["owner_user_id"] == user_id
            ]
        return {"points": sum(int(d.get("points", 0)) for d in all_docs), "docs": all_docs}
```

`delete_document` 桩替换为：

```python
    def delete_document(self, user_id: str, doc_id: str, is_admin: bool = False) -> tuple[int, bool]:
        visible = [
            (owner, d) for owner, docs in self.knowledge_docs_by_user.items()
            for d in docs
            if d.get("doc") == doc_id
            and (d.get("visibility", "private") == "public" or owner == user_id)
        ]
        if not visible:
            return 0, False
        if any(d.get("visibility") == "public" for _o, d in visible) and not is_admin:
            return 0, True
        deleted = 0
        for owner, d in visible:
            docs = self.knowledge_docs_by_user.get(owner, [])
            if d in docs:
                docs.remove(d)
                deleted += int(d.get("points", 0))
        return deleted, False

    def publish_document(self, user_id: str, doc_id: str) -> int:
        n = 0
        for d in self.knowledge_docs_by_user.get(user_id, []):
            if d.get("doc") == doc_id:
                d["visibility"] = "public"
                n += int(d.get("points", 0))
        return n

    def unpublish_document(self, user_id: str, doc_id: str) -> int:
        n = 0
        for d in self.knowledge_docs_by_user.get(user_id, []):
            if d.get("doc") == doc_id:
                d["visibility"] = "private"
                n += int(d.get("points", 0))
        return n
```

`run_knowledge_ask_stream` 桩签名加 `scope: str = "all"`（新增类属性 `knowledge_ask_scopes: list[str] = []`，每次调用 `self.knowledge_ask_scopes.append(scope)`，其余行为不变）。

- [ ] **Step 3: 运行既有知识库测试确认无回归**

Run: `pytest tests/api/test_knowledge_api.py tests/api/test_tenant_isolation_api.py tests/api/test_upload_isolation_api.py -v`
Expected: PASS（B3 前只做签名兼容；如有断言旧 `delete_document` 返回 int 的用例，按新 tuple 协议适配断言：`deleted, _ = ...`）。

- [ ] **Step 4: Commit**

```bash
git add careercrew_api/runtime.py tests/api/conftest.py tests/api/test_knowledge_api.py tests/api/test_tenant_isolation_api.py
git commit -m "feat(kb): runtime visibility-aware knowledge access, delete, publish and scope filters"
```

---

### Task B3: knowledge 路由 + schemas + 线程 scope 扩展 + API 测试矩阵

**Files:**
- Modify: `careercrew_api/routers/knowledge.py`
- Modify: `careercrew_api/schemas.py`（`KnowledgeAskRequest.scope`）
- Modify: `careercrew_api/routers/data.py`（`RetrievalScopeRequest.type` 允许 `public`/`private`）
- Test: `tests/api/test_knowledge_api.py`（新用例）、`tests/api/test_tenant_isolation_api.py`（公共/私有矩阵）

**Interfaces:**
- Consumes: B2 runtime 新签名；`AdminUser` 依赖。
- Produces:
  - `POST /api/knowledge/upload`：`visibility: str = Form("private")`；非 admin 传 `public` → 403。
  - `GET /api/knowledge?scope=all|public|private`。
  - `DELETE /api/knowledge/{doc_id}`：`(deleted, public_blocked)` → 403 或 404。
  - `POST /api/knowledge/{doc_id}/publish`、`POST /api/knowledge/{doc_id}/unpublish`（AdminUser）。
  - `POST /api/knowledge/ask` body 增 `scope`。

- [ ] **Step 1: 写失败测试**

`tests/api/test_knowledge_api.py` 追加（复用其现有 `client`/`fake_runtime` fixture）：

```python
@pytest.mark.web
def test_list_and_ask_pass_scope(client, fake_runtime):
    fake_runtime.knowledge_docs_by_user["u_001"] = [
        {"doc": "d1", "source": "s", "points": 1}
    ]
    assert client.get("/api/knowledge", params={"scope": "public"}).status_code == 200
    resp = client.post("/api/knowledge/ask", json={
        "question": "q", "thread_id": "t1", "scope": "private",
    })
    assert resp.status_code == 200
    assert "private" in getattr(fake_runtime, "knowledge_ask_scopes", [])
```

`tests/api/test_tenant_isolation_api.py` 追加（用其 `tenant_api` fixture 的 alice/bob 双账号；实现者先读该文件 `:1-60` 的 fixture 以取得 admin 登录凭据，若 fixture 未暴露 admin 登录，则按 `tests/api/conftest.py:468-482` 的 override 模式构造 admin client）：

```python
@pytest.mark.web
def test_knowledge_public_visibility_matrix(tenant_api, tmp_path) -> None:
    client, runtime, headers, ids = tenant_api
    runtime.knowledge_docs_by_user[ids["alice"]] = [
        {"doc": "alice-private", "source": "a.md", "points": 2,
         "owner_user_id": ids["alice"], "visibility": "private"},
        {"doc": "alice-public", "source": "a2.md", "points": 3,
         "owner_user_id": ids["alice"], "visibility": "public"},
    ]
    runtime.knowledge_docs_by_user[ids["bob"]] = [
        {"doc": "bob-private", "source": "b.md", "points": 1,
         "owner_user_id": ids["bob"], "visibility": "private"},
    ]
    alice_all = {d["doc"] for d in client.get("/api/knowledge", headers=headers["alice"]).json()["docs"]}
    bob_all = {d["doc"] for d in client.get("/api/knowledge", headers=headers["bob"]).json()["docs"]}
    assert alice_all == {"alice-private", "alice-public"}
    assert bob_all == {"alice-public", "bob-private"}  # 公共对所有人可见
    public_only = {d["doc"] for d in client.get("/api/knowledge", params={"scope": "public"}, headers=headers["bob"]).json()["docs"]}
    assert public_only == {"alice-public"}
    private_only = {d["doc"] for d in client.get("/api/knowledge", params={"scope": "private"}, headers=headers["bob"]).json()["docs"]}
    assert private_only == {"bob-private"}
    # 非 admin 删除公共 → 403；删除他人私有 → 404
    assert client.delete("/api/knowledge/alice-public", headers=headers["bob"]).status_code == 403
    assert client.delete("/api/knowledge/alice-private", headers=headers["bob"]).status_code == 404
    # 非 admin 发布 → 403
    assert client.post("/api/knowledge/bob-private/publish", headers=headers["bob"]).status_code == 403
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_tenant_isolation_api.py -k public_visibility -v` 与 `pytest tests/api/test_knowledge_api.py -k scope -v`
Expected: FAIL（scope 参数与 403/404 语义尚不存在）。

- [ ] **Step 3: 实现**

`schemas.py`：

```python
class KnowledgeAskRequest(BaseModel):
    question: str = Field(min_length=1)
    thread_id: str = "knowledge"
    category: str = ""  # resume / knowledge / interview，空串=全部
    scope: str = "all"  # all（公共+本人私有）| public | private
```

`data.py` 的 `RetrievalScopeRequest._check`：

```python
        if self.type not in ("all", "category", "public", "private"):
            raise ValueError("type 必须为 all / category / public / private")
        if self.type in ("all", "public", "private"):
            self.category_id = None
            return self
```

`knowledge.py`：

- `upload_knowledge` 加 `visibility: str = Form("private")`；函数开头：

```python
    if visibility not in ("private", "public"):
        raise HTTPException(status_code=422, detail="visibility 必须为 private 或 public")
    if visibility == "public" and current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以发布公共知识库")
```

`_run_ingest_job` 加 `visibility: str = "private"` 参数并 `rt.ingest_document(..., visibility=visibility)`；`upload_knowledge` 启动线程时透传。

- `list_knowledge` 加 `scope: str = "all"` Query 参数：`scope` 非法 → 422；传 `rt.knowledge_status(current_user["id"], scope)`。
- `delete_knowledge` 改为：

```python
@router.delete("/{doc_id}")
def delete_knowledge(
    doc_id: str,
    current_user: CurrentUser,
    rt: CareerCrewRuntime = Depends(get_runtime_dep),
) -> dict:
    """删除指定文档的全部向量点（私有仅本人；公共仅管理员）。"""
    try:
        deleted, public_blocked = rt.delete_document(
            current_user["id"], doc_id, is_admin=current_user["role"] == "admin"
        )
    except RuntimeInitError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if public_blocked:
        raise HTTPException(status_code=403, detail="只有管理员可以删除公共知识库文档")
    if deleted == 0:
        raise HTTPException(status_code=404, detail=f"知识文档不存在：{doc_id}")
    return {"deleted": deleted, "doc_id": doc_id}
```

- 新增 publish/unpublish：

```python
@router.post("/{doc_id}/publish")
def publish_knowledge(
    doc_id: str,
    _: Annotated[dict[str, str], Depends(require_admin)],
    current_user: CurrentUser,
    rt: CareerCrewRuntime = Depends(get_runtime_dep),
) -> dict:
    """管理员把自己名下的私有文档发布为公共。"""
    try:
        n = rt.publish_document(current_user["id"], doc_id)
    except RuntimeInitError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if n == 0:
        raise HTTPException(status_code=404, detail=f"知识文档不存在或不可发布：{doc_id}")
    return {"published": n, "doc_id": doc_id}


@router.post("/{doc_id}/unpublish")
def unpublish_knowledge(
    doc_id: str,
    _: Annotated[dict[str, str], Depends(require_admin)],
    current_user: CurrentUser,
    rt: CareerCrewRuntime = Depends(get_runtime_dep),
) -> dict:
    """管理员下架公共文档（转为自己的私有）。"""
    try:
        n = rt.unpublish_document(current_user["id"], doc_id)
    except RuntimeInitError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if n == 0:
        raise HTTPException(status_code=404, detail=f"知识文档不存在或不可下架：{doc_id}")
    return {"unpublished": n, "doc_id": doc_id}
```

- `ask_knowledge` 的 `_run`：`rt.run_knowledge_ask_stream(req.question, current_user["id"], thread_id=req.thread_id, cb=cb, category=req.category, scope=req.scope, cancel_check=cancel.check)`。

- [ ] **Step 4: 运行测试**

Run: `pytest tests/api/test_knowledge_api.py tests/api/test_tenant_isolation_api.py tests/api/test_upload_isolation_api.py tests/api/test_knowledge_image_api.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add careercrew_api/routers/knowledge.py careercrew_api/schemas.py careercrew_api/routers/data.py tests/api/test_knowledge_api.py tests/api/test_tenant_isolation_api.py
git commit -m "feat(kb): knowledge upload/list/ask scope, public publish endpoints and delete semantics"
```

---

### Task B4: payload 可见性迁移脚本 + 真实数据迁移 + 旧迁移脚本兼容

**Files:**
- Create: `scripts/migrate_knowledge_visibility.py`
- Modify: `scripts/migrate_legacy_tenant.py`（owner 双键兼容）
- Test: `tests/unit/test_knowledge_visibility_migration.py`（`:memory:` Qdrant）
- 执行：对真实 `careercrew_mm` 实施（先 snapshot、后 apply、复跑 0 变更、物理 ID 不变）

**Interfaces:**
- Consumes: settings 的 knowledge 集合名、Qdrant client scroll/set_payload。
- Produces: CLI `--collection`（默认 knowledge 集合）、`--default-owner`（默认 `u_001`）、`--apply`；默认 dry-run。模块级 `migrate_collection(client, collection, default_owner, apply) -> (changed, skipped, conflicts)`。

- [ ] **Step 1: 写失败测试（`:memory:` Qdrant）**

新建 `tests/unit/test_knowledge_visibility_migration.py`：

```python
"""knowledge 集合 payload 迁移：user_id → owner_user_id + visibility=private，物理 ID 不变。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from migrate_knowledge_visibility import migrate_collection  # noqa: E402
from careercrew_ai.vector_store.qdrant_store import QdrantStore
from careercrew_ai.vector_store.base_vector_store import VectorRecord


def _seed_store(valid_config_data):
    from qdrant_client import QdrantClient

    store = QdrantStore.__new__(QdrantStore)
    store._client = QdrantClient(":memory:")
    store._collection = "careercrew_mm"
    store._dim = 1024
    store._ensure_collection()
    store.upsert([VectorRecord(
        id="doc-p0", dense=[0.1] * 1024,
        metadata={"doc": "doc", "source": "doc.pdf", "category": "knowledge", "user_id": "u_001"},
    )])
    return store


def test_migration_moves_user_id_to_owner_and_is_idempotent(valid_config_data):
    store = _seed_store(valid_config_data)
    before = [p.id for p, _ in store._client.scroll("careercrew_mm", limit=100, with_payload=False)]

    changed, skipped, conflicts = migrate_collection(
        store._client, "careercrew_mm", "u_001", apply=False
    )
    assert changed == 1 and skipped == 0 and conflicts == 0
    assert store.count(filters={"owner_user_id": "u_001"}) == 0  # dry-run 未写

    changed, skipped, conflicts = migrate_collection(
        store._client, "careercrew_mm", "u_001", apply=True
    )
    assert changed == 1 and conflicts == 0
    assert store.count(filters={"owner_user_id": "u_001", "visibility": "private"}) == 1
    assert store.count(filters={"user_id": "u_001"}) == 0  # 旧键已删除

    after = [p.id for p, _ in store._client.scroll("careercrew_mm", limit=100, with_payload=False)]
    assert after == before  # 物理 ID 不变

    changed, skipped, conflicts = migrate_collection(
        store._client, "careercrew_mm", "u_001", apply=True
    )
    assert changed == 0 and skipped == 1 and conflicts == 0  # 幂等
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/unit/test_knowledge_visibility_migration.py -v`
Expected: FAIL（脚本不存在）。

- [ ] **Step 3: 实现脚本**

新建 `scripts/migrate_knowledge_visibility.py`：

```python
"""知识库集合 payload 迁移：user_id → owner_user_id + visibility=private。

物理 ID 不变（_to_qid 只依赖 owner 的值，不依赖键名）。默认 dry-run。
只处理知识库集合；情景记忆集合（careercrew_episodic_v2）继续用 user_id，不受影响。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def migrate_collection(client, collection: str, default_owner: str, *, apply: bool):
    """返回 (changed, skipped, conflicts)。apply 时写 owner_user_id/visibility 并删除 user_id 键。"""
    changed = skipped = conflicts = 0
    offset = None
    while True:
        points, offset = client.scroll(
            collection, limit=256, offset=offset, with_payload=True, with_vectors=False
        )
        for point in points:
            payload = dict(point.payload or {})
            owner = str(payload.get("user_id") or payload.get("owner_user_id") or default_owner)
            if payload.get("owner_user_id") and payload.get("visibility"):
                skipped += 1
                continue
            existing_owner = payload.get("owner_user_id")
            if existing_owner and existing_owner != owner:
                conflicts += 1
                continue
            changed += 1
            if apply:
                client.set_payload(
                    collection,
                    payload={"owner_user_id": owner, "visibility": "private", "user_id": None},
                    points=[point.id],
                )
        if offset is None:
            break
    return changed, skipped, conflicts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default="", help="默认读 settings 的 knowledge 集合")
    parser.add_argument("--default-owner", default="u_001")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(PROJECT_ROOT))
    from qdrant_client import QdrantClient

    from careercrew_core.state.settings import load_settings

    settings = load_settings()
    cfg = settings.vector_store
    collection = args.collection or cfg.collections["knowledge"]
    if (cfg.url or "").strip() == ":memory:":
        raise SystemExit("不能在 :memory: 后端上执行真实迁移")
    client = QdrantClient(url=cfg.url, api_key=cfg.api_key or None)
    if not client.collection_exists(collection):
        raise SystemExit(f"集合不存在：{collection}")
    changed, skipped, conflicts = migrate_collection(
        client, collection, args.default_owner, apply=args.apply
    )
    print(f"mode={'APPLY' if args.apply else 'DRY-RUN'} collection={collection} "
          f"changed={changed} skipped={skipped} conflicts={conflicts}")
    return 1 if conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`migrate_legacy_tenant.py` 的 `migrate_qdrant_client` 中 owner 读取（`payload.get("user_id")` 处）改为 `owner = payload.get("user_id") or payload.get("owner_user_id")`，其余不变（旧迁移工具与新模式双键兼容）。

- [ ] **Step 4: 运行测试**

Run: `pytest tests/unit/test_knowledge_visibility_migration.py tests/unit/test_tenant_migration.py -v`
Expected: PASS。

- [ ] **Step 5: 真实数据迁移**

```powershell
# 1) snapshot（迁移前）
Invoke-RestMethod -Uri http://localhost:6333/collections/careercrew_mm/snapshots -Method Post | ConvertTo-Json
# 2) dry-run
$env:PYTHONPATH=(Get-Location).Path
F:\Python_develop\miniconda3\envs\careercrew\python.exe scripts\migrate_knowledge_visibility.py
# 3) apply（期望 changed=215 skipped=0 conflicts=0）
F:\Python_develop\miniconda3\envs\careercrew\python.exe scripts\migrate_knowledge_visibility.py --apply
# 4) 复跑（期望 changed=0 skipped=215）
F:\Python_develop\miniconda3\envs\careercrew\python.exe scripts\migrate_knowledge_visibility.py
```

验证：`careercrew_mm` 点数仍为 215；抽样点 `owner_user_id=u_001`、`visibility=private`、无 `user_id` 键；`careercrew_episodic_v2` 仍 14 点且 `user_id` 键保留（未触碰）。

- [ ] **Step 6: Commit**

```bash
git add scripts/migrate_knowledge_visibility.py scripts/migrate_legacy_tenant.py tests/unit/test_knowledge_visibility_migration.py
git commit -m "feat(kb): idempotent knowledge payload visibility migration (user_id -> owner_user_id + visibility)"
```

---

## Part C：前端（/admin/users + 知识库可见性）

### Task C1: `/admin/users` 用户管理页面 + 角色守卫 + 导航

**Files:**
- Create: `careercrew_web/src/pages/AdminUsersPage.tsx`
- Create: `careercrew_web/src/pages/AdminUsersPage.test.tsx`
- Modify: `careercrew_web/src/App.tsx`（lazy、PAGES、NAV、守卫）

**Interfaces:**
- Consumes: `apiFetch`、`getAuthSnapshot/subscribeAuth`（`@/lib/auth`）、`ui/button|input|card`、`cn`。
- Produces: `/admin/users` 页面；管理端点调用（GET/POST `/api/auth/users`、PATCH `/api/auth/users/{id}`、POST `/api/auth/users/{id}/reset-password`）；导航项「用户管理」仅 admin 可见；非 admin 访问 `/admin/users` 渲染 ChatPage。

- [ ] **Step 1: 写失败测试**

新建 `careercrew_web/src/pages/AdminUsersPage.test.tsx`：

```tsx
// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

const apiFetchMock = vi.fn()

vi.mock("@/lib/auth", () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
  getAuthSnapshot: () => ({
    status: "authenticated",
    user: { id: "u_001", username: "admin", role: "admin" },
  }),
  subscribeAuth: () => () => {},
}))

import AdminUsersPage from "@/pages/AdminUsersPage"

const ACCOUNTS = {
  items: [
    { id: "u_001", username: "admin", role: "admin", status: "active", token_version: 0, created_at: "2026-08-15T00:00:00Z", updated_at: "2026-08-15T00:00:00Z" },
    { id: "u_abc", username: "member", role: "user", status: "active", token_version: 0, created_at: "2026-08-15T01:00:00Z", updated_at: "2026-08-15T01:00:00Z" },
  ],
  total: 2,
  page: 1,
  page_size: 20,
}

describe("AdminUsersPage", () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    apiFetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ACCOUNTS })
  })

  it("renders account list without password fields", async () => {
    render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByText("member")).toBeTruthy())
    expect(screen.getByText("admin")).toBeTruthy()
    expect(apiFetchMock).toHaveBeenCalledWith("/api/auth/users?page=1&page_size=20")
    expect(screen.queryByText(/password_hash|token/i)).toBeNull()
  })

  it("creates a user through the form", async () => {
    apiFetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/auth/users" && init?.method === "POST") {
        return Promise.resolve({ ok: true, status: 201, json: async () => ({ id: "u_new", username: "newbie", role: "user", status: "active", token_version: 0, created_at: "", updated_at: "" }) })
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ACCOUNTS })
    })
    render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByText("member")).toBeTruthy())
    fireEvent.click(screen.getByText("新建用户"))
    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "newbie" } })
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "long-password-123" } })
    fireEvent.click(screen.getByText("创建"))
    await waitFor(() => {
      const call = apiFetchMock.mock.calls.find(([u, i]) => u === "/api/auth/users" && (i as RequestInit)?.method === "POST")
      expect(call).toBeTruthy()
    })
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run（`careercrew_web` 目录）：`npx vitest run src/pages/AdminUsersPage.test.tsx`
Expected: FAIL（文件不存在）。

- [ ] **Step 3: 实现 AdminUsersPage.tsx（完整文件）**

```tsx
import { useEffect, useState, useSyncExternalStore } from "react"
import { RefreshCw, ShieldCheck, UserPlus } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { apiFetch, getAuthSnapshot, subscribeAuth } from "@/lib/auth"
import { cn } from "@/lib/utils"

interface AccountItem {
  id: string
  username: string
  role: "admin" | "user"
  status: "active" | "disabled"
  token_version: number
  created_at: string
  updated_at: string
}

const ROLE_LABEL: Record<string, string> = { admin: "管理员", user: "普通用户" }
const STATUS_LABEL: Record<string, string> = { active: "正常", disabled: "已禁用" }

export default function AdminUsersPage() {
  const auth = useSyncExternalStore(subscribeAuth, getAuthSnapshot, getAuthSnapshot)
  const [accounts, setAccounts] = useState<AccountItem[]>([])
  const [total, setTotal] = useState(0)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [creating, setCreating] = useState(false)
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [role, setRole] = useState<"user" | "admin">("user")

  const me = auth.user?.id

  const refresh = () => {
    setError("")
    setNotice("")
    apiFetch("/api/auth/users?page=1&page_size=100")
      .then(async (r) => {
        const data = await r.json()
        if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`)
        setAccounts(data.items)
        setTotal(data.total)
      })
      .catch((e) => setError((e as Error).message))
  }

  useEffect(() => { refresh() }, [])

  const createUser = async () => {
    if (!username.trim() || password.length < 12) {
      setError("用户名必填，密码至少 12 个字符")
      return
    }
    setError("")
    const resp = await apiFetch("/api/auth/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username.trim(), password, role }),
    })
    const data = await resp.json()
    if (!resp.ok) { setError(data.detail || `HTTP ${resp.status}`); return }
    setCreating(false)
    setUsername("")
    setPassword("")
    setRole("user")
    setNotice(`已创建账号 ${data.username}`)
    refresh()
  }

  const patch = async (id: string, body: Record<string, string>) => {
    const resp = await apiFetch(`/api/auth/users/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
    const data = await resp.json()
    if (!resp.ok) { setError(data.detail || `HTTP ${resp.status}`); return }
    setNotice(`已更新 ${data.username}`)
    refresh()
  }

  const resetPassword = async (id: string) => {
    const next = window.prompt(`为账号输入新密码（至少 12 个字符）：`)
    if (!next || next.length < 12) { setError("密码至少 12 个字符"); return }
    const resp = await apiFetch(`/api/auth/users/${id}/reset-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: next }),
    })
    if (!resp.ok) { const data = await resp.json().catch(() => ({})); setError(data.detail || `HTTP ${resp.status}`); return }
    setNotice("密码已重置，该用户所有会话已失效")
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-16 shrink-0 items-center justify-between border-b px-6">
        <div>
          <h1 className="font-display text-xl font-semibold">用户管理</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">开户、角色、启用/禁用与重置密码（共 {total} 个账号）</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={refresh}><RefreshCw className="mr-1 h-3.5 w-3.5" />刷新</Button>
          <Button size="sm" onClick={() => setCreating((v) => !v)}><UserPlus className="mr-1 h-3.5 w-3.5" />新建用户</Button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {error && <p className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>}
        {notice && <p className="mb-3 rounded-md border border-green-600/40 bg-green-600/10 px-3 py-2 text-sm text-green-700">{notice}</p>}

        {creating && (
          <Card className="mb-4">
            <CardHeader className="pb-2"><CardTitle className="text-sm font-semibold">新建账号</CardTitle></CardHeader>
            <CardContent className="flex flex-wrap items-end gap-3">
              <label className="text-xs text-muted-foreground">用户名
                <Input aria-label="用户名" value={username} onChange={(e) => setUsername(e.target.value)} className="mt-1 h-9 w-44 text-sm" placeholder="3-64 位字母数字" />
              </label>
              <label className="text-xs text-muted-foreground">密码
                <Input aria-label="密码" type="password" value={password} onChange={(e) => setPassword(e.target.value)} className="mt-1 h-9 w-44 text-sm" placeholder="至少 12 个字符" />
              </label>
              <label className="text-xs text-muted-foreground">角色
                <select value={role} onChange={(e) => setRole(e.target.value as "user" | "admin")} className="mt-1 h-9 w-32 rounded-md border border-border bg-card px-2 text-sm">
                  <option value="user">普通用户</option>
                  <option value="admin">管理员</option>
                </select>
              </label>
              <Button size="sm" onClick={createUser}>创建</Button>
              <Button size="sm" variant="ghost" onClick={() => setCreating(false)}>取消</Button>
            </CardContent>
          </Card>
        )}

        <Card>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="px-4 py-2.5 font-medium">用户名</th>
                  <th className="px-4 py-2.5 font-medium">角色</th>
                  <th className="px-4 py-2.5 font-medium">状态</th>
                  <th className="px-4 py-2.5 font-medium">创建时间</th>
                  <th className="px-4 py-2.5 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((a) => (
                  <tr key={a.id} className="border-b last:border-0">
                    <td className="px-4 py-2.5 font-medium">{a.username}</td>
                    <td className="px-4 py-2.5">
                      <span className={cn("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px]", a.role === "admin" ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground")}>
                        {a.role === "admin" && <ShieldCheck className="h-3 w-3" />}{ROLE_LABEL[a.role]}
                      </span>
                    </td>
                    <td className={cn("px-4 py-2.5", a.status === "disabled" && "text-destructive")}>{STATUS_LABEL[a.status]}</td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground">{a.created_at.slice(0, 10)}</td>
                    <td className="px-4 py-2.5">
                      {a.id === me ? (
                        <span className="text-xs text-muted-foreground">当前账号</span>
                      ) : (
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Button size="sm" variant="outline" className="h-7 px-2 text-xs"
                            onClick={() => patch(a.id, { role: a.role === "admin" ? "user" : "admin" })}>
                            {a.role === "admin" ? "降为普通用户" : "升为管理员"}
                          </Button>
                          <Button size="sm" variant="outline" className="h-7 px-2 text-xs"
                            onClick={() => patch(a.id, { status: a.status === "active" ? "disabled" : "active" })}>
                            {a.status === "active" ? "禁用" : "启用"}
                          </Button>
                          <Button size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => resetPassword(a.id)}>
                            重置密码
                          </Button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {accounts.length === 0 && <p className="px-4 py-8 text-center text-sm text-muted-foreground">暂无账号</p>}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: App.tsx 接线**

```tsx
const AdminUsersPage = lazy(() => import("@/pages/AdminUsersPage"))
```

`NAV` 数组（`:23-31`）加一项并在类型上允许 `adminOnly`：

```tsx
const NAV: { to: string; label: string; icon: ComponentType<{ className?: string }>; end?: boolean; adminOnly?: boolean }[] = [
  { to: "/", label: "求职规划", icon: MessageSquare, end: true },
  { to: "/matcher", label: "职位匹配", icon: Target },
  { to: "/resume", label: "简历优化", icon: FileText },
  { to: "/interview", label: "面试练习", icon: GraduationCap },
  { to: "/consult", label: "会诊", icon: Users },
  { to: "/knowledge", label: "知识库问答", icon: BookOpen },
  { to: "/admin/users", label: "用户管理", icon: UserCog, adminOnly: true },
]
```

渲染处（`:74`）改为：

```tsx
          {NAV.filter((item) => !item.adminOnly || auth.user?.role === "admin").map((item) => (
```

`PAGES` 加 `"/admin/users": AdminUsersPage`；页面渲染处（`:132-135`）改为：

```tsx
          {(() => {
            const requested = PAGES[location.pathname] ?? ChatPage
            const Page =
              location.pathname === "/admin/users" && auth.user?.role !== "admin"
                ? ChatPage
                : requested
            return <Page key={location.pathname} />
          })()}
```

`lucide-react` import 增加 `UserCog`。

- [ ] **Step 5: 运行测试与静态检查**

Run（`careercrew_web`）：`npx vitest run`、`npm run lint`、`npx tsc -b`
Expected: PASS（全部既有 + 新增测试绿；lint/tsc 无错）。

- [ ] **Step 6: Commit**

```bash
git add careercrew_web/src/pages/AdminUsersPage.tsx careercrew_web/src/pages/AdminUsersPage.test.tsx careercrew_web/src/App.tsx
git commit -m "feat(web): admin users page with role guard and sidebar entry"
```

---

### Task C2: 知识库可见性前端（面板/问答 scope/类型/DataPage 修复）

**Files:**
- Modify: `careercrew_web/src/types.ts`（`KnowledgeDoc` 型别、`KB_SCOPE` 常量）
- Modify: `careercrew_web/src/store/threadStore.ts`（`RetrievalScope` 联合扩展）
- Modify: `careercrew_web/src/components/KnowledgePanel.tsx`（徽标/可见性上传/发布/删除权限）
- Modify: `careercrew_web/src/pages/KnowledgePage.tsx`（scope 选择器 + ask 传 scope）
- Modify: `careercrew_web/src/pages/DataPage.tsx`（`:133/:414/:459` 硬编码 `u_001` → 登录用户）
- Test: `careercrew_web/src/components/KnowledgePanel.test.tsx`（新建）

**Interfaces:**
- Consumes: 后端 B3 的新字段/端点（`GET /api/knowledge?scope=`、上传 `visibility` 表单、`/publish`、`/unpublish`）。
- Produces:
  - `RetrievalScope = { type: "all" } | { type: "public" } | { type: "private" } | { type: "category"; category_id: string }`。
  - `KnowledgeDoc = { doc; source; points; category?; visibility: "private" | "public"; owner_user_id: string }`。
  - 面板：公共/我的徽标、admin 发布/下架按钮、删除权限（私有且本人 或 admin）、上传可见性选择（仅 admin 显示「发布到公共库」）。

- [ ] **Step 1: 写失败测试**

新建 `careercrew_web/src/components/KnowledgePanel.test.tsx`：

```tsx
// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

const apiFetchMock = vi.fn()

vi.mock("@/lib/auth", () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
  getAuthSnapshot: () => ({
    status: "authenticated",
    user: { id: "u_001", username: "admin", role: "admin" },
  }),
  subscribeAuth: () => () => {},
}))

import KnowledgePanel from "@/components/KnowledgePanel"

const STATUS = {
  points: 4,
  docs: [
    { doc: "mine.pdf", source: "mine.pdf", points: 2, category: "knowledge", visibility: "private", owner_user_id: "u_001" },
    { doc: "public.pdf", source: "public.pdf", points: 2, category: "knowledge", visibility: "public", owner_user_id: "u_001" },
  ],
}

describe("KnowledgePanel visibility", () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    apiFetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => STATUS })
  })

  it("shows public badge and admin publish controls", async () => {
    render(<KnowledgePanel />)
    await waitFor(() => expect(screen.getByText("mine.pdf")).toBeTruthy())
    expect(screen.getByText("公共")).toBeTruthy()
    expect(screen.getByText("我的")).toBeTruthy()
    expect(screen.getByText("发布到公共库")).toBeTruthy()
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/KnowledgePanel.test.tsx`
Expected: FAIL（文件不存在）。

- [ ] **Step 3: 实现类型与 store**

`types.ts` 追加：

```ts
export const KB_SCOPE = [
  { id: "all", label: "全部" },
  { id: "public", label: "公共库" },
  { id: "private", label: "个人库" },
] as const
```

`threadStore.ts` 的 `RetrievalScope`（`:7`）替换为：

```ts
/** 会话检索范围（与后端 RetrievalScopeRequest 对齐；历史会话无该字段时为 null → "全部"）。 */
export type RetrievalScope =
  | { type: "all" }
  | { type: "public" }
  | { type: "private" }
  | { type: "category"; category_id: string }
```

- [ ] **Step 4: KnowledgePanel.tsx 改造**

顶部 import 增加 `useSyncExternalStore` 与 auth 快照：

```tsx
import { useEffect, useState, useSyncExternalStore } from "react"
import { Upload, BookOpen, Globe, Trash2, X } from "lucide-react"
import { apiFetch, getAuthSnapshot, subscribeAuth } from "@/lib/auth"
```

`KnowledgeDoc` 接口扩展：

```tsx
interface KnowledgeDoc {
  doc: string
  source: string
  points: number
  category?: string
  visibility: "private" | "public"
  owner_user_id: string
}
```

组件内：

```tsx
  const auth = useSyncExternalStore(subscribeAuth, getAuthSnapshot, getAuthSnapshot)
  const me = auth.user?.id ?? ""
  const isAdmin = auth.user?.role === "admin"
  const [uploadVisibility, setUploadVisibility] = useState<"private" | "public">("private")
```

`handleUpload` 的 FormData 增加 `fd.append("visibility", uploadVisibility)`。

`handleDelete` 增加 403 处理：

```tsx
  const handleDelete = async (doc: KnowledgeDoc) => {
    if (!window.confirm(`确定从知识库删除「${doc.doc}」吗？删除后需重新上传才能恢复。`)) return
    const resp = await apiFetch(`/api/knowledge/${encodeURIComponent(doc.doc)}`, { method: "DELETE" })
    if (resp.status === 403) {
      setError("只有管理员可以删除公共知识库文档")
      return
    }
    if (!resp.ok) setError(`删除失败：HTTP ${resp.status}`)
    refresh()
  }

  const togglePublish = async (doc: KnowledgeDoc) => {
    const action = doc.visibility === "public" ? "unpublish" : "publish"
    const resp = await apiFetch(`/api/knowledge/${encodeURIComponent(doc.doc)}/${action}`, { method: "POST" })
    if (!resp.ok) { const data = await resp.json().catch(() => ({})); setError(data.detail || `操作失败：HTTP ${resp.status}`); return }
    refresh()
  }
```

上传表单区（`uploadCategory` 选择器下方）加 admin 可见性开关：

```tsx
          {isAdmin && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">可见性</span>
              <button
                onClick={() => setUploadVisibility((v) => (v === "private" ? "public" : "private"))}
                className={cn(
                  "rounded-full border px-2 py-0.5 text-[11px] font-medium transition-all",
                  uploadVisibility === "public" ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card hover:bg-muted"
                )}
              >
                {uploadVisibility === "public" ? "发布到公共库" : "我的私有库"}
              </button>
            </div>
          )}
```

文档列表项改造：key 改 `doc.doc + doc.visibility`；徽标：

```tsx
                      <span className={cn(
                        "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium",
                        doc.visibility === "public" ? "bg-amber-500/15 text-amber-600" : "bg-primary/10 text-primary"
                      )}>
                        {doc.visibility === "public" ? "公共" : "我的"}
                      </span>
```

操作区替换为：

```tsx
                  <div className="flex shrink-0 items-center gap-1">
                    {isAdmin && (
                      <button
                        className="flex items-center gap-0.5 rounded p-1 text-[11px] text-muted-foreground transition-colors hover:text-primary"
                        onClick={() => togglePublish(doc)}
                        title={doc.visibility === "public" ? "下架公共文档" : "发布到公共库"}
                      >
                        <Globe className="h-3.5 w-3.5" />
                      </button>
                    )}
                    {(doc.visibility === "private" ? doc.owner_user_id === me : isAdmin) && (
                      <button
                        className="shrink-0 text-muted-foreground transition-colors hover:text-destructive"
                        onClick={() => handleDelete(doc)}
                        title={`删除 ${doc.doc}`}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
```

（列表渲染处 `status.docs.map((doc) => ...)` 内相应字段替换；`doc` 类型为扩展后的 `KnowledgeDoc`。）

- [ ] **Step 5: KnowledgePage.tsx scope 选择器 + ask 传 scope**

`changeCategory` 附近加 scope 状态派生与切换：

```tsx
  const scope = savedScope?.type === "public" || savedScope?.type === "private" ? savedScope.type : "all"
  const changeScope = (next: "all" | "public" | "private") => {
    void setThreadScope("knowledge", currentThreadId, { type: next })
  }
```

分类选择器行（现有 `KB_CATEGORIES` chips，`:…` 位置）之前插入 scope chips：

```tsx
            {KB_SCOPE.map((s) => (
              <button
                key={s.id}
                onClick={() => changeScope(s.id)}
                className={cn(
                  "shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium transition-all",
                  scope === s.id ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card hover:bg-muted"
                )}
              >
                {s.label}
              </button>
            ))}
```

（若原分类 chips 在 scope 之后渲染，保持视觉顺序：scope chips 在前、分类 chips 在后；实现者按文件内实际 JSX 位置插入。）

`handleAsk` 的 startStream body 加 `scope`：

```tsx
    await startStream(currentThreadId, "/knowledge/ask", { question, thread_id: currentThreadId, category, scope })
```

import `KB_SCOPE` 自 `@/types`。

- [ ] **Step 6: DataPage.tsx 硬编码修复**

三处 `"u_001"`（`:133/:414/:459`）改为 `getAuthSnapshot().user?.id ?? "u_001"`；文件顶部 `import { apiFetch, getAuthSnapshot } from "@/lib/auth"`（若已 import apiFetch 则合并）。

- [ ] **Step 7: 运行测试与静态检查**

Run（`careercrew_web`）：`npx vitest run`、`npm run lint`、`npx tsc -b`、`npm run build`
Expected: PASS。

- [ ] **Step 8: Commit**

```bash
git add careercrew_web/src/types.ts careercrew_web/src/store/threadStore.ts careercrew_web/src/components/KnowledgePanel.tsx careercrew_web/src/components/KnowledgePanel.test.tsx careercrew_web/src/pages/KnowledgePage.tsx careercrew_web/src/pages/DataPage.tsx
git commit -m "feat(web): knowledge visibility UI (scope selector, badges, publish controls) and per-user data page"
```

---

## Part D：清理、运维与全量验收

### Task D1: fetch_kb / ingest_knowledge 改造 + data/knowledge 归档 + 备份文档

**Files:**
- Modify: `scripts/fetch_kb.py`（完整重写）
- Modify: `scripts/ingest_knowledge.py`（加 `--user-id`/`--visibility`）
- Create: `docs/OPS_BACKUP.md`
- 数据操作：`data/knowledge` → `data/archived/knowledge_legacy_20260815`

**Interfaces:**
- Consumes: `careercrew_api.storage`（`DATA_ROOT`/`L`/`resolve_under`）、`MultimodalIngestionPipeline`。
- Produces:
  - `fetch_kb.py` CLI：`--user-id`（默认 `u_001`）、`--visibility`（默认 `private`）、`--no-ingest`；输出到 `data/uploads/knowledge_raw/{user_id}/{uuid}.md` 并经标准管线入库（metadata 含 `owner_user_id/visibility`）。
  - `ingest_knowledge.py` CLI：新增同义参数并 `metadata={"owner_user_id": ..., "visibility": ...}`。

- [ ] **Step 1: 重写 fetch_kb.py（完整文件）**

```python
"""从 Exa 语义搜索抓取大模型/Agent/RAG 语料，经标准知识库管线入库。

输出：data/uploads/knowledge_raw/{user_id}/{uuid}.md（不再写 data/knowledge）。
入库 metadata：owner_user_id + visibility（private 仅本人可见；public 面向全部用户）。

Exa key 从 ~/.mcporter/mcporter.json 读（不硬编码）。
跑法：$env:PYTHONPATH=(Get-Location).Path; python scripts/fetch_kb.py [--visibility public] [--no-ingest]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse as up
from pathlib import Path
from uuid import uuid4

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def get_exa_key() -> str:
    p = os.path.expanduser("~/.mcporter/mcporter.json")
    data = json.load(open(p, encoding="utf-8"))
    url = data["mcpServers"]["exa"]["baseUrl"]  # https://mcp.exa.ai/mcp?exaApiKey=KEY
    return up.parse_qs(up.urlparse(url).query)["exaApiKey"][0]


def exa_search(key: str, query: str, num: int = 4, max_chars: int = 4000) -> list[dict]:
    resp = requests.post(
        "https://api.exa.ai/search",
        headers={"x-api-key": key, "Content-Type": "application/json"},
        json={"query": query, "numResults": num, "contents": {"text": {"maxCharacters": max_chars}}},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def save_topic(key: str, filename: str, query: str, num: int = 4) -> tuple[int, int]:
    results = exa_search(key, query, num=num)
    sys.path.insert(0, str(PROJECT_ROOT))
    from careercrew_api import storage

    target = storage.resolve_under(
        storage.L.knowledge_raw, "u_001", f"{uuid4().hex[:12]}.md"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {filename.replace('.md', '')} 知识库（Exa 搜索聚合）\n"]
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "").strip()
        url = r.get("url") or ""
        text = (r.get("text") or "").strip()
        lines.append(f"\n## [{i}] {title}\n")
        lines.append(f"来源: {url}\n")
        lines.append(f"\n{text}\n")
    target.write_text("\n".join(lines), encoding="utf-8")
    return len(results), sum(len(r.get("text") or "") for r in results)


def ingest_markdown(path: Path, user_id: str, visibility: str) -> int:
    from careercrew_core.rag.pipeline_multimodal import MultimodalIngestionPipeline
    from careercrew_core.state.settings import load_settings

    settings = load_settings()
    from careercrew_ai.embedding import create_embedding
    from careercrew_ai.vector_store import create_vector_store

    pipeline = MultimodalIngestionPipeline(
        create_embedding(settings), create_vector_store(settings),
        contextual=False, output_dir=settings.rag.loaders.output_dir,
        loader_provider=settings.rag.loaders.provider,
        loader_api_key=settings.rag.loaders.api_key,
        loader_device=settings.rag.loaders.device,
        loader_method=settings.rag.loaders.method,
        loader_formula=settings.rag.loaders.formula,
        loader_table=settings.rag.loaders.table,
        loader_language=settings.rag.loaders.language,
        loader_model_version=settings.rag.loaders.model_version,
        loader_poll_interval=settings.rag.loaders.poll_interval,
        loader_timeout=settings.rag.loaders.timeout,
        chunk_size=settings.rag.chunking.chunk_size,
        chunk_overlap=settings.rag.chunking.chunk_overlap,
    )
    from careercrew_core.rag.categories import category_for_doc

    return pipeline.ingest_file(
        path,
        metadata={"owner_user_id": user_id, "visibility": visibility},
        category=category_for_doc(path.name),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", default="u_001")
    parser.add_argument("--visibility", default="private", choices=["private", "public"])
    parser.add_argument("--no-ingest", action="store_true", help="只抓取落盘，不入库")
    args = parser.parse_args(argv)

    key = get_exa_key()
    topics = [
        ("exa_rag_interview.md", "大模型 RAG 检索增强生成 面试题 八股 向量检索 rerank 混合检索"),
        ("exa_interview_experience.md", "大模型算法岗 面经 字节 阿里 美团 面试经历 大厂"),
        ("exa_career_planning.md", "大模型应用 求职职业规划 方向选择 学习路线 Agent 工程师 成长路径"),
    ]
    total_results = total_chars = 0
    sys.path.insert(0, str(PROJECT_ROOT))
    from careercrew_api import storage

    for fname, q in topics:
        target = storage.resolve_under(
            storage.L.knowledge_raw, args.user_id, f"{uuid4().hex[:12]}.md"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        results = exa_search(key, q, num=4)
        lines = [f"# {fname.replace('.md', '')} 知识库（Exa 搜索聚合）\n"]
        for i, r in enumerate(results, 1):
            title = (r.get("title") or "").strip()
            url = r.get("url") or ""
            text = (r.get("text") or "").strip()
            lines.append(f"\n## [{i}] {title}\n")
            lines.append(f"来源: {url}\n")
            lines.append(f"\n{text}\n")
        target.write_text("\n".join(lines), encoding="utf-8")
        n, chars = len(results), sum(len(r.get("text") or "") for r in results)
        print(f"  {fname}: {n} 条结果, {chars} 字符 -> {target}")
        total_results += n
        total_chars += chars
        if not args.no_ingest:
            points = ingest_markdown(target, args.user_id, args.visibility)
            print(f"    入库 {points} 点（visibility={args.visibility}）")
    print(f"\n总计: {total_results} 条结果, {total_chars} 字符（user={args.user_id}, visibility={args.visibility}）")


if __name__ == "__main__":
    main()
```

（注：`save_topic` 旧函数被内联进 main；若实现者发现脚本中 `save_topic` 被其它脚本 import，保留兼容别名 `save_topic = None  # removed; see main()` 并搜索确认无引用。）

- [ ] **Step 2: ingest_knowledge.py 加参数**

读 `scripts/ingest_knowledge.py`（90 行）。加 argparse：

```python
    parser.add_argument("--user-id", default="u_001")
    parser.add_argument("--visibility", default="private", choices=["private", "public"])
```

`pipe.ingest_file(f)` 调用改为：

```python
        pipe.ingest_file(
            f,
            metadata={"owner_user_id": args.user_id, "visibility": args.visibility},
        )
```

（实现者按文件内实际变量名/循环结构落地；其余逻辑不动。）

- [ ] **Step 3: 归档 data/knowledge**

```powershell
# 校验：原件已存在于 data/uploads/langchain_v1_tools.md 且向量已迁移（前面 B4 已确认）
Compare-Object (Get-Content data\knowledge\langchain_v1_tools.md) (Get-Content data\uploads\langchain_v1_tools.md)
New-Item -ItemType Directory -Force data\archived | Out-Null
Move-Item data\knowledge data\archived\knowledge_legacy_20260815
Get-ChildItem data\archived\knowledge_legacy_20260815
```

（若 Compare-Object 有差异：仅归档仍执行，但把差异文件先复制到 `data/archived/knowledge_legacy_20260815/` 并在 commit message 注明；向量归属已验证过。）

- [ ] **Step 4: 写 docs/OPS_BACKUP.md（完整文件）**

```markdown
# CareerCrew 备份与恢复手册

## 1. 组件与位置

| 组件 | 位置 | 备份方式 |
|---|---|---|
| 账号/刷新会话/审计/限速 | Postgres `auth_accounts` / `auth_refresh_sessions` / `admin_audit_events` / `auth_login_attempts` | `pg_dump` |
| 会话/记忆/画像/线程 | Postgres（`DATABASE_URL` 指向库）其余表 | `pg_dump` |
| 知识库向量 `careercrew_mm` / 情景记忆 `careercrew_episodic_v2` | Qdrant（`http://localhost:6333`） | collection snapshot |
| 上传原件/解析产物/简历 | `data/`（uploads/parsed） | 文件复制/压缩 |

## 2. 备份命令（Windows PowerShell）

```powershell
# Postgres（含账号与记忆）
$env:PGPASSWORD="careercrew"
pg_dump -h localhost -U careercrew -d careercrew -Fc -f "backup\careercrew_$((Get-Date -Format yyyyMMdd-HHmmss)).dump"

# Qdrant snapshot
Invoke-RestMethod -Uri http://localhost:6333/collections/careercrew_mm/snapshots -Method Post | ConvertTo-Json
Invoke-RestMethod -Uri http://localhost:6333/collections/careercrew_episodic_v2/snapshots -Method Post | ConvertTo-Json
# snapshot 文件默认在 Qdrant 数据目录 snapshots/ 子目录，恢复前将其拷贝备份

# 文件
Compress-Archive -Path data\uploads,data\parsed -DestinationPath "backup\data_$((Get-Date -Format yyyyMMdd-HHmmss)).zip"
```

## 3. 恢复

1. 停应用 → `pg_restore -h localhost -U careercrew -d careercrew --clean backup\careercrew_xxx.dump`
2. Qdrant：新建同名 collection 后 `POST /collections/{name}/snapshots/recover`（body: `{"location": "<snapshot文件URL或路径>"}`），或把 snapshot 文件放回 snapshots 目录用控制台恢复。
3. 解压 `data` zip 到仓库根（保持 uploads/parsed 目录结构）。
4. 启动应用；管理员登录确认用户数与知识库点数与备份时一致。

## 4. 例行事项

- 过期刷新会话由应用内置清理任务自动删除（周期 `auth.cleanup_interval_hours`）。
- 账号迁移：SQLite 仅测试用；运行时以 `auth.backend=postgres` + `AUTH_DATABASE_URL`（回退 `DATABASE_URL`）为准。
- 迁移类脚本均有 dry-run 默认值：`scripts/migrate_accounts_postgres.py`、`scripts/migrate_knowledge_visibility.py`、`scripts/migrate_legacy_tenant.py`、`scripts/migrate_uploads.py`。
```

- [ ] **Step 5: 运行相关测试**

Run: `pytest tests/unit/test_smoke_imports.py tests/unit/test_ingestion_pipeline.py -v`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add scripts/fetch_kb.py scripts/ingest_knowledge.py docs/OPS_BACKUP.md
git commit -m "chore: rework fetch_kb to user-isolated ingestion, add ingest args, archive legacy data/knowledge, add backup runbook"
```

---

### Task D2: 全量验收

**Files:** 无新增（如有修复，走最小改动 + 对应测试文件）。

- [ ] **Step 1: 后端全量测试**

```powershell
$env:PYTHONPATH=(Get-Location).Path
F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit tests/api -q
```

Expected: PASS（无失败；`tests/api` 全部用 fake 后端，不需要外部服务）。

- [ ] **Step 2: Postgres 相关集成测试（本机）**

```powershell
$env:POSTGRES_TEST_DSN="postgresql://careercrew:careercrew@localhost:5432/careercrew"
F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/integration/test_postgres_account_store.py tests/integration/test_account_migration_postgres.py tests/integration/test_postgres_memory_db.py -q
```

Expected: PASS。

- [ ] **Step 3: 前端全量验证**

```powershell
Set-Location careercrew_web
npm run test; npm run lint; npm run build
```

Expected: PASS（vitest 全绿、oxlint 无错、`tsc -b && vite build` 成功）。

- [ ] **Step 4: 数据侧验收核对**

```powershell
# 账号：Postgres 已迁移、SQLite 已归档
F:\Python_develop\miniconda3\envs\careercrew\python.exe scripts\migrate_accounts_postgres.py   # 期望 conflicts=0，to_insert=0 或全 skip
Get-ChildItem data\db
# 知识库：215 点已 owner_user_id/visibility，复跑 0 变更
F:\Python_develop\miniconda3\envs\careercrew\python.exe scripts\migrate_knowledge_visibility.py  # 期望 changed=0 skipped=215
# data/knowledge 已归档
Test-Path data\knowledge  # False；Test-Path data\archived\knowledge_legacy_20260815  # True
```

- [ ] **Step 5: 实时 API 冒烟（TestClient + Postgres 认证）**

临时脚本 `scripts/_smoke_postgres_auth.py`（跑完删除）：

```python
"""冒烟：Postgres 认证服务可登录 u_001（密码由用户输入校验哈希一致性即可）。"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from careercrew_api.auth.service import AuthService
from careercrew_api.auth.store import create_account_store
from careercrew_core.state.settings import load_auth_settings

settings = load_auth_settings()
assert settings.backend == "postgres", settings.backend
service = AuthService(settings, create_account_store(settings))
password = getpass.getpass("u_001 password: ")
payload, _ = service.login("liyou", password)
print("login ok:", payload["user"])
```

（用户本人在场时运行验证「原管理员密码仍可登录」；若不在场，以 Step 4 的哈希一致性核对为准并在交付说明中注明。）

- [ ] **Step 6: git diff --check 与最终 commit（如有改动）**

```powershell
git diff --check
git status --short
```

- [ ] **Step 7: 汇总交付说明**

输出：改动文件清单、数据迁移结果（accounts/knowledge/归档）、测试结果、剩余手工事项（重启后端并重新登录验证知识库可见；`fetch_kb.py` 生产使用说明）。
