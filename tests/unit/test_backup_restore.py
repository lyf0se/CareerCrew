"""Tests for the automated backup and restore-drill helpers."""
from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from scripts import backup_restore


@pytest.fixture(autouse=True)
def _no_local_postgres_client(monkeypatch):
    """让本模块与宿主机是否装了 PostgreSQL 客户端彻底解耦。

    ``verify_backup`` 会探测 PATH 上的 ``pg_restore`` 并对归档跑 ``--list``
    校验。本模块用假 dump runner 造出的归档不是真实 dump，永远过不了该校验；
    而 GitHub runner 预装 postgresql-client，校验会真的执行并抢先于被测的
    清理/编排逻辑失败——同一份代码本地全绿、CI 必红的根源。
    """
    monkeypatch.setattr(backup_restore.shutil, "which", lambda _name: None)


def test_backup_rejects_inherited_libpq_routing(monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "203.0.113.99")
    with pytest.raises(backup_restore.BackupValidationError, match="routing"):
        backup_restore.parse_database_url("postgresql://backup:secret@localhost/test_backup")


def test_backup_defaults_cover_conversation_message_vectors() -> None:
    assert "careercrew_workspace_messages" in backup_restore.DEFAULT_COLLECTIONS


def test_pg_dump_falls_back_to_container_without_client_binary(tmp_path: Path, monkeypatch) -> None:
    """主机缺少 pg_dump 时回退到容器内执行，argv 不携带密码。"""
    calls: list[tuple[list[str], dict]] = []

    def _fake_run(command, **kwargs):
        calls.append((list(command), dict(kwargs)))
        stream = kwargs.get("stdout")
        if stream is not None:
            stream.write(b"container dump")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(backup_restore.shutil, "which", lambda _name: None)
    monkeypatch.setattr(backup_restore, "resolve_pg_container", lambda explicit=None: "postgres")
    monkeypatch.setattr(backup_restore.subprocess, "run", _fake_run)

    output = tmp_path / "postgres.dump"
    config = backup_restore.parse_database_url("postgresql://backup:secret@localhost:5432/demo")
    backup_restore.run_pg_dump(config, output)

    command = calls[0][0]
    assert command[:4] == ["docker", "exec", "postgres", "pg_dump"]
    assert "--dbname" in command and "demo" in command
    assert not any("secret" in str(part) for part in command)
    assert output.read_bytes() == b"container dump"


def test_pg_restore_falls_back_to_container_without_client_binary(tmp_path: Path, monkeypatch) -> None:
    """主机缺少 pg_restore 时把 dump 通过 stdin 送进容器执行。"""
    calls: list[tuple[list[str], dict]] = []
    dump_path = tmp_path / "postgres.dump"
    dump_path.write_bytes(b"container dump")

    def _fake_run(command, **kwargs):
        calls.append((list(command), dict(kwargs)))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(backup_restore.shutil, "which", lambda _name: None)
    monkeypatch.setattr(backup_restore, "resolve_pg_container", lambda explicit=None: "postgres")
    monkeypatch.setattr(backup_restore.subprocess, "run", _fake_run)

    config = backup_restore.parse_database_url("postgresql://backup:secret@localhost:5432/demo")
    backup_restore.restore_postgres_dump(dump_path, "careercrew_restore_unit", config)

    command, kwargs = calls[0]
    assert command[:5] == ["docker", "exec", "-i", "postgres", "pg_restore"]
    assert "--exit-on-error" in command
    assert "careercrew_restore_unit" in command
    assert not any("secret" in str(part) for part in command)
    assert kwargs["stdin"] is not None


def test_qdrant_snapshots_skip_uncreated_optional_workspace_collection(tmp_path: Path, monkeypatch) -> None:
    class _MissingResponse:
        status_code = 404

        def raise_for_status(self) -> None:
            raise requests.HTTPError("not found")

        def json(self):
            return {"status": {"error": "not found"}}

    class _Session:
        def get(self, url: str, **kwargs):
            del kwargs
            assert url.endswith("/collections/careercrew_workspace_messages")
            return _MissingResponse()

        def post(self, *args, **kwargs):
            raise AssertionError("missing optional collection must not create a snapshot")

    monkeypatch.setattr(backup_restore.requests, "Session", _Session)

    assert backup_restore.create_qdrant_snapshots(
        "http://qdrant.example:6333",
        ["careercrew_workspace_messages"],
        tmp_path,
    ) == []


