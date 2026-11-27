# CareerCrew 第六至八期完成报告

- 报告时间：2026-09-16
- 提交基线：`3cdc88b feat: finalize phase six to eight release gates` 及其后续修复提交
- 结论：第六、七、八期的功能与本地发布门禁已全部可复核；生产环境验收与受保护
  真实模型评测仍按门禁保持未通过，原因见"仍未完成"一节。

## 一、交付范围

第六期（全局稳定性治理）

- 浏览器岗位采集路由：补鉴权与本机来源限制后注册到主应用，并纳入浏览器验收。
- 迁移防漂移：`scripts/validate_migrations.py` 校验修订图、校验和清单与真实
  schema invariant；发布演练覆盖空库升级、旧版本升级、失败回滚恢复三条路径。
- 备份恢复：`scripts/backup_restore.py` 生成 PostgreSQL dump、Qdrant 快照与
  上传/解析文件归档，含 SHA-256 清单、保留周期与隔离恢复演练；主机缺少
  `pg_dump`/`pg_restore` 时回退到容器内执行（argv 不携带密码）。
- 发布门禁：`scripts/release_acceptance.py` 统一编排静态/在线迁移校验、Qdrant
  健康与所有权检查、备份校验、隔离恢复演练与受保护真实模型评测；证据回执必须
  绑定数据库/Qdrant 端点身份，缺失凭据一律 fail-closed。

第七期（知识与智能质量闭环）

- 知识库治理：文档版本、重复检测、来源可信度、失效日期、引用命中统计、
  分块预览与**单分块编辑**、重新索引与下架/发布对称。
- 长期记忆治理：确认、修改、忽略、过期、冲突合并与变更历史。
- 成本治理：按用户/模块的用量与预算控制、模型降级规则与异常消耗告警。
- 可观测性：Prometheus 指标（请求延迟、SSE 中断、LLM 失败、Qdrant 命中率等）。

第八期（工作台与协作增强）

- 跨会话语义检索、消息书签与分支、回答转行动项、会诊决策报告与方案对比、
  简历母版管理、工具中心状态与调用记录。

## 二、验证证据（本地，全部真实组件）

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 后端全量测试 | 1375 通过 / 0 失败 / 1 跳过 | `data/reports/backend-20260916-final2.xml`（一次性 PostgreSQL 库，跑完整库删除） |
| 前端 | 214 测试通过、lint 0 error、生产构建成功 | `careercrew_web` 本地运行记录 |
| 迁移静态校验 | 通过 | `python scripts/validate_migrations.py --static` |
| 迁移在线校验 | 通过（head `0018_eval_case_updated_at`） | `--database-url` 真实 schema invariant |
| 迁移演练（四路径） | 全部通过 | `data/reports/release-rehearsal-20260915.md` |
| 真实 Docker 备份恢复演练 | 通过 | `tests/integration/test_backup_restore_docker_live.py`（容器内 pg_dump/pg_restore + 真实 Qdrant 快照上传、校验与清理） |
| 本地发布验收 | `rehearsal_passed` | `data/reports/local-release-acceptance-20260916.json`；含恢复演练：92 个文件 SHA-256 校验、临时库 canary 关系全 0、临时资源清理 |
| 开发库备份 | 5 个产物 / 92 个归档文件 / 3 个集合快照 | `data/backups/careercrew-20260916-143626` |

跳过项说明：唯一 skipped 是需要 `CAREERCREW_DOCKER_BACKUP_TEST=1` 显式开启的
Docker 备份演练；该演练已单独运行并通过。

## 三、本轮修复的真实缺陷

1. `eval_cases.updated_at` 只存在于运行时惰性 DDL，编辑/审批会写不存在的列。
   新增迁移 `0018_eval_case_updated_at`，并同步校验和清单与 EXPECTED_HEAD。
