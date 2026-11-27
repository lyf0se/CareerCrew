# CareerCrew 自动备份与恢复演练

备份任务覆盖 PostgreSQL、Qdrant 向量集合以及 `data/uploads`、`data/parsed`。每次运行写入一个带 UTC 时间戳的目录，并生成 `careercrew-backup-v2` `manifest.json`；清单记录每个 dump、snapshot、压缩包以及压缩包内每个上传/解析文件的大小与 SHA-256。默认保留 30 天。

## 配置

将 `.env.example` 复制为 `.env` 后填写真实环境值，不要把密码写入命令行或提交到 Git：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `DATABASE_URL` | — | PostgreSQL 连接串，备份程序从中派生主机、端口、用户和密码 |
| `QDRANT_URL` | `http://127.0.0.1:6333` | Qdrant API 地址 |
| `QDRANT_COLLECTIONS` | `careercrew_mm,careercrew_episodic_v2,careercrew_workspace_messages` | 要创建 snapshot 的集合；`careercrew_workspace_messages` 尚未创建时会安全跳过 |
| `BACKUP_ROOT` | `data/backups` | 备份输出目录 |
| `BACKUP_RETENTION_DAYS` | `30` | 自动清理周期 |
| `RESTORE_DATABASE_URL` | — | `restore-drill` 的隔离 PostgreSQL 目标；不会回退到源库 |
| `RESTORE_QDRANT_URL` | — | `restore-drill` 的隔离 Qdrant 地址；有 Qdrant artifact 时必填 |
| `RESTORE_QDRANT_CONTAINER` | — | 仅 Docker 文件恢复时使用的隔离 Qdrant 容器名；不设置则使用 snapshot upload API |
| `RESTORE_QDRANT_API_KEY` | — | 访问隔离恢复 Qdrant 的密钥；不会回退到源端密钥 |

备份主机需要 `pg_dump`；验证若系统存在 `pg_restore` 会执行 `pg_restore --list`。创建 Qdrant snapshot 需要对应 API 可用。

## 日常创建、验证与保留

在仓库根目录执行：

```powershell
$env:DATABASE_URL = "postgresql://<user>:<password>@<host>:5432/careercrew"
python scripts/backup_restore.py create
python scripts/backup_restore.py verify data/backups/careercrew-20260909-020000
```

`create` 会：

1. 通过 `PGPASSWORD` 子进程环境调用 `pg_dump`，密码不会出现在 argv、清单或输出中。
2. 为配置的每个 Qdrant 集合创建并下载 snapshot，然后尽力删除服务端临时 snapshot。
3. 只压缩 `data/uploads` 和 `data/parsed`，写入安全的相对路径。
4. 生成清单并进行本地大小、哈希和 ZIP 完整性校验。
5. 只删除 `BACKUP_ROOT` 下名称严格匹配 `careercrew-YYYYMMDD-HHMMSS` 且超过保留期的直接子目录。

## Windows 定时任务

默认每天 02:00 执行，不在安装过程中执行备份：

```powershell
.\scripts\install_backup_schedule.ps1 `
  -PythonPath "<仓库根目录>\.venv\Scripts\python.exe" `
  -RepositoryRoot "<仓库根目录>"
```

也可以显式指定 `-TaskName`、`-DailyTime HH:mm` 和 `-RetentionDays`。任务动作只包含 Python 路径、脚本路径和保留天数；连接串仍从任务运行环境的 `.env`/环境变量读取。脚本默认不会覆盖同名计划任务；确认需要替换时才追加 `-AllowOverwrite`。

## 恢复演练

恢复演练只使用自动生成的临时目标，不覆盖源库、源向量集合或源文件：

```powershell
# 把隔离 RESTORE_DATABASE_URL 放入任务运行环境，不作为命令行参数传入
python scripts/backup_restore.py restore-drill `
  data/backups/careercrew-20260909-020000 `
  --qdrant-url $env:RESTORE_QDRANT_URL
```

演练会先验证清单和所有 artifact，再创建形如 `careercrew_restore_<UTC timestamp>` 的临时 PostgreSQL 数据库，恢复 dump 并执行 schema invariant、代表性表计数和跨表关系 canary；文件压缩包解压到临时目录后逐个核对清单中的路径、大小与 SHA-256。指定 `--qdrant-container` 时，snapshot 会恢复到带 `__restore__` 后缀的临时集合并核对点数；不指定容器名时使用 Qdrant 的 snapshot upload API，可对托管 Qdrant 做隔离恢复。数据库、临时集合和容器内 snapshot 文件都会在成功或失败路径尝试清理。

发布验收通过 `RESTORE_DATABASE_URL`、`RESTORE_QDRANT_URL` 和 `RESTORE_QDRANT_API_KEY` 指向隔离恢复目标；生产源端点和恢复端点必须分离，恢复密钥也不得复用源端密钥配置。只有使用 Docker 文件恢复时才设置 `RESTORE_QDRANT_CONTAINER`。

演练成功输出临时目标已清理；失败返回非零退出码。发现清理异常时，应先暂停应用并人工确认临时目标，再重新执行清理。

## 例行检查与恢复流程

- 每日查看定时任务结果，并至少执行一次 `verify`；校验失败的目录不得用于恢复。
- 每周或发布前执行一次 `restore-drill`，记录成功时间、数据库 `SELECT 1`、Qdrant 点数和清理结果。
- 真正灾难恢复时先停止应用，选择已通过 `verify` 的备份，按演练方式恢复到隔离目标并核对用户数、知识库点数和上传文件，再由管理员安排切换。
- 备份目录应位于独立磁盘或由主机级备份系统再次保护；本脚本不上传到远端对象存储。

认证库、会话、记忆、线程、审计和业务表均在 `DATABASE_URL` 指向的 PostgreSQL dump 中；Qdrant 集合名称由 `QDRANT_COLLECTIONS` 明确控制。备份脚本只负责生成并校验本地 artifact，不负责上传对象存储；生产验收还必须提供与实际 `manifest.json` SHA-256 绑定的加密、异地、不可变介质证据，并完成隔离目标恢复演练。