def _fake_dump(config, output_path: Path) -> None:
    output_path.write_bytes(b"synthetic postgres custom dump")


def _fake_snapshots(qdrant_url: str, collections: list[str], output_dir: Path):
    del qdrant_url
    result = []
    for collection in collections:
        path = output_dir / "qdrant" / f"{collection}.snapshot"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"snapshot:{collection}".encode())
        result.append(
            backup_restore.SnapshotArtifact(
                collection=collection,
                snapshot=f"{collection}.snapshot",
                path=path,
                point_count=2,
            )
        )
    return result


def _create_backup(tmp_path: Path, *, collections: list[str] | None = None) -> Path:
    return backup_restore.create_backup(
        database_url="postgresql://backup_user:super-secret@db.example:5433/careercrew?sslmode=require",
        qdrant_url="http://qdrant.example:6333",
        backup_root=tmp_path / "backups",
        uploads_dir=tmp_path / "data" / "uploads",
        parsed_dir=tmp_path / "data" / "parsed",
        collections=collections if collections is not None else ["careercrew_mm"],
        now=datetime(2026, 9, 9, 2, 0, 0, tzinfo=UTC),
        pg_dump_runner=_fake_dump,
        qdrant_snapshotter=_fake_snapshots,
    )


def test_parse_database_url_keeps_password_internal_and_never_in_dump_argv() -> None:
    config = backup_restore.parse_database_url(
        "postgresql://backup_user:super-secret@db.example:5433/careercrew?sslmode=require"
    )

    command = backup_restore.pg_dump_command(config, Path("/tmp/careercrew.dump"))

    assert config.password == "super-secret"
    assert config.host == "db.example"
    assert config.port == 5433
    assert config.database == "careercrew"
    assert "super-secret" not in command
    assert "super-secret" not in backup_restore.redacted_database_identifier(config)


def test_database_url_for_normalizes_psycopg_driver_scheme() -> None:
    config = backup_restore.parse_database_url(
        "postgresql+psycopg://backup_user:super-secret@db.example:5433/careercrew?sslmode=require"
    )

    assert backup_restore._database_url_for(config, "postgres") == (
        "postgresql://backup_user:super-secret@db.example:5433/postgres?sslmode=require"
    )


def test_create_backup_manifest_records_size_hash_and_point_count(tmp_path: Path) -> None:
    uploads = tmp_path / "data" / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "resume.txt").write_text("synthetic", encoding="utf-8")

    backup_dir = _create_backup(tmp_path)
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))

    artifact_names = {item["path"] for item in manifest["artifacts"]}
    assert "postgres.dump" in artifact_names
    assert "files.zip" in artifact_names
    assert "qdrant/careercrew_mm.snapshot" in artifact_names
    assert all(item["size"] > 0 and len(item["sha256"]) == 64 for item in manifest["artifacts"])
    assert manifest["qdrant"][0]["point_count"] == 2
    assert manifest["database"] == {
        "host": "db.example",
        "port": 5433,
        "database": "careercrew",
        "user": "backup_user",
    }


def test_verify_backup_rejects_changed_artifact(tmp_path: Path) -> None:
    backup_dir = _create_backup(tmp_path, collections=[])
    (backup_dir / "postgres.dump").write_bytes(b"changed")

    with pytest.raises(backup_restore.BackupValidationError, match="sha256|size"):
        backup_restore.verify_backup(backup_dir, check_pg_restore=False)


def test_verify_backup_rejects_undeclared_qdrant_manifest_artifact(tmp_path: Path) -> None:
    backup_dir = _create_backup(tmp_path)
    manifest_path = backup_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["qdrant"][0]["path"] = "qdrant/unhashed.snapshot"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(backup_restore.BackupValidationError, match="Qdrant.*artifact"):
        backup_restore.verify_backup(backup_dir, check_pg_restore=False)


