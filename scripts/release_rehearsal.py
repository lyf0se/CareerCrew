"""在 Docker PostgreSQL 中执行隔离、可清理的 CareerCrew 发布演练。

``DATABASE_URL`` 仅用于派生连接参数和开发库保护边界。演练不会读取、备份、
迁移或删除该 URL 指向的数据库；所有读写都限制在本次生成的
``careercrew_rehearsal_<run-id>_*`` 临时数据库中。
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "docs" / "运维手册" / "发布演练.md"
DATABASE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
PREFIX_NAME = re.compile(r"^careercrew_rehearsal_[a-z0-9_]+$")
SYNTHETIC_PAYLOAD = "synthetic-release-rehearsal"
# Keep this aligned with scripts/validate_migrations.py.  The release drill
# must exercise the current published migration chain, not an old phase head.
EXPECTED_HEAD = "0018_eval_case_updated_at"


@dataclass(frozen=True)
class DatabaseConfig:
    url: str
    scheme: str
    user: str
    password: str
    host: str
    port: int
    database: str

    @classmethod
    def from_url(cls, value: str) -> DatabaseConfig:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"postgres", "postgresql", "postgresql+psycopg"}:
            raise ValueError("DATABASE_URL 必须使用 PostgreSQL 方言")
        if not parsed.username or not parsed.hostname:
            raise ValueError("DATABASE_URL 必须包含用户名和主机")
        database = unquote(parsed.path.lstrip("/"))
        if not DATABASE_NAME.fullmatch(database):
            raise ValueError("DATABASE_URL 必须包含安全的数据库名")
        return cls(
            url=value.strip(),
            scheme=parsed.scheme,
            user=unquote(parsed.username),
            password=unquote(parsed.password or ""),
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=database,
        )


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


class CommandError(RuntimeError):
    pass


def database_url_for(config: DatabaseConfig, database: str) -> str:
    if not DATABASE_NAME.fullmatch(database):
        raise ValueError(f"不安全的数据库名：{database!r}")
    parsed = urlsplit(config.url)
    return urlunsplit(parsed._replace(path=f"/{quote(database, safe='')}"))


def make_run_prefix() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"careercrew_rehearsal_{timestamp}_{secrets.token_hex(4)}"


def validate_temporary_database(
    name: str,
    *,
    run_prefix: str,
    source_database: str,
) -> None:
    if not PREFIX_NAME.fullmatch(run_prefix) or len(run_prefix) > 53:
        raise ValueError(f"无效的演练前缀：{run_prefix!r}")
    if not DATABASE_NAME.fullmatch(name):
        raise ValueError(f"不安全的数据库名：{name!r}")
    if name == source_database or not name.startswith(f"{run_prefix}_"):
        raise ValueError(f"拒绝操作本次演练范围外的数据库：{name!r}")


def docker_psql_command(
    config: DatabaseConfig,
    *,
    container: str,
    database: str,
    arguments: Sequence[str],
) -> list[str]:
    return [
        "docker",
        "exec",
        "-e",
        "PGPASSWORD",
        container,
        "psql",
        "-U",
        config.user,
        "-d",
        database,
        *arguments,
    ]


def _run(
    command: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    input_data: bytes | None = None,
    binary: bool = False,
) -> subprocess.CompletedProcess:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(  # noqa: S603 -- argv is a list built from validated internal commands
        list(command),
        cwd=ROOT,
        env=merged_env,
        input=input_data,
        capture_output=True,
        text=not binary,
        encoding=None if binary else "utf-8",
        errors=None if binary else "replace",
        check=False,
    )


def _error_detail(process: subprocess.CompletedProcess) -> str:
    value = process.stderr or process.stdout or "命令失败且未返回错误文本"
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return " ".join(value.strip().split())[-1000:]


class DockerPostgres:
    def __init__(self, config: DatabaseConfig, container: str, *, run_prefix: str) -> None:
        self.config = config
        self.container = container
        self.run_prefix = run_prefix

    def _validate_target(self, name: str) -> None:
        validate_temporary_database(
            name,
            run_prefix=self.run_prefix,
            source_database=self.config.database,
        )

    @property
    def _password_env(self) -> dict[str, str]:
        return {"PGPASSWORD": self.config.password}

    def execute(self, sql: str, *, database: str = "postgres") -> str:
        command = docker_psql_command(
            self.config,
            container=self.container,
            database=database,
            arguments=["-v", "ON_ERROR_STOP=1", "-t", "-A", "-c", sql],
        )
        process = _run(command, env=self._password_env)
        if process.returncode != 0:
            raise CommandError(_error_detail(process))
        return process.stdout.strip()

    def preflight(self) -> None:
        inspect = _run(["docker", "inspect", self.container])
        if inspect.returncode != 0:
            raise CommandError(f"PostgreSQL 容器不可用：{_error_detail(inspect)}")
        self.execute("SELECT 1")

    def create_database(self, name: str) -> None:
        self._validate_target(name)
        self.execute(f'CREATE DATABASE "{name}"')

    def drop_database(self, name: str) -> None:
        self._validate_target(name)
        self.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')

    def dump(self, database: str) -> bytes:
        self._validate_target(database)
        command = [
            "docker",
            "exec",
            "-e",
            "PGPASSWORD",
            self.container,
            "pg_dump",
            "-U",
            self.config.user,
            "-d",
            database,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
        ]
        process = _run(command, env=self._password_env, binary=True)
        if process.returncode != 0:
            raise CommandError(_error_detail(process))
        return process.stdout

    def restore(self, database: str, payload: bytes) -> None:
        self._validate_target(database)
        command = [
            "docker",
            "exec",
            "-i",
            "-e",
            "PGPASSWORD",
            self.container,
            "pg_restore",
            "-U",
            self.config.user,
            "-d",
            database,
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
        ]
        process = _run(
            command,
            env=self._password_env,
            input_data=payload,
            binary=True,
        )
        if process.returncode != 0:
            raise CommandError(_error_detail(process))


@contextmanager
def managed_databases(
    names: Sequence[str],
    *,
    run_prefix: str,
    source_database: str,
    create: Callable[[str], None],
    drop: Callable[[str], None],
) -> Iterator[None]:
    created: list[str] = []
    try:
        for name in names:
            validate_temporary_database(
                name,
                run_prefix=run_prefix,
                source_database=source_database,
            )
            create(name)
            created.append(name)
        yield
    finally:
        body_failed = sys.exc_info()[0] is not None
        cleanup_errors: list[str] = []
        for name in reversed(created):
            try:
                drop(name)
            except Exception as exc:  # cleanup remains best-effort for every database
                detail = f"{name}: {exc}"
                cleanup_errors.append(detail)
                print(f"清理临时数据库失败：{detail}", file=sys.stderr)
        if cleanup_errors and not body_failed:
            raise CommandError("临时数据库清理失败：" + "; ".join(cleanup_errors))


def create_failure_migration_tree(
    source: Path,
    workspace: Path,
    *,
    down_revision: str = "0008_prod_hardening",
) -> Path:
    destination = workspace / "migrations"
    shutil.copytree(source, destination)
    versions = destination / "versions"
    bad_revision = versions / "9999_rehearsal_bad.py"
    bad_revision.write_text(
        '"""仅用于隔离发布演练的必然失败迁移。"""\n'
        "from alembic import op\n\n"
        'revision = "9999_rehearsal_bad"\n'
        f'down_revision = "{down_revision}"\n'
        "branch_labels = None\n"
        "depends_on = None\n\n"
        "def upgrade() -> None:\n"
        '    op.execute("INSERT INTO no_such_table_rehearsal VALUES (1)")\n\n'
        "def downgrade() -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )
    return versions


def create_temporary_alembic_config(source: Path, script_location: Path, target: Path) -> None:
    content = source.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"(?m)^script_location\s*=.*$",
        f"script_location = {script_location.as_posix()}",
        content,
        count=1,
    )
    if count != 1:
        raise ValueError("alembic.ini 缺少唯一的 script_location")
    target.write_text(updated, encoding="utf-8")


def alembic_upgrade(
    config: DatabaseConfig,
    database: str,
    target: str,
    *,
    ini_path: Path | None = None,
) -> subprocess.CompletedProcess:
    command = [sys.executable, "-m", "alembic"]
    if ini_path is not None:
        command.extend(["-c", str(ini_path)])
    command.extend(["upgrade", target])
    return _run(command, env={"DATABASE_URL": database_url_for(config, database)})


def _revision_and_tables(pg: DockerPostgres, database: str) -> tuple[str, int]:
    revision = pg.execute("SELECT version_num FROM alembic_version", database=database)
    table_count = int(
        pg.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'",
            database=database,
        )
    )
    return revision, table_count


def _migration_result(
    name: str,
    process: subprocess.CompletedProcess,
    pg: DockerPostgres,
    database: str,
    expected_revision: str,
) -> CheckResult:
    if process.returncode != 0:
        return CheckResult(name, False, _error_detail(process))
    revision, tables = _revision_and_tables(pg, database)
    return CheckResult(
        name,
        revision == expected_revision and tables > 0,
        f"version={revision}, tables={tables}",
    )


def run_rehearsal(
    config: DatabaseConfig,
    *,
    container: str,
    run_prefix: str,
) -> list[CheckResult]:
    pg = DockerPostgres(config, container, run_prefix=run_prefix)
    pg.preflight()
    databases = {
        "fresh": f"{run_prefix}_fresh",
        "upgrade": f"{run_prefix}_upgrade",
        "failure": f"{run_prefix}_failure",
        "restore": f"{run_prefix}_restore",
    }
    results: list[CheckResult] = []

    with managed_databases(
        list(databases.values()),
        run_prefix=run_prefix,
        source_database=config.database,
        create=pg.create_database,
        drop=pg.drop_database,
    ):
        fresh = alembic_upgrade(config, databases["fresh"], "head")
        results.append(
            _migration_result(
                "路径 A：空库 → head",
                fresh,
                pg,
                databases["fresh"],
                EXPECTED_HEAD,
            )
        )

        base = alembic_upgrade(config, databases["upgrade"], "0002_long_term_memory_records")
        mid = (
            pg.execute("SELECT version_num FROM alembic_version", database=databases["upgrade"])
            if base.returncode == 0
            else "upgrade-to-0002-failed"
        )
        head = (
            alembic_upgrade(config, databases["upgrade"], "head")
            if base.returncode == 0
            else base
        )
        final = (
            pg.execute("SELECT version_num FROM alembic_version", database=databases["upgrade"])
            if head.returncode == 0
            else "upgrade-to-head-failed"
        )
        results.append(
            CheckResult(
                "路径 B：0002 → head",
                base.returncode == 0
                and head.returncode == 0
                and mid == "0002_long_term_memory_records"
                and final == EXPECTED_HEAD,
                f"mid={mid}, final={final}",
            )
        )

        before_process = alembic_upgrade(
            config,
            databases["failure"],
            "0007_version_attribution_shares",
        )
        before = (
            pg.execute("SELECT version_num FROM alembic_version", database=databases["failure"])
            if before_process.returncode == 0
            else "upgrade-to-0007-failed"
        )
        with tempfile.TemporaryDirectory(prefix=f"{run_prefix}_migrations_") as raw_workspace:
            workspace = Path(raw_workspace)
            create_failure_migration_tree(
                ROOT / "migrations", workspace, down_revision=EXPECTED_HEAD,
            )
            temp_ini = workspace / "alembic.ini"
            create_temporary_alembic_config(
                ROOT / "alembic.ini",
                workspace / "migrations",
                temp_ini,
            )
            failed = alembic_upgrade(
                config,
                databases["failure"],
                "9999_rehearsal_bad",
                ini_path=temp_ini,
            )
            stuck = pg.execute(
                "SELECT version_num FROM alembic_version",
                database=databases["failure"],
            )
        recovered_process = alembic_upgrade(config, databases["failure"], "head")
        recovered = (
            pg.execute("SELECT version_num FROM alembic_version", database=databases["failure"])
            if recovered_process.returncode == 0
            else "recovery-failed"
        )
        results.append(
            CheckResult(
                "路径 C：失败迁移回滚与恢复",
                before_process.returncode == 0
                and failed.returncode != 0
                and stuck == before == "0007_version_attribution_shares"
                and recovered == EXPECTED_HEAD,
                f"before={before}, after_failure={stuck}, recovered={recovered}",
            )
        )

        restore_db = databases["restore"]
        pg.execute(
            "CREATE TABLE release_rehearsal_probe (id INTEGER PRIMARY KEY, payload TEXT NOT NULL); "  # noqa: S608 -- payload is a fixed module constant
            f"INSERT INTO release_rehearsal_probe VALUES (1, '{SYNTHETIC_PAYLOAD}')",
            database=restore_db,
        )
        backup = pg.dump(restore_db)
        pg.drop_database(restore_db)
        pg.create_database(restore_db)
        pg.restore(restore_db, backup)
        restored = pg.execute(
            "SELECT count(*)::text || '|' || min(payload) FROM release_rehearsal_probe",
            database=restore_db,
        )
        results.append(
            CheckResult(
                "路径 D：合成数据备份恢复",
                restored == f"1|{SYNTHETIC_PAYLOAD}",
                f"rows=1, payload={restored.partition('|')[2] or '<missing>'}",
            )
        )

    return results


def render_report(
    *,
    run_prefix: str,
    started_at: datetime,
    duration_seconds: float,
    source_database: str,
    container: str,
    results: Sequence[CheckResult],
) -> str:
    rows = "\n".join(
        f"| {'通过' if item.passed else '失败'} | {item.name} | {item.detail} |"
        for item in results
    )
    passed = all(item.passed for item in results) and bool(results)
    return f"""# CareerCrew 发布演练

