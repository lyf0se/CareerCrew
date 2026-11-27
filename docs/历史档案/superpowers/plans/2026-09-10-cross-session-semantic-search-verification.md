# 跨会话语义搜索验收记录

更新时间：2026-09-11

## 结论

跨会话搜索已从默认 `text_fallback` 接入独立的 BGE-M3 + Qdrant 语义投影；消息表仍是权限和正文的唯一来源。模型或 Qdrant 不可用时，接口继续降级为原有全文搜索。本地代码、自动化测试和一次真实 Docker 语义冒烟通过，但这不是生产发布批准：受保护的真实模型质量集、生产数据库迁移和生产恢复演练仍需在对应环境执行。

## 本地验证

| 检查 | 结果 | 证据 |
|---|---|---|
| 语义索引与 owner/type 过滤 | 通过，聚焦 22 passed | `tests/unit/test_workspace_semantic_search.py`、`tests/unit/test_runtime_workspace_semantic_search.py`、`tests/unit/test_workspace_traceability.py`、`tests/unit/test_qdrant_store.py` |
| 后端聚焦 | 通过；仓库全量仅剩 1 个本机 PostgreSQL 环境失败 | `python -m pytest tests/unit/test_release_acceptance.py tests/unit/test_backup_restore.py tests/unit/test_eval_runner.py tests/unit/test_memory_records.py -q` |
| 前端回归 | 51 个测试文件、214 个测试通过 | `careercrew_web/npm run test` |
| 迁移静态校验 | 通过 | `scripts/validate_migrations.py --static`，head=`0017_knowledge_source_identity` |
| 发布恢复演练 | 退出码 0 | `scripts/release_rehearsal.py`，临时库 A/B/C/D 四路径通过 |

后端全量本轮退出码为 1，唯一失败为 `tests/api/test_user_settings_api.py::test_apikey_settings_crud_lifecycle`，原因是本机 `localhost:5432` PostgreSQL 不可用；不能据此宣称后端全绿。Qdrant 真实服务路径和本地 Docker 冒烟也未在本轮重跑。

## 真实本地语义冒烟

使用本地 Docker PostgreSQL/Qdrant 和已配置的 BGE-M3 模型，在临时账号、临时会话和临时消息上执行：

- 返回 `mode=embedding`、`total=1`，命中当前 owner 的目标消息；
- 另一 owner 的同文本消息没有进入结果；Qdrant 查询同时使用 `owner_user_id` 与 `record_type=conversation_message`；
- 删除临时消息后，源表行数和 owner 过滤下的 Qdrant 点数均为 0；临时账号、会话和向量已清理；
- 另有单测覆盖 embedding/Qdrant 异常时返回 `text_fallback`。

新增集合为 `careercrew_workspace_messages`，与知识库和长期记忆集合隔离；备份脚本将其列入默认集合，但在尚未发生过语义搜索、集合不存在时按可选集合跳过，并在 manifest 中不伪造备份对象。

## 尚未通过的发布门禁

1. `scripts/eval_runner.py --require-real --fail-on-regression` 需要受保护的供应商凭据、固定发布数据集和 CI/预发布模型环境；本机没有执行真实模型质量结论。
2. 当前证据是本地 Docker 和临时库演练，不等同于生产 PostgreSQL/Qdrant 的迁移、重索引、备份恢复和容量/故障演练。
3. 语义索引是可重建投影；正文、owner 和删除状态仍必须以 PostgreSQL 复核，不能把向量命中当作授权凭据。