def test_file_manifest_rejects_duplicate_archive_members(tmp_path: Path) -> None:
    uploads = tmp_path / "data" / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "resume.txt").write_text("synthetic", encoding="utf-8")
    backup_dir = _create_backup(tmp_path)
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))

    with zipfile.ZipFile(backup_dir / "files.zip", "w") as archive:
        archive.writestr("data/uploads/resume.txt", b"synthetic")
        archive.writestr("data/uploads/resume.txt", b"synthetic")

    with pytest.raises(backup_restore.BackupValidationError, match="duplicate"):
        backup_restore._validate_file_manifest_archive(backup_dir / "files.zip", manifest)


def test_restore_drill_attempts_database_cleanup_when_restore_fails(tmp_path: Path, monkeypatch) -> None:
    backup_dir = _create_backup(tmp_path, collections=[])
    admin_calls: list[str] = []

    def fake_admin(database_url: str, sql: str) -> None:
        del database_url
        admin_calls.append(sql)

    def fail_restore(*args, **kwargs) -> None:
        raise backup_restore.BackupValidationError("synthetic restore failure")

    monkeypatch.setattr(backup_restore, "_execute_admin", fake_admin)
    monkeypatch.setattr(backup_restore, "restore_postgres_dump", fail_restore)

    with pytest.raises(backup_restore.BackupValidationError, match="synthetic restore failure"):
        backup_restore.restore_drill(
            backup_dir,
            database_url="postgresql://backup_user:secret@db.example:5433/careercrew",
            now=datetime(2026, 9, 9, 3, 0, 0, tzinfo=UTC),
        )

    assert any(sql.startswith('DROP DATABASE IF EXISTS "careercrew_restore_') for sql in admin_calls)


def test_restore_drill_surfaces_database_cleanup_failure(tmp_path: Path, monkeypatch) -> None:
    backup_dir = _create_backup(tmp_path, collections=[])
    calls = 0

    def fake_admin(database_url: str, sql: str) -> None:
        nonlocal calls
        del database_url
        calls += 1
        if sql.startswith("DROP DATABASE"):
            raise backup_restore.BackupValidationError("synthetic cleanup failure")

    def fail_restore(*args, **kwargs) -> None:
        raise backup_restore.BackupValidationError("synthetic restore failure")

    monkeypatch.setattr(backup_restore, "_execute_admin", fake_admin)
    monkeypatch.setattr(backup_restore, "restore_postgres_dump", fail_restore)

    with pytest.raises(backup_restore.BackupValidationError, match="cleanup failure"):
        backup_restore.restore_drill(
            backup_dir,
            database_url="postgresql://backup_user:secret@db.example:5433/careercrew",
            now=datetime(2026, 9, 9, 3, 0, 1, tzinfo=UTC),
        )

    assert calls == 2


def test_restore_drill_uses_unique_generated_database_name(tmp_path: Path, monkeypatch) -> None:
    backup_dir = _create_backup(tmp_path, collections=[])
    admin_calls: list[str] = []

    def fake_admin(database_url: str, sql: str) -> None:
        del database_url
        admin_calls.append(sql)

    def fail_restore(*args, **kwargs) -> None:
        raise backup_restore.BackupValidationError("synthetic restore failure")

    monkeypatch.setattr(backup_restore, "_execute_admin", fake_admin)
    monkeypatch.setattr(backup_restore, "restore_postgres_dump", fail_restore)
    monkeypatch.setattr(backup_restore.secrets, "token_hex", lambda size: "a1b2c3d4e5f60708")

    with pytest.raises(backup_restore.BackupValidationError, match="synthetic restore failure"):
        backup_restore.restore_drill(
            backup_dir,
            database_url="postgresql://backup_user:secret@db.example:5433/careercrew",
            now=datetime(2026, 9, 9, 3, 0, 3, tzinfo=UTC),
        )

    assert any("careercrew_restore_20260909030003_a1b2c3d4e5f60708" in sql for sql in admin_calls)


def test_restore_drill_cleans_database_when_create_request_errors(tmp_path: Path, monkeypatch) -> None:
    backup_dir = _create_backup(tmp_path, collections=[])
    admin_calls: list[str] = []

    def fail_create(database_url: str, sql: str) -> None:
        del database_url
        admin_calls.append(sql)
        if sql.startswith("CREATE DATABASE"):
            raise backup_restore.BackupValidationError("connection lost after create request")

    monkeypatch.setattr(backup_restore, "_execute_admin", fail_create)

    with pytest.raises(backup_restore.BackupValidationError, match="connection lost after create request"):
        backup_restore.restore_drill(
            backup_dir,
            database_url="postgresql://backup_user:secret@db.example:5433/careercrew",
            now=datetime(2026, 9, 9, 3, 0, 2, tzinfo=UTC),
        )

    assert any(sql.startswith('DROP DATABASE IF EXISTS "careercrew_restore_') for sql in admin_calls)


