# Phase 7 知识与智能质量闭环验收

更新时间：2026-09-11

## 验收范围

本报告覆盖第七期的真实模型评测、长期记忆治理、知识库治理、成本预算、工具治理和基础观测能力。所有新增能力继续遵守“AI 草案、人工确认、完整留痕”，没有增加自动投递、自动联系 HR 或未经确认的外部副作用。

## 交付矩阵

| 能力 | 结果 | 证据 |
|---|---|---|
| 真实模型评测 | 已实现产品 runtime 评测、严格 --require-real、固定 JSONL 数据集、模型/Prompt 实验元数据、回归门禁；受保护真实调用尚未执行 | scripts/eval_runner.py、data/eval/README.md、tests/unit/test_eval_runner.py、.github/workflows/ci.yml |
| 长期记忆纠错 | 已实现确认、修改、暂时忽略、过期、冲突合并、版本冲突和历史事件 | careercrew_core/memory/governance.py、careercrew_api/routers/memory_governance.py |
| 知识内容治理 | 已实现文档版本、SHA-256 重复检测、分块预览、单分块编辑、失效日期、可信度、重新索引状态和引用命中统计 | careercrew_core/knowledge/governance.py、careercrew_api/routers/knowledge_governance.py |
| 成本治理 | 已实现不可变 Token/费用事件、日/月预算、并发预留、未知价格 fail-closed 和降级建议 | careercrew_core/usage/ledger.py、careercrew_api/routers/usage.py |
| 运维观测 | 已实现低基数 Prometheus 指标，覆盖 HTTP、LLM、RAG、上传、工具和 SSE；生产 scrape 要求服务令牌 | careercrew_core/observability/metrics.py、careercrew_api/routers/metrics.py |
| 工具治理 | 已实现状态、有效权限、脱敏调用历史、失败分类、管理员开关和审计；策略存储不可用时请求级 fail-closed | careercrew_core/tools/operations.py、careercrew_api/routers/tools.py |
| 数据库 owner/知识源约束 | 0016 为工作台跨资源关系增加 (resource_id, owner_id) 组合 FK；0017 固化原始知识源 hash/size，避免分块编辑破坏重复检测 | migrations/versions/0016_workspace_owner_integrity.py、migrations/versions/0017_knowledge_source_identity.py |

## 本地验证

- Phase 7 聚焦测试：记忆、知识、用量、预算、指标、工具及 API 均通过。
- scripts/validate_migrations.py --static：通过，当前 head 为 0017_knowledge_source_identity。
- 前端完整回归：51 个测试文件、214 个测试通过；Workspace 工作台测试 3 个通过。
- 前端 npm run lint：退出码 0；保留既有 hook/Fast Refresh warning，无 error。
- 前端 npm run build：退出码 0。
- 后端 `python -m pytest -q`：本轮退出码 1；除 `tests/api/test_user_settings_api.py::test_apikey_settings_crud_lifecycle` 外均通过。该失败是本机 PostgreSQL `localhost:5432` 不可用，不作为代码全绿证据。
- `scripts/release_rehearsal.py`：0017 加入后未在本机重跑；此前 0016 链上的临时库 A/B/C/D 合成演练通过，当前 Docker 不可用，因此不把历史结果升级为 0017 live 证据。
- 本地 Docker PostgreSQL/Qdrant + BGE-M3 语义冒烟通过；见 [跨会话语义搜索验收记录](2026-09-10-cross-session-semantic-search-verification.md)。

## 未能在本机完成的验收

1. 真实模型评测：本机没有受保护的供应商凭据/发布数据集环境；`--real --runtime --allow-skip` 仅记录 fail-closed skipped，不能作为真实模型质量结论。发布流水线必须在受保护 environment 中使用 `--require-real --fail-on-regression`。
2. 生产 PostgreSQL/Qdrant：本轮未执行生产迁移、重索引、容量、故障切换和恢复；统一门禁要求独立恢复目标、shadow collection、canary、cutover 和清理证据。
3. 生产备份恢复：脚本和合成恢复路径已有校验；本机没有可验证的生产 backup run，且 Docker 当前不可用，因此真实备份介质、保留/不可变策略和跨节点恢复仍未验收。
4. 广泛代码审查：已完成针对本轮改动的独立只读定向审查和本地静态检查；独立 reviewer 工具不可用，不能把全仓库广泛审查标记为完成。

## 安全复核结论

- 新 API 均通过当前用户依赖，管理动作使用管理员依赖；数据库关系又增加 owner 组合约束。
- Prometheus 在配置 CAREERCREW_METRICS_TOKEN 时要求 Bearer token；生产/预发布未配置令牌时 fail-closed。
- 工具调用和导出失败信息只返回分类/用户可读摘要，不回显原始异常、路径、凭据或 raw tool arguments。
- usage、metrics、audit 继续使用低基数和脱敏字段，不保存 JD、简历、Prompt、回答或密钥。

## 发布判定

第七期代码、自动化测试、单分块治理 UI 和 fail-closed 验收编排已达到待发布状态；受保护真实模型质量、生产 PostgreSQL/Qdrant、生产备份介质、运维恢复和广泛代码审查仍是未完成门禁，不在本报告中宣称已通过。
