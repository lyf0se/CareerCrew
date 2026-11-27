"""本地账号、访问 JWT 与刷新 Cookie API 回归测试。"""
from __future__ import annotations

import pytest

PASSWORD = "correct-horse-battery-staple"
USER_PASSWORD = "member-password-123"  # 满足新密码策略（字母+数字）


@pytest.fixture
def auth_client():
    from fastapi.testclient import TestClient

    from careercrew_api.auth.dependencies import get_auth_service
    from careercrew_api.auth.service import AuthService
    from careercrew_api.main import create_app
    from careercrew_core.state.settings import AuthSettings
    from tests.fakes import FakeAccountStore

    settings = AuthSettings(
        environment="test",
        jwt_secret="test-signing-secret-that-is-long-enough-for-repeatable-api-tests",
    )
    service = AuthService(settings, FakeAccountStore())
    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: service
    with TestClient(app) as client:
        yield client


def _bootstrap(client, username: str = "admin") -> dict:
    response = client.post("/api/auth/bootstrap", json={"username": username, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.web
def test_bootstrap_status_only_reports_whether_first_admin_can_be_created(auth_client):
    available = auth_client.get("/api/auth/bootstrap")
    assert available.status_code == 200
    assert available.json() == {"available": True}

    _bootstrap(auth_client)

    unavailable = auth_client.get("/api/auth/bootstrap")
    assert unavailable.status_code == 200
    assert unavailable.json() == {"available": False}


@pytest.mark.web
def test_password_login_protects_me_and_never_returns_refresh_token(auth_client):
    user = _bootstrap(auth_client)
    assert user == {"id": "u_001", "username": "admin", "role": "admin", "must_change_password": False, "display_name": None}

    unauthorized = auth_client.get("/api/auth/me")
    assert unauthorized.status_code == 401

    login = auth_client.post("/api/auth/token", json={"username": "admin", "password": PASSWORD})
    assert login.status_code == 200
    payload = login.json()
    assert payload["token_type"] == "bearer"
    assert payload["user"] == user
    assert "refresh" not in payload
    assert "password" not in payload
    assert "httponly" in login.headers["set-cookie"].lower()
    assert "samesite=lax" in login.headers["set-cookie"].lower()

    me = auth_client.get("/api/auth/me", headers={"Authorization": f"Bearer {payload['access_token']}"})
    assert me.status_code == 200
    assert me.json() == user


@pytest.mark.web
def test_refresh_cookie_rotates_and_logout_invalidates_session(auth_client):
    _bootstrap(auth_client)
    auth_client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    old_cookie = auth_client.cookies.get("careercrew_refresh")
    assert old_cookie

    refresh = auth_client.post(
        "/api/auth/refresh", headers={"Cookie": f"careercrew_refresh={old_cookie}"}
    )
    assert refresh.status_code == 200
    new_cookie = auth_client.cookies.get("careercrew_refresh")
    assert new_cookie and new_cookie != old_cookie

    # 重放旧值：轮换后旧令牌必须已被服务端撤销。
    # 每个请求显式携带 Cookie 头，而不是把值写回测试客户端的 cookie jar：
    # httpx2 的 IndexedCookieJar 按 domain 索引发送同名条目，顺序与 httpx
    # 相反，jar 中后写入的"旧值"会被随后的"新值"覆盖，导致重放意图失真。
    # 显式 Cookie 头在两套客户端下都不触发 jar 合并，行为一致。
    replay = auth_client.post(
        "/api/auth/refresh", headers={"Cookie": f"careercrew_refresh={old_cookie}"}
    )
    assert replay.status_code == 401

    # 恢复新 cookie 后验证 logout 确实撤销服务端会话，而非仅删除浏览器值。
    logout = auth_client.post(
        "/api/auth/logout", headers={"Cookie": f"careercrew_refresh={new_cookie}"}
    )
    assert logout.status_code == 204
    assert auth_client.post(
        "/api/auth/refresh", headers={"Cookie": f"careercrew_refresh={new_cookie}"}
    ).status_code == 401


@pytest.mark.web
def test_only_administrator_can_create_accounts(auth_client):
    _bootstrap(auth_client)
    admin_login = auth_client.post("/api/auth/token", json={"username": "admin", "password": PASSWORD}).json()
    admin_headers = {"Authorization": f"Bearer {admin_login['access_token']}"}

    created = auth_client.post(
        "/api/auth/users",
        json={"username": "member", "password": USER_PASSWORD, "role": "user"},
        headers=admin_headers,
    )
    assert created.status_code == 201
    member = created.json()
    assert member["role"] == "user"
    assert member["id"] != "u_001"
    assert "password" not in member
    # 管理员自定义密码开户：视为已交付，不强制首登改密
    assert member["must_change_password"] is False

    member_login = auth_client.post("/api/auth/token", json={"username": "member", "password": USER_PASSWORD}).json()
    member_headers = {"Authorization": f"Bearer {member_login['access_token']}"}
    forbidden = auth_client.post(
        "/api/auth/users", json={"username": "other", "password": USER_PASSWORD}, headers=member_headers
    )
    assert forbidden.status_code == 403


@pytest.mark.web
def test_production_startup_requires_explicit_auth_secret(tmp_path, monkeypatch):
    from careercrew_api.main import create_app
    from careercrew_core.state import settings as settings_module
    from careercrew_core.state.settings import SettingsError

    config = tmp_path / "settings.yaml"
    config.write_text(
        "auth:\n  environment: production\n"
        "  database_url: 'postgresql://careercrew:careercrew@localhost:5432/careercrew'\n"
        "  jwt_secret: ''\n  cookie_secure: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config)
    monkeypatch.setattr(settings_module, "load_dotenv", lambda *a, **k: None)
    with pytest.raises(SettingsError, match="auth.jwt_secret"):
        create_app()


@pytest.mark.web
def test_admin_lists_patches_and_disables_users(auth_client):
    _bootstrap(auth_client)
    admin_headers = {"Authorization": f"Bearer {auth_client.post('/api/auth/token', json={'username': 'admin', 'password': PASSWORD}).json()['access_token']}"}

    created = auth_client.post(
        "/api/auth/users", json={"username": "member", "password": USER_PASSWORD}, headers=admin_headers
    )
    assert created.status_code == 201
    member_id = created.json()["id"]

    listed = auth_client.get("/api/auth/users", headers=admin_headers)
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 2
    assert {u["username"] for u in body["items"]} == {"admin", "member"}

    member_token = auth_client.post("/api/auth/token", json={"username": "member", "password": USER_PASSWORD}).json()["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}

    patched = auth_client.patch(
        f"/api/auth/users/{member_id}", json={"status": "disabled"}, headers=admin_headers
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "disabled"
    # 禁用立即生效：旧 access token 失效、登录被拒（明确提示账号已锁定）
    assert auth_client.get("/api/auth/me", headers=member_headers).status_code == 401
    blocked = auth_client.post("/api/auth/token", json={"username": "member", "password": USER_PASSWORD})
    assert blocked.status_code == 403
    assert "账号已被锁定" in blocked.json()["detail"]

    reenabled = auth_client.patch(
        f"/api/auth/users/{member_id}", json={"status": "active"}, headers=admin_headers
    )
    assert reenabled.status_code == 200 and reenabled.json()["status"] == "active"


@pytest.mark.web
def test_admin_self_and_last_admin_guards(auth_client):
    _bootstrap(auth_client)
    admin_token = auth_client.post("/api/auth/token", json={"username": "admin", "password": PASSWORD}).json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    # 唯一 admin 自改 → 系统级不变量 409（禁止失去最后管理员）
    assert auth_client.patch("/api/auth/users/u_001", json={"status": "disabled"}, headers=admin_headers).status_code == 409
    assert auth_client.patch("/api/auth/users/u_001", json={"role": "user"}, headers=admin_headers).status_code == 409
    assert auth_client.patch("/api/auth/users/not-exist", json={"status": "disabled"}, headers=admin_headers).status_code == 404
    # 有第二个 admin 后，第二个 admin 自改 → 403（SelfAdmin）
    assert auth_client.post("/api/auth/users", json={"username": "second", "password": USER_PASSWORD, "role": "admin"}, headers=admin_headers).status_code == 201
    second_id = auth_client.get("/api/auth/users", headers=admin_headers).json()["items"][1]["id"]
    # 自定义密码开户不强制改密：second 直接登录即可调用管理端点
    second_token = auth_client.post("/api/auth/token", json={"username": "second", "password": USER_PASSWORD}).json()["access_token"]
    second_headers = {"Authorization": f"Bearer {second_token}"}
    assert auth_client.patch(f"/api/auth/users/{second_id}", json={"status": "disabled"}, headers=second_headers).status_code == 403


@pytest.mark.web
def test_reset_password_and_change_own_password(auth_client):
    _bootstrap(auth_client)
    admin_headers = {"Authorization": f"Bearer {auth_client.post('/api/auth/token', json={'username': 'admin', 'password': PASSWORD}).json()['access_token']}"}
    member_id = auth_client.post("/api/auth/users", json={"username": "member", "password": USER_PASSWORD}, headers=admin_headers).json()["id"]

    reset = auth_client.post(
        f"/api/auth/users/{member_id}/reset-password",
        json={"password": "another-password-456"}, headers=admin_headers,
    )
    assert reset.status_code == 200 and reset.json() == {"ok": True}
    assert auth_client.post("/api/auth/token", json={"username": "member", "password": USER_PASSWORD}).status_code == 401
    member_login = auth_client.post(
        "/api/auth/token", json={"username": "member", "password": "another-password-456"}
    )
    assert member_login.status_code == 200
    assert member_login.json()["user"]["must_change_password"] is False
    member_token = member_login.json()["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}
    # 自定义重置密码不触发强制改密门禁，业务 API 可直接访问。
    assert auth_client.get("/api/knowledge/upload/no-such-job", headers=member_headers).status_code == 404

    change = auth_client.post(
        "/api/auth/password",
        json={"old_password": "another-password-456", "new_password": "third-password-789"},
        headers=member_headers,
    )
    assert change.status_code == 200 and change.json() == {"ok": True}
    # 改密后旧 access token 已按 token_version 失效，需重新登录
    assert auth_client.get("/api/auth/users", headers=member_headers).status_code == 401
    fresh_token = auth_client.post("/api/auth/token", json={"username": "member", "password": "third-password-789"}).json()["access_token"]
    fresh_headers = {"Authorization": f"Bearer {fresh_token}"}
    # 普通用户不能调用管理端点
    assert auth_client.get("/api/auth/users", headers=fresh_headers).status_code == 403


@pytest.mark.web
def test_reset_to_default_password_still_requires_change(auth_client):
    _bootstrap(auth_client)
    admin_headers = {"Authorization": f"Bearer {auth_client.post('/api/auth/token', json={'username': 'admin', 'password': PASSWORD}).json()['access_token']}"}
    member_id = auth_client.post(
        "/api/auth/users",
        json={"username": "default-reset", "password": USER_PASSWORD},
        headers=admin_headers,
    ).json()["id"]

    reset = auth_client.post(
        f"/api/auth/users/{member_id}/reset-password",
        json={"password": None},
        headers=admin_headers,
    )
    assert reset.status_code == 200 and reset.json() == {"ok": True}
    assert auth_client.post(
        "/api/auth/token", json={"username": "default-reset", "password": USER_PASSWORD}
    ).status_code == 401

    login = auth_client.post(
        "/api/auth/token", json={"username": "default-reset", "password": "123456"}
    )
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert auth_client.get("/api/knowledge/upload/no-such-job", headers=headers).status_code == 403


@pytest.mark.web
def test_new_user_default_password_forced_change_blocks_business_api(auth_client):
    _bootstrap(auth_client)
    admin_headers = {"Authorization": f"Bearer {auth_client.post('/api/auth/token', json={'username': 'admin', 'password': PASSWORD}).json()['access_token']}"}
    created = auth_client.post("/api/auth/users", json={"username": "newbie"}, headers=admin_headers)
    assert created.status_code == 201
    assert created.json()["must_change_password"] is True
    login = auth_client.post("/api/auth/token", json={"username": "newbie", "password": "123456"})
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    # 业务 API 被 403 拦截；me/password 可用（用不依赖 runtime 的业务端点验证门禁）
    assert auth_client.get("/api/knowledge/upload/no-such-job", headers=headers).status_code == 403
    assert auth_client.get("/api/auth/me", headers=headers).status_code == 200
    change = auth_client.post("/api/auth/password", json={"new_password": USER_PASSWORD}, headers=headers)
    assert change.status_code == 200
    fresh = auth_client.post("/api/auth/token", json={"username": "newbie", "password": USER_PASSWORD}).json()
    assert fresh["user"]["must_change_password"] is False
    fresh_headers = {"Authorization": f"Bearer {fresh['access_token']}"}
    assert auth_client.get("/api/knowledge/upload/no-such-job", headers=fresh_headers).status_code == 404


@pytest.mark.web
def test_new_user_custom_password_can_login_without_forced_change(auth_client):
    _bootstrap(auth_client)
    admin_headers = {"Authorization": f"Bearer {auth_client.post('/api/auth/token', json={'username': 'admin', 'password': PASSWORD}).json()['access_token']}"}
    created = auth_client.post(
        "/api/auth/users", json={"username": "direct", "password": USER_PASSWORD}, headers=admin_headers
    )
    assert created.status_code == 201
    assert created.json()["must_change_password"] is False
    login = auth_client.post("/api/auth/token", json={"username": "direct", "password": USER_PASSWORD})
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is False
    # 无强制改密标记：业务 API 直接放行（不依赖 runtime 的端点返回 404 而非 403）
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert auth_client.get("/api/knowledge/upload/no-such-job", headers=headers).status_code == 404


@pytest.mark.web
def test_refresh_rejects_untrusted_origin(auth_client):
    _bootstrap(auth_client)
    auth_client.post("/api/auth/token", json={"username": "admin", "password": PASSWORD})
    evil = auth_client.post("/api/auth/refresh", headers={"Origin": "http://evil.example"})
    assert evil.status_code == 403
    trusted = auth_client.post("/api/auth/refresh", headers={"Origin": "http://localhost:5175"})
    assert trusted.status_code == 200


@pytest.mark.web
def test_admin_assigns_quality_reviewer_and_dependency_gates(auth_client):
    from careercrew_api.auth.dependencies import require_quality_reviewer

    _bootstrap(auth_client)
    admin_headers = {"Authorization": f"Bearer {auth_client.post('/api/auth/token', json={'username': 'admin', 'password': PASSWORD}).json()['access_token']}"}

    # 普通 user
    member = auth_client.post(
        "/api/auth/users", json={"username": "member", "password": USER_PASSWORD}, headers=admin_headers
    )
    assert member.status_code == 201
    member_id = member.json()["id"]

    # admin 将其设为 quality_reviewer（写审计 + token_version 递增沿用 update_user）
    patched = auth_client.patch(
        f"/api/auth/users/{member_id}", json={"role": "quality_reviewer"}, headers=admin_headers
    )
    assert patched.status_code == 200
    assert patched.json()["role"] == "quality_reviewer"

    # 该用户登录后，access token role claim == quality_reviewer
    login = auth_client.post("/api/auth/token", json={"username": "member", "password": USER_PASSWORD})
    assert login.status_code == 200
    assert login.json()["user"]["role"] == "quality_reviewer"

    # reviewer 访问 require_admin 端点 → 403
    reviewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert auth_client.get("/api/auth/users", headers=reviewer_headers).status_code == 403

    # 普通 user 访问 reviewer 依赖 → 403（直接用依赖函数验证，Phase 5 才建端点）
    with pytest.raises(Exception) as user_err:
        require_quality_reviewer({"id": "u", "username": "u", "role": "user"})
    assert getattr(user_err.value, "status_code", None) == 403

    # reviewer 通过自己的依赖 → 200（依赖函数直接返回 user dict，不抛异常）
    assert require_quality_reviewer({"id": member_id, "username": "member", "role": "quality_reviewer"}) \
        == {"id": member_id, "username": "member", "role": "quality_reviewer"}


@pytest.mark.web
def test_login_lock_returns_429_with_retry_after(auth_client):
    _bootstrap(auth_client)
    for _ in range(5):
        auth_client.post("/api/auth/token", json={"username": "admin", "password": "wrong-password-123"})
    locked = auth_client.post("/api/auth/token", json={"username": "admin", "password": PASSWORD})
    assert locked.status_code == 429
    assert locked.headers.get("retry-after")


@pytest.mark.web
def test_admin_deletes_user_and_data(auth_client):
    """删除账号：普通用户可删；不能删自己；最后一名管理员不可删。"""
    _bootstrap(auth_client)
    admin_token = auth_client.post("/api/auth/token", json={"username": "admin", "password": PASSWORD}).json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 建一个普通用户（自定义密码，无强制改密）并登录
    created = auth_client.post("/api/auth/users", json={"username": "doomed", "password": USER_PASSWORD}, headers=admin_headers)
    assert created.status_code == 201
    doomed_id = created.json()["id"]

    # 删除自己 → 403
    assert auth_client.delete("/api/auth/users/u_001", headers=admin_headers).status_code == 403
    # 删除不存在账号 → 404
    assert auth_client.delete("/api/auth/users/u_no_such_user", headers=admin_headers).status_code == 404

    # 删除普通用户 → 200，且账号消失、无法再登录
    resp = auth_client.delete(f"/api/auth/users/{doomed_id}", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is True
    assert auth_client.post("/api/auth/token", json={"username": "doomed", "password": USER_PASSWORD}).status_code == 401
    names = [u["username"] for u in auth_client.get("/api/auth/users", headers=admin_headers).json()["items"]]
    assert "doomed" not in names

    # 建第二个 admin 并登录：双管理员并存时，u_001 可被另一名管理员正常删除
    second = auth_client.post(
        "/api/auth/users",
        json={"username": "second-admin", "password": USER_PASSWORD, "role": "admin"},
        headers=admin_headers,
    )
    assert second.status_code == 201
    second_login = auth_client.post("/api/auth/token", json={"username": "second-admin", "password": USER_PASSWORD})
    assert second_login.status_code == 200
    second_headers = {"Authorization": f"Bearer {second_login.json()['access_token']}"}
    resp = auth_client.delete("/api/auth/users/u_001", headers=second_headers)
    assert resp.status_code == 200, resp.text
    assert auth_client.post("/api/auth/token", json={"username": "admin", "password": PASSWORD}).status_code == 401
    names = [u["username"] for u in auth_client.get("/api/auth/users", headers=second_headers).json()["items"]]
    assert names == ["second-admin"]
    # 此后系统只剩最后一名管理员（second-admin）：HTTP 层删除者恒为另一名有效 admin，
    # 故「删最后管理员 → 409」由服务层单测覆盖（test_cannot_delete_last_active_admin）


@pytest.mark.web
def test_delete_user_purges_local_avatar_files(auth_client, tmp_path, monkeypatch):
    """删除账号时一并清理本地头像目录（含换过头像留下的历史文件）。"""
    from careercrew_api.routers import auth as auth_router

    # 头像落盘改到临时目录，避免污染 data/uploads；同时屏蔽本机 OSS 配置，强制走本地回退
    monkeypatch.setattr(auth_router, "AVATAR_ROOT", tmp_path)
    monkeypatch.setattr(auth_router, "oss_config", lambda: None)
    _bootstrap(auth_client)
    admin_token = auth_client.post("/api/auth/token", json={"username": "admin", "password": PASSWORD}).json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    created = auth_client.post(
        "/api/auth/users",
        json={"username": "painted", "password": USER_PASSWORD},
        headers=admin_headers,
    )
    assert created.status_code == 201
    painted_id = created.json()["id"]
    login = auth_client.post("/api/auth/token", json={"username": "painted", "password": USER_PASSWORD})
    user_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # 连传两次头像（模拟换头像：目录里应有两个文件）
    png = b"\x89PNG\r\n\x1a\nfake-image"
    for _ in range(2):
        up = auth_client.post(
            "/api/auth/avatar",
            files={"file": ("a.png", png, "image/png")},
            headers=user_headers,
        )
        assert up.status_code == 200, up.text
    user_dir = tmp_path / painted_id
    assert user_dir.is_dir()
    assert len(list(user_dir.iterdir())) == 2

    # 删除账号 → 用户头像目录整体清除
    resp = auth_client.delete(f"/api/auth/users/{painted_id}", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert not user_dir.exists()


def _runtime_with_recording_stores(calls: dict[str, list]) -> object:
    """构造只记录调用的假运行时，用于断言账号删除的数据清理边界。"""

    class _VectorStore:
        def __init__(self, name: str) -> None:
            self._name = name

        def delete_by_metadata(self, filters):
            calls["vectors"].append((self._name, dict(filters)))
            return 3

        def metadata_exists(self, filters):
            return False

    class _Records:
        def delete_all_for_user(self, user_id):
            calls["records"].append(user_id)
            return {"records_deleted": 2}

    class _MemoryService:
        records = _Records()
        _vector_store = _VectorStore("episodic")

    class _Runtime:
        conversation_store = None
        memory_db = None
        attachment_store = None
        memory_service = _MemoryService()
        store = _VectorStore("knowledge")
        _workspace_semantic_search = None
        settings = None

        def _ensure_heavy(self) -> None:
            return None

    return _Runtime()


@pytest.fixture(autouse=True)
def _isolate_account_deletion_runtime(monkeypatch):
    """删号接口会解析全局 runtime：测试必须注入假运行时。

    否则 DELETE /api/auth/users/{id} 会走真实重组件，把开发库里的真实
    会话/记忆/向量清理掉（历史上确实发生过）。
    """
    from careercrew_api.routers import auth as auth_router

    calls: dict[str, list] = {"vectors": [], "records": []}
    monkeypatch.setattr(
        auth_router, "get_runtime", lambda: _runtime_with_recording_stores(calls)
    )


@pytest.mark.web
def test_delete_user_purges_long_term_memory_and_vectors(auth_client, monkeypatch):
    """删除账号时同时清空长期记忆行与向量副本，避免孤儿数据留在检索库。"""
    from careercrew_api.routers import auth as auth_router

    calls: dict[str, list] = {"vectors": [], "records": []}
    monkeypatch.setattr(auth_router, "get_runtime", lambda: _runtime_with_recording_stores(calls))

    _bootstrap(auth_client)
    admin_token = auth_client.post(
        "/api/auth/token", json={"username": "admin", "password": PASSWORD}
    ).json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    created = auth_client.post(
        "/api/auth/users",
        json={"username": "purge-me", "password": USER_PASSWORD},
        headers=admin_headers,
    )
    assert created.status_code == 201
    doomed_id = created.json()["id"]

    resp = auth_client.delete(f"/api/auth/users/{doomed_id}", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert calls["records"] == [doomed_id]
    assert ("episodic", {"user_id": doomed_id}) in calls["vectors"]
    assert ("knowledge", {"owner_user_id": doomed_id}) in calls["vectors"]
    assert ("knowledge", {"__access_user": doomed_id}) in calls["vectors"]


@pytest.mark.web
def test_delete_user_aborts_when_vector_purge_fails(auth_client, monkeypatch):
    """向量清理失败时中止账号删除，保留账号供重试，避免留下不可检索的孤儿数据。"""
    from careercrew_api.routers import auth as auth_router

    runtime = _runtime_with_recording_stores({"vectors": [], "records": []})

    def _explode(filters):
        raise RuntimeError("vector backend unavailable")

    runtime.memory_service._vector_store.delete_by_metadata = _explode
    monkeypatch.setattr(auth_router, "get_runtime", lambda: runtime)

    _bootstrap(auth_client)
    admin_token = auth_client.post(
        "/api/auth/token", json={"username": "admin", "password": PASSWORD}
    ).json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    created = auth_client.post(
        "/api/auth/users",
        json={"username": "keep-me", "password": USER_PASSWORD},
        headers=admin_headers,
    )
    kept_id = created.json()["id"]

    resp = auth_client.delete(f"/api/auth/users/{kept_id}", headers=admin_headers)
    assert resp.status_code == 500
    names = [
        item["username"]
        for item in auth_client.get("/api/auth/users", headers=admin_headers).json()["items"]
    ]
    assert "keep-me" in names