def test_restore_drill_runs_application_canary_and_verifies_file_manifest(
    tmp_path: Path, monkeypatch,
) -> None:
    uploads = tmp_path / "data" / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "resume.txt").write_text("restored resume", encoding="utf-8")
    backup_dir = _create_backup(tmp_path, collections=[])
    admin_calls: list[str] = []
    observed: dict[str, object] = {}

    def fake_admin(database_url: str, sql: str) -> None:
        del database_url
        admin_calls.append(sql)

    monkeypatch.setattr(backup_restore, "_execute_admin", fake_admin)
    monkeypatch.setattr(backup_restore, "restore_postgres_dump", lambda *args, **kwargs: None)
    monkeypatch.setattr(backup_restore, "_verify_restored_postgres", lambda target_url: None)

    def application_canary(target_url: str, extracted_root: Path, manifest: dict) -> dict[str, object]:
        del target_url
        observed["file"] = (extracted_root / "data" / "uploads" / "resume.txt").read_text(encoding="utf-8")
        observed["manifest_files"] = len(manifest["files"])
        return {"database_relationships": 0, "files_verified": len(manifest["files"])}

    result = backup_restore.restore_drill(
        backup_dir,
        database_url="postgresql://backup_user:secret@db.example:5433/careercrew",
        application_canary=application_canary,
        now=datetime(2026, 9, 9, 3, 0, 5, tzinfo=UTC),
    )

    assert result["cleaned"] is True
    assert result["application_canary"]["files"]["verified_files"] == 1
    assert result["application_canary"]["database"] == {"database_relationships": 0, "files_verified": 1}
    assert observed == {"file": "restored resume", "manifest_files": 1}
    assert any(sql.startswith('DROP DATABASE IF EXISTS "careercrew_restore_') for sql in admin_calls)


class _FakeQdrantResponse:
    def __init__(self, payload=None, error: Exception | None = None, status_code: int = 200) -> None:
        self.payload = payload
        self.error = error
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.error is not None:
            raise self.error

    def json(self):
        return self.payload


def test_qdrant_restore_registers_target_before_recovery_failure(tmp_path: Path, monkeypatch) -> None:
    backup_dir = _create_backup(tmp_path)
    manifest = backup_restore.verify_backup(backup_dir, check_pg_restore=False)
    deleted: list[str] = []
    monkeypatch.setattr(
        backup_restore.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )
    monkeypatch.setattr(
        backup_restore.requests,
        "put",
        lambda *args, **kwargs: _FakeQdrantResponse(error=requests.HTTPError("recover failed")),
    )
    monkeypatch.setattr(
        backup_restore.requests,
        "delete",
        lambda url, **kwargs: deleted.append(url) or _FakeQdrantResponse(status_code=204),
    )

    with pytest.raises(backup_restore.BackupValidationError, match="Qdrant snapshot recovery"):
        backup_restore.restore_qdrant_snapshots(
            backup_dir,
            manifest,
            "http://qdrant.example:6333",
            "qdrant",
            "20260909120000",
        )

    assert any("__restore__" in url for url in deleted)


