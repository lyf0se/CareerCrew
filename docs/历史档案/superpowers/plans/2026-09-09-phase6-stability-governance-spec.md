# CareerCrew 第六期全局稳定性治理规格

## 目标

在不改变“AI 准备、人工确认、完整留痕”边界的前提下，恢复后端测试全绿基线，封闭本地浏览器采集接口的访问边界，防止已发布 Alembic 迁移漂移，固定生产依赖镜像，并把 PostgreSQL、Qdrant 与上传文件纳入可校验的自动备份和恢复演练。

## 范围

1. `GET/POST /api/browser/*` 必须同时要求有效登录和 loopback 请求来源；路由必须注册到主应用；非本机请求不得触发 CDP 探测或 Chrome 启动。
2. 现有后端全量测试中的 8 个失败必须恢复：浏览器路由、阿里云百炼配置期望、知识来源默认分数阈值、Qdrant 纯访问过滤形态。
3. 迁移校验必须检查版本链、已发布迁移文件 SHA-256 manifest，以及真实数据库的 head、核心表/列、`pg_trgm` 和 trigram 索引不变量。
4. `docker-compose.yml` 中 Qdrant 必须固定到 `qdrant/qdrant:v1.19.0@sha256:057ee3a8da769fe7310dd3537b4dc7583bf87a95ce8ac43c0af5a46bc580d1fc`；CI 的依赖安全扫描失败必须阻断工作流。
5. 备份工具必须从 `DATABASE_URL` 派生 PostgreSQL 连接参数，不把密码写入命令行或日志；默认备份 `data/uploads`、`data/parsed` 和 `careercrew_mm`、`careercrew_episodic_v2`，保存 manifest、文件大小和 SHA-256，默认保留 30 天，并提供只对命名临时目标执行的恢复演练。
6. 不实现第七、八期 P1/P2 功能；不 push、merge 或修改生产数据库。

## 验收

- `F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest -q` 退出码为 0，且无失败项。
- 迁移静态校验与真实 PostgreSQL schema 校验均通过；篡改 manifest、漏列、错误 head 或缺索引时测试失败。
- `docker compose config -q` 通过，Qdrant image 同时包含不可变版本 tag 和 digest；CI YAML 不再允许 security-audit 失败后继续。
- 备份单测覆盖密码不出现在 argv/log、manifest 校验、保留期清理、路径穿越拒绝和恢复目标保护；本地 Docker 环境完成一次真实备份验证和 PostgreSQL/Qdrant 恢复探针。
- 受控浏览器或 Playwright 验收证明：登录用户从本机可以读取 CDP 状态；未登录、非 loopback 和未注册路由均得到预期拒绝/成功响应；已有岗位页的 CDP 状态条不再收到 404。
