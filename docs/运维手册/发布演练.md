# CareerCrew 发布演练

- 执行时间：2026-09-10T16:43:36+00:00
- 运行前缀：`careercrew_rehearsal_20260910164336_85a4045c`
- PostgreSQL 容器：`postgres`
- 迁移 head：`0016_workspace_owner_integrity`
- 受保护源数据库：`careercrew`（仅从 DSN 派生名称，未读取或修改）
- 用时：41.05 秒
- 结论：全部通过

| 结果 | 演练项 | 证据 |
| --- | --- | --- |
| 通过 | 路径 A：空库 → head | version=0016_workspace_owner_integrity, tables=70 |
| 通过 | 路径 B：0002 → head | mid=0002_long_term_memory_records, final=0016_workspace_owner_integrity |
| 通过 | 路径 C：失败迁移回滚与恢复 | before=0007_version_attribution_shares, after_failure=0007_version_attribution_shares, recovered=0016_workspace_owner_integrity |
| 通过 | 路径 D：合成数据备份恢复 | rows=1, payload=synthetic-release-rehearsal |

备份恢复只使用临时库中的固定合成探针行，真实开发库数据未进入备份。
失败迁移写入系统临时目录中的迁移副本，仓库 `migrations/` 未被注入演练文件。
所有数据库名都必须匹配本次唯一前缀；退出和异常路径均执行清理。