def test_qdrant_restore_creates_snapshot_directory_before_copy(tmp_path: Path, monkeypatch) -> None:
    backup_dir = _create_backup(tmp_path)
    manifest = backup_restore.verify_backup(backup_dir, check_pg_restore=False)
    calls: list[list[str]] = []
    recovery_calls: list[tuple[str, dict]] = []

    def fake_run(args, **kwargs):
        del kwargs
        calls.append(args)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(backup_restore.subprocess, "run", fake_run)
    monkeypatch.setattr(
        backup_restore.requests,
        "put",
        lambda url, json, **kwargs: recovery_calls.append((url, json))
        or _FakeQdrantResponse(payload={"result": {}}),
    )
    monkeypatch.setattr(
        backup_restore.requests,
        "get",
        lambda *args, **kwargs: _FakeQdrantResponse(payload={"result": {"points_count": 2}}),
    )
    monkeypatch.setattr(
        backup_restore.requests,
        "delete",
        lambda *args, **kwargs: _FakeQdrantResponse(status_code=204),
    )

    backup_restore.restore_qdrant_snapshots(
        backup_dir,
        manifest,
        "http://qdrant.example:6333",
        "qdrant",
        "20260909120000",
    )

    assert calls[0] == ["docker", "exec", "qdrant", "mkdir", "-p", "/qdrant/snapshots"]
    assert calls[1][0:2] == ["docker", "cp"]
    assert recovery_calls[0][1]["location"].startswith("file:///qdrant/snapshots/")


def test_qdrant_restore_uploads_snapshot_to_isolated_remote_target(
    tmp_path: Path, monkeypatch,
) -> None:
    backup_dir = _create_backup(tmp_path)
    manifest = backup_restore.verify_backup(backup_dir, check_pg_restore=False)
    uploads: list[tuple[str, dict, dict]] = []
    deleted: list[tuple[str, dict]] = []

    def fail_docker(*_args, **_kwargs):
        raise AssertionError("remote Qdrant restore must not require local Docker")

    def fake_post(url, **kwargs):
        uploads.append((url, kwargs.get("headers", {}), kwargs.get("files", {})))
        return _FakeQdrantResponse(payload={"result": {}})

    monkeypatch.setattr(backup_restore.subprocess, "run", fail_docker)
    monkeypatch.setattr(backup_restore.requests, "post", fake_post)
    monkeypatch.setattr(
        backup_restore.requests,
        "get",
        lambda _url, **kwargs: _FakeQdrantResponse(
            payload={"result": {"points_count": 2}},
        ),
    )
    monkeypatch.setattr(
        backup_restore.requests,
        "delete",
        lambda url, **kwargs: deleted.append((url, kwargs.get("headers", {})))
        or _FakeQdrantResponse(status_code=204),
    )

    backup_restore.restore_qdrant_snapshots(
        backup_dir,
        manifest,
        "https://restore-qdrant.example",
        None,
        "20260909120000",
        qdrant_api_key="restore-key",
    )

    assert len(uploads) == len(manifest["qdrant"])
    assert all(url.endswith("/snapshots/upload?wait=true") for url, _, _ in uploads)
    assert all(headers == {"api-key": "restore-key"} for _, headers, _ in uploads)
    assert all("snapshot" in files for _, _, files in uploads)
    assert len(deleted) == len(uploads)
    assert all(headers == {"api-key": "restore-key"} for _, headers in deleted)


def test_qdrant_restore_never_falls_back_to_source_environment_credentials(
    tmp_path: Path, monkeypatch,
) -> None:
    backup_dir = _create_backup(tmp_path)
    manifest = backup_restore.verify_backup(backup_dir, check_pg_restore=False)
    headers_seen: list[dict] = []

    monkeypatch.setenv("QDRANT_API_KEY", "source-key-must-not-leak")
    monkeypatch.setattr(
        backup_restore.requests,
        "post",
        lambda _url, **kwargs: headers_seen.append(kwargs.get("headers", {}))
        or _FakeQdrantResponse(payload={"result": {}}),
    )
    monkeypatch.setattr(
        backup_restore.requests,
        "get",
        lambda _url, **kwargs: _FakeQdrantResponse(
            payload={"result": {"points_count": 2}},
        ),
    )
    monkeypatch.setattr(
        backup_restore.requests,
        "delete",
        lambda _url, **kwargs: _FakeQdrantResponse(status_code=204),
    )

    backup_restore.restore_qdrant_snapshots(
        backup_dir,
        manifest,
        "https://restore-qdrant.example",
        None,
        "20260909120000",
    )

    assert headers_seen and all(headers == {} for headers in headers_seen)