2. 评测用例行返回 UUID 导致审计 JSON 序列化失败（`_eval_case_row` 统一转字符串）。
3. alembic 基线对比把迁移专用表算进差异，造成误报（收敛为运行时表对比）。
4. 知识文档下架后治理接口仍可读取；冷启动缺向量后端时不报错；重复上传
   draft/failed 版本不重新索引；分块编辑后旧投影未整体失效——均已修复并加回归测试。
5. 账号删除只清理旧记忆表，**不清理** `memory_records` 长期记忆与任何向量副本，
   留下可检索孤儿数据。新增 `LongTermMemoryRepository.delete_all_for_user`
   与路由层向量清理（episodic / knowledge / workspace 三个集合），失败即中止删号
   可重试；治理审计 append-only，因此有审计事件时按软删保留审计链。
6. 所有权 dry-run 会创建快照（非只读）、报告可不覆盖必需集合、备份恢复沿用
   继承的 libpq 路由变量、CI 取消会打断受保护恢复——均已修复。

## 四、事故与恢复记录（本地开发库）

- 现象：`tests/api/test_auth_api.py` 的删号测试会解析**真实全局 runtime**，
  因此实际清理了开发库 `u_001` 的会话/记忆，并在本轮新增向量清理后删除了
  `careercrew_mm` 中该账号的 2 个知识向量。
- 影响：`u_001` 的 28 条长期记忆行与其 2 个知识向量被删除；会话在此之前已被
  更早的测试运行清理（既有行为）。
- 恢复：从当日 11:58 备份定点回填 28 条记忆记录及其来源/关系/队列行；从
  Qdrant 快照恢复 2 个知识向量点；恢复后 `memory_records=28`、`mm=2`。
- 根因修复：为该测试模块加 autouse 假运行时夹具，删号路径不再接触真实服务；
  并加入哨兵行 + Qdrant 删除计数验证（运行前后均为 3，无新增删除）。
- 复核：修复后再次跑全量测试，开发库数据保持不变。

## 五、仍未完成（门禁保持未通过）

1. 生产环境迁移/重索引/备份介质/恢复演练：**已按决策移除**。它需要真实生产目标、
   备份介质证据、重索引与 canary/cutover 证据以及外部证据验证服务，本项目当前
   无法提供；相关代码（production target、evidence verifier、介质/重索引回执）
   已删除，`release_acceptance.py` 只保留回环地址的本地预演。
2. 受保护真实模型评测：**已按决策移除**。它需要 `eval_` 专用租户的外部
   attestation 与可证明的独占写入租约（证明清理后没有在途 worker 重新写入向量），
   两者当前都无法实现；相关代码（runtime 评测会话、attestation 校验、
   `--real/--runtime/--require-real/--model-probe`、`scripts/deployment_identity.py`）
   已删除，评测只保留离线 fixture 回归。
3. CI lint 仍有 25 项既有错误（13 个文件，均为 main 上存量：`E402`/`F841`/`E731`
   等），与本次改动无关；CI 的 lint job 在合并前需要单独清理。
4. 知识库/工作台的账号删除尚未覆盖全部业务表；本次只补齐长期记忆与向量副本，
   其余表仍依赖运行时可用时的既有清理路径。

## 六、复现方式

```powershell
# 后端全量（一次性库，跑完自动删除）
python -c "..."            # 见本轮会话中的 disposable-db runner
python scripts/validate_migrations.py --static
python scripts/release_rehearsal.py --report data/reports/release-rehearsal-20260915.md

# 本地发布验收（含隔离恢复演练）
$env:CAREERCREW_RELEASE_RESTORE_DRILL = "1"
$env:RESTORE_DATABASE_URL = "postgresql://careercrew:careercrew@localhost:5432/careercrew_restore_control"
$env:RESTORE_QDRANT_URL = "http://127.0.0.1:6333"
python scripts/release_acceptance.py --target local `
  --backup-dir data/backups/careercrew-20260916-143626 `
  --report data/reports/local-release-acceptance-20260916.json
```