- 执行时间：{started_at.astimezone(UTC).isoformat(timespec='seconds')}
- 运行前缀：`{run_prefix}`
- PostgreSQL 容器：`{container}`
- 迁移 head：`{EXPECTED_HEAD}`
- 受保护源数据库：`{source_database}`（仅从 DSN 派生名称，未读取或修改）
- 用时：{duration_seconds:.2f} 秒
- 结论：{'全部通过' if passed else '存在失败项'}

| 结果 | 演练项 | 证据 |
| --- | --- | --- |
{rows}

备份恢复只使用临时库中的固定合成探针行，真实开发库数据未进入备份。
失败迁移写入系统临时目录中的迁移副本，仓库 `migrations/` 未被注入演练文件。
所有数据库名都必须匹配本次唯一前缀；退出和异常路径均执行清理。
"""


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CareerCrew 隔离发布演练")
    parser.add_argument(
        "--container",
        default=os.environ.get("CAREERCREW_PG_CONTAINER", "postgres"),
        help="本机 PostgreSQL Docker 容器名",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help="Markdown 演练报告路径",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    load_dotenv(ROOT / ".env", override=False)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("缺少 DATABASE_URL；脚本拒绝猜测数据库凭据或受保护库名", file=sys.stderr)
        return 2

    started_at = datetime.now(UTC)
    started = time.monotonic()
    run_prefix = make_run_prefix()
    try:
        config = DatabaseConfig.from_url(database_url)
        results = run_rehearsal(config, container=args.container, run_prefix=run_prefix)
    except Exception as exc:
        source_database = "<DSN 解析失败>"
        try:
            source_database = DatabaseConfig.from_url(database_url).database
        except ValueError:
            pass
        results = [CheckResult("演练执行", False, str(exc))]
        config = None
    duration = time.monotonic() - started
    report = render_report(
        run_prefix=run_prefix,
        started_at=started_at,
        duration_seconds=duration,
        source_database=config.database if config else source_database,
        container=args.container,
        results=results,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    print(report)
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