def test_qdrant_restore_surfaces_cleanup_http_failure(tmp_path: Path, monkeypatch) -> None:
    backup_dir = _create_backup(tmp_path)
    manifest = backup_restore.verify_backup(backup_dir, check_pg_restore=False)
    monkeypatch.setattr(
        backup_restore.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )
    monkeypatch.setattr(
        backup_restore.requests,
        "put",
        lambda *args, **kwargs: _FakeQdrantResponse(payload={"result": {}}),
    )
    monkeypatch.setattr(
        backup_restore.requests,
        "get",
        lambda *args, **kwargs: _FakeQdrantResponse(payload={"result": {"points_count": 2}}),
    )
    monkeypatch.setattr(
        backup_restore.requests,
        "delete",
        lambda *args, **kwargs: _FakeQdrantResponse(error=requests.HTTPError("delete failed")),
    )

    with pytest.raises(backup_restore.BackupValidationError, match="Qdrant restore cleanup"):
        backup_restore.restore_qdrant_snapshots(
            backup_dir,
            manifest,
            "http://qdrant.example:6333",
            "qdrant",
            "20260909120000",
        )


def test_qdrant_restore_continues_after_cleanup_subprocess_error(tmp_path: Path, monkeypatch) -> None:
    backup_dir = _create_backup(tmp_path, collections=["careercrew_mm", "careercrew_episodic_v2"])
    manifest = backup_restore.verify_backup(backup_dir, check_pg_restore=False)
    cleanup_calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        del kwargs
        if args[1] == "exec" and args[3] == "mkdir":
            return SimpleNamespace(returncode=0, stderr="", stdout="")
        if args[1] == "cp":
            return SimpleNamespace(returncode=0, stderr="", stdout="")
        cleanup_calls.append(args)
        if len(cleanup_calls) == 1:
            raise OSError("docker unavailable")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(backup_restore.subprocess, "run", fake_run)
    monkeypatch.setattr(
        backup_restore.requests,
        "put",
        lambda *args, **kwargs: _FakeQdrantResponse(payload={"result": {}}),
    )
    monkeypatch.setattr(
        backup_restore.requests,
        "get",
        lambda *args, **kwargs: _FakeQdrantResponse(payload={"result": {"points_count": 2}}),
    )
    monkeypatch.setattr(
        backup_restore.requests,
        "delete",
        lambda *args, **kwargs: _FakeQdrantResponse(status_code=204),
    )

    with pytest.raises(backup_restore.BackupValidationError, match="snapshot file"):
        backup_restore.restore_qdrant_snapshots(
            backup_dir,
            manifest,
            "http://qdrant.example:6333",
            "qdrant",
            "20260909120000",
        )

    assert len(cleanup_calls) == 2


def test_resolve_qdrant_container_prefers_compose_service(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        del kwargs
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="compose-container-id\n", stderr="")

    monkeypatch.setattr(backup_restore.subprocess, "run", fake_run)

    assert backup_restore.resolve_qdrant_container() == "compose-container-id"
    assert calls == [["docker", "compose", "ps", "-q", "qdrant"]]


def test_resolve_qdrant_container_honors_explicit_container_before_compose(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        del kwargs
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="explicit-id|true\n", stderr="")

    monkeypatch.setattr(backup_restore.subprocess, "run", fake_run)

    assert backup_restore.resolve_qdrant_container("restore-qdrant") == "restore-qdrant"
    assert calls == [[
        "docker", "inspect", "--format", "{{.Id}}|{{.State.Running}}", "restore-qdrant",
    ]]


def test_resolve_qdrant_container_validates_explicit_container(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        del kwargs
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="container-id|true\n", stderr="")

    monkeypatch.setattr(backup_restore.subprocess, "run", fake_run)

    assert backup_restore.resolve_qdrant_container("qdrant") == "qdrant"
    assert calls == [
        ["docker", "inspect", "--format", "{{.Id}}|{{.State.Running}}", "qdrant"],
    ]


