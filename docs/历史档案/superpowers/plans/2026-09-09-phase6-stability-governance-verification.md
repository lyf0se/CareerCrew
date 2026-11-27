# CareerCrew 第六期全局稳定性治理验收记录

日期：2026-09-10
分支：`codex/job-preparation-phase1`
范围：第六期 P0 稳定性治理；不包含第七、八期功能，不 push、不 merge。

## 代码交付

本期代码交付链以 `4226e91 fix: close phase six restore safety gaps` 收口，随后通过验收文档提交固化。主要交付包括：

- 浏览器 CDP 路由注册到主应用，三条路由均要求登录并限制 loopback 来源；非 loopback 在 CDP 探测或启动前被拒绝。
- 恢复后端基线：百炼模型期望、知识来源默认阈值和 Qdrant 访问过滤形态。
- 迁移账号/租户 CLI 的 PostgreSQL 连接也统一归一化驱动方言，避免 Compose/CI DSN 在 psycopg 边界失败。
- Alembic 迁移版本链、SHA-256 manifest、真实 schema invariant 校验，并接入 CI/Docker smoke。
- PostgreSQL、Qdrant snapshot、上传/解析文件的带 manifest 备份、校验、保留期清理和临时目标恢复演练。
- Qdrant 固定为 `qdrant/qdrant:v1.19.0@sha256:057ee3a8da769fe7310dd3537b4dc7583bf87a95ce8ac43c0af5a46bc580d1fc`；`security-audit` 失败会阻断 CI。
- 修正 Qdrant 1.19 本地 snapshot 恢复协议：使用 `/qdrant/snapshots`、`PUT` 和 `file:///...` location，与 [Qdrant snapshot 文档](https://qdrant.tech/documentation/operations/snapshots/)一致。

## 自动化验证

| 检查 | 命令/证据 | 结果 |
|---|---|---|
| 后端全量 | `F:\\Python_develop\\miniconda3\\envs\\careercrew\\python.exe -m pytest -q` | 退出码 0，100% 完成，0 failures；仅有本地 Qdrant payload-index warning |
| 迁移/备份/迁移 CLI 单测 | `pytest -q tests/unit/test_validate_migrations.py tests/unit/test_backup_restore.py tests/unit/test_account_migration.py tests/unit/test_tenant_migration.py` | 63 passed |
| 迁移静态校验 | `python scripts/validate_migrations.py --static` | `migration static validation: OK` |
| 真实 schema 校验 | `python scripts/validate_migrations.py --database-url <本地开发库>` | `migration schema validation: OK` |
| Phase 6 Ruff | `ruff check scripts/backup_restore.py scripts/validate_migrations.py tests/unit/test_backup_restore.py tests/unit/test_validate_migrations.py` | `All checks passed!` |
| Python 编译 | `python -m compileall -q careercrew_api careercrew_core careercrew_ai scripts` | 通过 |
| 前端 lint | `npm run lint` | 退出码 0；8 条已有 warning，无 error |
| 前端单测 | `npm run test` | 50 files / 201 tests passed |
| 前端类型与构建 | `npm run build` | `tsc -b` 与 Vite build 均通过；仓库没有独立 `npm run typecheck` script |
| Compose/CI | `docker compose config -q`；CI YAML safe-load | 均通过 |
| 工作树 | `git diff --check` | 通过；Phase 5 既有 dirty 文件未触碰、未暂存 |
| 独立复审 | 当前 HEAD `ba8a605` | 已批准；未发现 Critical/Important 问题 |

仓库级 Ruff 仍报告 13 个 Phase 5/legacy 文件的既有错误；本期脚本和测试文件已单独通过 Ruff，本期没有扩大修改范围。

## 真实备份与恢复演练

使用本地 Docker PostgreSQL/Qdrant 完成一次真实 `create`、`verify` 和 `restore-drill`。宿主机没有 `pg_dump`/`pg_restore`，因此演练适配器调用了 PostgreSQL 16 容器内的客户端；备份工具本身仍使用解析后的 DSN 和子进程环境传递密码，不把密码放进 argv、manifest 或输出；直接调用 psycopg 前会归一化 `postgresql+psycopg://` 方言。

- 生成目录：`%TEMP%\\careercrew-real-backup-4f55806b59944769b1c7a0bd58c9ebdf\\careercrew-20260910-020000`。
- manifest：4 个 artifact、2 个 Qdrant collection、保留期 30 天；manifest SHA-256 为 `4a78260434bd91355cc4b9b91f15dc110a87a8a929878beaa92a5ca50f7bc73b8`。
- `verify` 通过；使用 `postgresql+psycopg://` DSN 且自动解析本机 Qdrant 容器完成恢复，实际目标为 `careercrew_restore_20260910100000_a13585c4e1e9bbf2`，PostgreSQL restore exit 为 0。
- 演练结束后临时 PostgreSQL 数据库为 0 个，临时 Qdrant collection 为 0 个，`/qdrant/snapshots` 下无遗留 snapshot，容器 `/tmp` 下无遗留 dump。
- 恢复演练优先读取 `docker compose ps -q qdrant` 的实际容器 ID；非 Compose 本机服务再回退到精确名称 `qdrant`，不依赖 `<project>-qdrant-1` 字面名称。
- 原有 `data/backups/careercrew-pre-0008-20260909-101905.dump` 未触碰。生成的临时备份根保留在系统 Temp 中供复核，未写入仓库。

当前数据承载 Qdrant 集合仍为 `careercrew_episodic_v2`（28 points）和 `careercrew_mm`（2 points），状态均为 green。

## 浏览器与接口验收

本地 FastAPI `127.0.0.1:8000` 与 Vite `127.0.0.1:5175` 启动后，用 Playwright 1.63 实际打开页面：

- 页面 HTTP 200，页面标题区域显示 `CareerCrew`，无 `Internal Server Error`。
- 页面内 fetch `/api/browser/cdp-status` 与 `/api/browser/cdp-command` 均返回 JSON 401，响应不是 SPA HTML fallback。
- `tests/unit/test_browser_router.py` 的 12 个测试覆盖：已认证 loopback 成功、三条路由未认证 401、三条路由 non-loopback 403 且不触发 CDP、IPv4-mapped loopback 兼容；全部通过。
- 页面截图：`C:\\Users\\86186\\.codex\\visualizations\\2026\\09\\09\\01a08588-557f-76c3-85a6-7d1c923a273f\\phase6-browser-acceptance.png`。

当前开发库已有账号但没有可安全复用的已知密码；为避免猜密码触发限流或改写账号，未进行真实登录页面的 authenticated 200 操作。认证成功路径和每个端点的授权边界已由 TestClient 覆盖，真实浏览器侧完成匿名页面/401/fallback 验收。

## 工作树与环境说明

- Qdrant 本地运行容器已重建为固定 tag+digest，继续使用原 `qdrant_storage` named volume；两个原有集合保持可读。
- `BACKUP_ROOT` 会拒绝与 `data/uploads`、`data/parsed` 或传入源目录存在祖先/子孙关系，避免把备份写进归档源。
- 恢复演练在发出 `CREATE DATABASE` 前即登记清理责任；即使客户端在创建请求后异常，也会尝试 `DROP DATABASE ... WITH (FORCE)`。
- Compose 验证时曾产生一个空的项目级 Qdrant 容器/卷，已仅清理该次产生的空容器和空卷；没有删除数据卷。
- 没有注册真实 Windows Task Scheduler 任务；安装脚本和 operator 文档已交付，生产环境按明确 Python 路径注册。
- 最后一次独立复审批准当前 HEAD，未发现 Critical/Important 问题；剩余风险已记录为非阻断项。
- 未修改生产数据库、未 push、未 merge。