def test_resolve_qdrant_container_rejects_stopped_explicit_container(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        del kwargs
        return SimpleNamespace(returncode=0, stdout="container-id|false\n", stderr="")

    monkeypatch.setattr(backup_restore.subprocess, "run", fake_run)

    with pytest.raises(backup_restore.BackupValidationError, match="could not be resolved"):
        backup_restore.resolve_qdrant_container("qdrant")


def test_restore_drill_cli_does_not_use_source_database_or_qdrant_credentials(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_restore_drill(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return "careercrew_restore_test"

    monkeypatch.setattr(backup_restore, "_load_environment", lambda: None)
    monkeypatch.setattr(backup_restore, "restore_drill", fake_restore_drill)
    monkeypatch.setenv("DATABASE_URL", "postgresql://source:secret@source.example:5432/careercrew")
    monkeypatch.setenv("QDRANT_URL", "https://source-qdrant.example")
    monkeypatch.setenv("QDRANT_API_KEY", "source-key")
    monkeypatch.delenv("RESTORE_DATABASE_URL", raising=False)
    monkeypatch.delenv("RESTORE_QDRANT_URL", raising=False)
    monkeypatch.delenv("RESTORE_QDRANT_CONTAINER", raising=False)
    monkeypatch.delenv("RESTORE_QDRANT_API_KEY", raising=False)

    assert backup_restore.main(["restore-drill", "backup-run"]) == 0
    assert captured["database_url"] == ""
    assert captured["qdrant_url"] == ""
    assert captured["qdrant_container"] is None
    assert captured["qdrant_api_key"] is None


def test_resolve_qdrant_container_falls_back_to_running_legacy_name(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        del kwargs
        calls.append(args)
        if args[:4] == ["docker", "compose", "ps", "-q"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="legacy-container-id\n", stderr="")

    monkeypatch.setattr(backup_restore.subprocess, "run", fake_run)

    assert backup_restore.resolve_qdrant_container() == "legacy-container-id"
    assert calls == [
        ["docker", "compose", "ps", "-q", "qdrant"],
        ["docker", "ps", "--filter", "name=^qdrant$", "--format", "{{.ID}}"],
    ]


def test_schedule_installer_is_non_destructive_by_default() -> None:
    script = (backup_restore.ROOT / "scripts" / "install_backup_schedule.ps1").read_text(encoding="utf-8")

    assert "[switch]$AllowOverwrite" in script
    assert "Get-ScheduledTask" in script
    assert "if ($AllowOverwrite)" in script


def test_prune_backups_only_removes_old_exact_backup_children(tmp_path: Path) -> None:
    root = tmp_path / "backups"
    root.mkdir()
    old = root / "careercrew-20200101-000000"
    fresh = root / "careercrew-20260908-000000"
    arbitrary = root / "careercrew-20200101-000000.tmp"
    for path in (old, fresh, arbitrary):
        path.mkdir()
        (path / "marker").write_text("x", encoding="utf-8")
    source = tmp_path / "data" / "uploads"
    source.mkdir(parents=True)
    (source / "keep.txt").write_text("keep", encoding="utf-8")

    removed = backup_restore.prune_backups(
        root,
        retention_days=30,
        now=datetime(2026, 9, 9, tzinfo=UTC),
    )

    assert removed == [old]
    assert not old.exists()
    assert fresh.exists()
    assert arbitrary.exists()
    assert source.exists()


@pytest.mark.parametrize("backup_root_kind", ["inside_source", "parent_of_source"])
def test_create_backup_rejects_backup_root_overlapping_source(tmp_path: Path, backup_root_kind: str) -> None:
    uploads = tmp_path / "data" / "uploads"
    parsed = tmp_path / "data" / "parsed"
    uploads.mkdir(parents=True)
    parsed.mkdir(parents=True)
    backup_root = uploads / "backup-runs" if backup_root_kind == "inside_source" else tmp_path / "data"

    with pytest.raises(backup_restore.BackupValidationError, match="source"):
        backup_restore.create_backup(
            database_url="postgresql://backup_user:secret@db.example:5433/careercrew",
            backup_root=backup_root,
            uploads_dir=uploads,
            parsed_dir=parsed,
            collections=[],
            pg_dump_runner=_fake_dump,
            qdrant_snapshotter=_fake_snapshots,
        )


@pytest.mark.parametrize(
    "target",
    ["careercrew", "postgres", "../careercrew_restore_bad", "careercrew_restore_bad/path"],
)
def test_validate_restore_target_rejects_source_root_and_path_traversal(target: str) -> None:
    with pytest.raises(backup_restore.BackupValidationError):
        backup_restore.validate_restore_target(target, "careercrew")


def test_validate_restore_target_accepts_generated_temporary_name() -> None:
    backup_restore.validate_restore_target(
        "careercrew_restore_20260909120000",
        "careercrew",
    )
