# 多模态 RAG（MCP 化）全面替换现有 RAG 体系 — 实施规格 v1.1（修正版）

> 基于 v1 评审修正。修订要点见 §0；其余结构与 v1 保持一致，方便对照。

> **v1.2 修订（实施后定稿）**：移除本地 ColQwen 视觉检索模型（8GB 显存机型实测
> 单次编码 30s+ 且多进程叠加打满 GPU）。最终方案：**图片内容由 MinerU 抽取为文本
> （OCR/Markdown），统一走 BGE-M3 文本向量**；页面图/对象图路径保留在 payload，
> 回答阶段由 Qwen3-VL-8B API 看图生成。检索侧无任何本地视觉模型，GPU 负载≈0。
> 具体变化：Qdrant collection 恢复 text_dense+text_sparse 双向量 schema（无 visual）；
> `MultimodalSearch` 只有文本路（图片查询经 VLM API 提取文本后入文本路）；
> `rag.multimodal` 配置段删除；ColQwen/SigLIP2 均不在运行时使用。

> **v1.3 修订（2026-08-11，云端 API 化定稿）**：MinerU 解析从本地子进程切换为
> **MinerU 官方精准解析 API**（`https://mineru.net/api/v4/file-urls/batch` 上传 →
> 轮询 `GET /api/v4/extract-results/batch/{batch_id}` → 下载 zip 解压），本机零推理负载，
> 彻底消除 8GB 显存 OOM 与 CPU 慢解析。新增 `rag.loaders.provider: api|local`
> （默认 `api`）、`api_key`（`MINERU_API_KEY`）、`model_version`（默认 `vlm`）、
> `poll_interval` / `timeout` / `table` / `language`；本地 loader 保留为 `local`
> 可选回退。新增 `careercrew_core/rag/loaders/mineru_api_loader.py`，产物组装
> 抽到 `mineru_common.py` 与本地 loader 共用。实现要点：
> - OSS 上传 TLS 需关闭 ALPN（urllib3 默认协商 h2 触发 SSL EOF），已内置默认 Session；
> - 上传/轮询/下载带指数退避重试（云端间歇断连）；
> - 批量结果状态含 `waiting-file`（上传后排队）等活跃态；
> - zip 解压做路径穿越防护；解析失败统一 `ParsingError` → `doc_type=error` 跳过。

## 0. 修订说明（相对 v1 的关键决策）

**R1 · MCP SDK 锁定 1.x，用 FastMCP。** 实测当前环境 `mcp==2.0.0` 已移除
`mcp.server.fastmcp`（FastMCP 类不存在，新 API 是 `mcp.server.mcpserver.MCPServer` +
`mcp.server.apps`）。本规格选择：

- 安装 `mcp>=1.20,<2`（1.29.0 可用），沿用 FastMCP 的
  `@mcp.tool()` / `transport="stdio"|"streamable-http"` 写法，双传输开箱即用。
- 现有 `mcp.client.stdio` / `mcp.server.lowlevel` 用法在 1.x 完全兼容，MCP 客户端
  （`careercrew_core/tools/mcp/mcp_client.py`）与测试 mock server 无需改动。
- 若实施时坚持 mcp 2.0，则 server 改用 `MCPServer(tools=[...])` 新 API，
  **二选一，不可混用**（决策记录写入 ADR）。

**R2 · MarkItDown 全量移除，resume 上传改走 MinerU。**
`careercrew_api/routers/resume.py` 的简历上传路径（`rt.load_document` →
`loader_factory` → `MarkItDownLoader`）是 MarkItDown 的活消费者，直接删 loader 会断掉
PDF/docx 简历上传。修正：resume 上传与 RAG 统一走 MinerU loader（MinerU 支持
pdf/image/docx/pptx/xlsx），解析失败返回 `doc_type=error` 不崩；`pyproject.toml` 的
`ingestion` / `web` extra 同步移除 markitdown。

**R3 · 设备与内存分配明确（8GB 显存预算）。**

| 组件 | 设备 | 说明 |
|---|---|---|
| BGE-M3 | CPU | 维持现状 `use_fp16: false`，常驻进程 |
| ColQwen2.5（base+adapter） | GPU bf16 | 独占显存；batch 先按 1 验证，显存不足则编码完即卸载 |
| MinerU | 子进程/CLI | `mineru -b pipeline` 以独立进程跑，解析完进程退出、模型释放，不与 ColQwen 同进程争显存 |
| Qwen3-VL 生成/精排 | API（硅基流动） | 本地不占显存 |

**R4 · 融合统一为客户端加权 RRF。** Qdrant 服务端 prefetch 的 RRF 不支持按路加权，
与"图片查询 visual 路权重提升"矛盾。修正：**一次 Qdrant Query API 三路 prefetch 取回
各路上 top_m → 客户端 `rrf_fuse`（扩展 weights 参数）加权融合**。单次往返 + 权重可控，
并复用现有 `careercrew_core/rag/retrieval/fusion.py`。

**R5 · QdrantStore 双 schema、collection 感知。** `careercrew_mm`（三向量）与
`careercrew_episodic`（纯文本两向量）schema 不同，`QdrantStore` 按 collection 名自动
路由建库/查询，记忆系统（`VectorIndex` 目前与知识库共用同一 store 实例）必须显式拿到
episodic collection 的 store（或带 collection 参数），否则会查错库。

**R6 · Loader 契约升级。** 现有 `BaseLoader.load() -> Document`（单一 text）装不下
MinerU 的「页面 + 对象」产出。新增 `ParsedDocument{pages[], objects[]}`；`BaseLoader`
保持原契约不动（Markdown 直读仍用它），MinerU loader 返回新类型，由
`MultimodalIngestionPipeline` 消费。

**R7 · SigLIP2 移出 v1 依赖。** 盘点中列了 SigLIP2 但架构未使用，v1 不引入；如需
对象区域编码备选可后续评估，不在本次范围。

**R8 · rag_query 图文输出契约。** agent 主 LLM 是纯文本 DeepSeek，图片路径塞进正文
无意义。修正：工具返回字符串**保持纯文本格式不变**，图片路径以
`[image: <绝对路径>]` 行附在末尾，Web/CLI 渲染层识别并展示，文本模型忽略；
MCP `query` 工具返回结构化 `sources`（含 `image_path`），外部系统直接展示。

**R9 · 测试计划修正。** QdrantStore roundtrip 依赖真实 Qdrant 容器，属集成测试并加
`skipif` 守卫；补充 payload 过滤/删除、`get_by_ids` 带向量回读、VL rerank 请求格式、
图片查询加权、episodic 回归、resume 上传回归等用例（见 §测试计划）。

---

## Summary

**已安装盘点（v1.2 定稿）**：torch 2.8.0+cu128（RTX 4060 已启用，但运行时不再依赖）、
transformers 4.57.6、FlagEmbedding（BGE-M3，CPU）、mineru 3.4.4[pipeline] + 模型
（2.4GB）、qdrant-client 1.19.0、pymupdf；Qdrant 1.19.0 已作为 Docker 服务常驻。
**需变更**：`mcp` 2.0.0 → 降级 `mcp>=1.20,<2`（R1）；卸载 pymilvus / markitdown。
可选但非必需：flash-attn（Windows 无官方轮子，v1 不做）；ColQwen/SigLIP2 已下载但
运行时不用（v1.2 移除视觉检索）。**实施后（v1.3）**：mineru / colpali-engine / peft
亦不在 `pyproject.toml` 依赖内（云端 API 用 `requests`，本地 loader 仅环境可选回退）。

**目标架构（v1.2）**：文档经 MinerU 解析为「页面文本 + 对象文本（OCR/Markdown）+
页面图/对象图路径」→ 文本统一用 BGE-M3 编码 dense+sparse → 写入 Qdrant（唯一向量库）
→ 查询时两路召回（文本 dense/sparse）客户端 RRF 融合 → Qwen3-VL-Reranker-8B 精排
（API）→ zai-org/GLM-4.5V 看图回答（API，读 payload 中的页面图）。整个能力
封装为 MCP server（stdio + HTTP 双传输，FastMCP，SDK 1.x），对外暴露
ingest/search/query/status。本地 GPU 视觉模型全部移除，推理负载≈0。

**完全替换**：删除 Milvus/Chroma 后端代码与相关测试；MarkItDown 从 RAG 链路与 resume
上传链路一并移除（R2）；settings 默认 `backend: qdrant`、`loaders.backend: mineru`；
旧 Milvus 数据不迁移（`data/db/milvus` 遗留目录手动清理），全部重新入库。

## 关键改动

### 配置层（`config/settings.yaml` + `careercrew_core/state/settings.py`）

```yaml
vector_store:
  backend: qdrant              # 合法值只剩 {"qdrant"}
  url: http://localhost:6333
  api_key: ""
  collections:
    knowledge: careercrew_mm
    episodic_memory: careercrew_episodic

rag:
  loaders:
    backend: mineru            # 合法值只剩 {"mineru"}
    provider: api              # api（云端精准解析，默认）| local（本地子进程回退）
    api_key: "${MINERU_API_KEY}"
    model_version: vlm         # pipeline | vlm（推荐）| MinerU-HTML
    poll_interval: 5
    timeout: 1800
    output_dir: ./data/parsed  # MinerU 产物落盘（页面图/对象裁剪图/Markdown）
    device: cpu                # 仅 provider=local 使用
    method: auto               # 仅 provider=local 使用
    formula: true
    table: true
    language: ch

vlm:
  model: zai-org/GLM-4.5V
  rerank_model: Qwen/Qwen3-VL-Reranker-8B
  base_url: https://api.siliconflow.cn/v1
  api_key: "${SILICONFLOW_API_KEY}"
```

- `settings.py`：`_VALID_VECTOR_BACKENDS = {"qdrant"}`（删除 milvus_lite /
  milvus_docker / chroma），`_VALID_LOADER_BACKENDS = {"mineru"}`（删除 markitdown /
  pymupdf / python-docx 合法值）；新增 `VLMSettings` 嵌套模型；`MultimodalSettings`
  不引入（v1.2 无视觉 Embedding 配置）；`VectorStoreSettings` 改为
  `backend/url/api_key/collections`（去掉 persist_path，迁移配置兼容在加载层做显式报错提示）。
- 语义校验新增：`vlm.api_key` 未设置时报错（复用 SILICONFLOW_API_KEY）；
  `rag.loaders.output_dir` 相对路径按项目根解析。
- 旧值 fail-fast：配置文件里仍写 `milvus_lite` / `markitdown` 时，
  `SettingsError` 信息给出迁移指引（"请改 qdrant / mineru"），不静默替换。

### 向量层（`careercrew_ai/vector_store/qdrant_store.py`）

实现 `BaseVectorStore` 契约 + `count()` + `list_docs()`（按 `payload.doc` 聚合列库内
文档，知识库管理用）；删除 `milvus_store.py`、`chroma_store.py` 及工厂注册
（`create_vector_store` 只留 fake/qdrant 两路）。

**Collection `careercrew_mm`**（点 = 一个页面单元或对象单元，v1.2 纯文本双向量）：

| named vector | 类型 | 说明 |
|---|---|---|
| `text_dense` | dense, 1024 维, Cosine | BGE-M3 dense |
| `text_sparse` | sparse, IP | BGE-M3 稀疏权重（Qdrant 稀疏向量无需声明维度） |

payload：`text`、`image_path`、`type(page|object)`、`doc`、`page`、`source`、
`bbox`（object 可选，MinerU 对象框；image_path 仅供 VLM 看图回答展示，不参与检索）。
payload index：`doc`、`type`、`page`、`source`（过滤与 `delete_by_metadata` 走索引）。

**Collection `careercrew_episodic`**：只有 `text_dense` + `text_sparse`，payload 含
`text`、`type`（记忆类型），复用文本路。**QdrantStore 按 collection 名自动路由
schema**：`settings.vector_store.collections` 中 episodic 名 → 纯文本 schema，
knowledge 名 → 多模态 schema；构造参数与 MilvusStore 对齐
（`QdrantStore(settings, collection_name=None)`，默认 knowledge）。

**接口行为**：

- `upsert`：id 规则确定且幂等——页面 `{doc_id}_p{page:03d}`、对象
  `{doc_id}_o{page:03d}_{obj:02d}`；同 id 覆盖，重灌不产生脏数据。
- `query(dense, top_k, filters, sparse)`：两路 prefetch（`text_dense` /
  `text_sparse`，各取 top_m）→ 客户端 `rrf_fuse(weights=...)` 融合 → 返回
  `QueryResult`（含 `image_path/type/page` 字段，默认值保证旧构造兼容）。
- `query_routes(dense, sparse, top_m, filters)`：返回各路原始 top_m 供客户端加权融合。
- `get_by_ids`：`with_vectors=True` 回读 dense/sparse 到 `VectorRecord`（schema 无视觉向量）。
- `count()`：按 collection 统计（runtime/CLI 的 `store.count()==0` 首启判断继续可用）。
- `delete_by_metadata(filters)`：支持 `doc/source/type/page` 组合过滤。
- `BaseVectorStore` 契约本身不变（记忆系统兼容）；`FakeVectorStore` 补 `count()`。

### 解析与入库

新增 `careercrew_core/rag/loaders/mineru_loader.py`（本地子进程）、
`mineru_api_loader.py`（云端 API，v1.3 默认）、`mineru_common.py`（产物组装共用）
与 `careercrew_core/rag/pipeline_multimodal.py`（`MultimodalIngestionPipeline`）。
按 `rag.loaders.provider` 路由：

- **`api`（默认，v1.3）**：`MinerUApiLoader` 走 MinerU 官方精准解析 API
  （`file-urls/batch` 上传 → 轮询 `extract-results/batch` → 下载 zip 解压），
  本机零推理负载；`api_key`（`MINERU_API_KEY`）必填，`poll_interval` / `timeout`
  控制轮询与超时，上传/轮询/下载带指数退避重试，zip 解压做路径穿越防护。
- **`local`（可选回退）**：`MinerULoader` 以独立子进程跑
  `mineru -p <file> -o <output_dir>/<doc_id> -b pipeline --method <method> -l ch`。
- 两种 loader 产物结构一致：每页渲染图、每页 Markdown、`content_list.json`
  （对象块：表格 Markdown + 图/表裁剪图，带 bbox），组装逻辑在 `mineru_common.py`
  共用。解析失败统一 `ParsingError` → 调用方记录 `doc_type=error` 并跳过，
  不中断批量入库。
- **ParsedDocument 契约**：
  `pages[{page_no, image_path, markdown}]`、
  `objects[{page_no, image_path, text, bbox}]`。
- **编码（v1.2/v1.3 定稿）**：页面/对象文本 →（可选 Contextualizer，LLM 文档级
  上下文前缀，仅用于 embedding；启用与否由 `settings.rag.chunking.contextual` 决定，
  运行时首启入库当前固定关闭）→ BGE-M3 dense+sparse；**图片内容由 MinerU 抽取为
  文本（OCR/Markdown）后统一走 BGE-M3 文本向量，检索侧无任何本地视觉模型**；
  页面图/对象图路径保留在 payload，回答阶段由 Qwen3-VL-8B API 看图生成。
  合并 upsert 到 `careercrew_mm`。
- `ingest_text` 保留：纯文本走原路径（Markdown 直读 + 切分 + 上下文 + BGE-M3），
  schema 无视觉向量（文本双向量与文件路径一致）。
- `scripts/ingest_knowledge.py` 与 API/CLI 首启自动入库改调新管线；语料默认
  `data/uploads/*.pdf/png/docx`（知识库 = 用户上传的简历/文档；`data/knowledge`
  不参与入库）。

### 检索与生成

`MultimodalSearch` 替换 `HybridSearch`（构造/`search(query, top_k, filters)` 签名兼容，
`image_path` 参数走 VLM 提取文本后并入查询），`rag_query` 工具保留名称、内部换实现。

- **文本查询**：BGE-M3 编 query（dense+sparse）→ 两路 prefetch（各 top_m=30）→
  客户端加权 RRF（weights：dense 1.0 / sparse 1.0）→ 取 top_m。
- **图片查询**：image_reader（VLM API 提取图片文字/描述）→ 并入文本查询。
- **精排**：`SiliconFlowVLReranker.rerank` 调硅基流动 `/rerank` 接口
  （Qwen3-VL-Reranker-8B，`documents[].content` 含文本块 + base64 data URI 图片，
  本地图片必须转 data URI 而非传路径）→ 取 top_k；API 失败回退 RRF 序
  （不做 image token 计费记录）。
- **回答**：top_k 页面图（base64 data URI）+ 文本块 → zai-org/GLM-4.5V 生成，
  返回 `{answer, sources[]}`；sources 含 `image_path`，可在 Web/CLI 展示。
- **rag_query 输出**：返回字符串保持旧文本格式兼容，图片以
  `[image: <绝对路径>]` 行附尾；渲染层（Web/CLI）识别并展示，文本模型忽略（R8）。
- `AgenticSearch` / `QueryRouter` / `query_decomposer` 不动，内部依赖换成
  `MultimodalSearch`。

### MCP 封装

- **SDK**：`mcp>=1.20,<2`（R1），`from mcp.server.fastmcp import FastMCP`。
- **包布局**：顶层新增 `careercrew_mcp/` 包（`__main__.py` + `server.py`），加入
  `pyproject.toml` 的 `[tool.setuptools.packages.find].include`；`[project.scripts]`
  加 `careercrew-mcp` 入口。`python -m careercrew_mcp` 或 `careercrew-mcp` 均可启动
  （必须用 careercrew env 的 python 跑，base env 无 torch/FlagEmbedding）。
- **双传输**：默认 stdio（本地 Agent 直接连）；`--http --port` 时启用 Streamable
  HTTP（uvicorn）。HTTP 默认绑定 `127.0.0.1`，v1 不加认证（README/CLI help 注明）。
- **工具**：
  - `ingest_document(path, metadata?) -> {doc_id, points, path}`（本地路径或
    http(s) URL，URL 先下载到 `data/uploads/`）；
  - `search(query, image_path?, top_k=5, filters?) -> [{id, score, text, image_path, type, doc, page}]`；
  - `query(question, image_path?, top_k=5) -> {answer, sources}`；
  - `status() -> {vector_store, url, collections, knowledge_points, vlm_model, rerank_model}`
    （库状态 + 配置后端）。
- 项目内 Agent 通过内部函数直调同一核心（不走网络）；外部系统走 MCP。

### 删除与清理

- 删除 `milvus_store.py` / `chroma_store.py`、`markitdown_loader.py` 及全部引用
  （含 `careercrew_api/routers/resume.py` 的 PDF/docx 路径改 MinerU，R2）。
- `pyproject.toml`（实施后定稿）：去掉 `pymilvus`、`markitdown`、`mineru`、
  `colpali-engine`、`peft`；加 `qdrant-client>=1.19`、`requests`（MinerU 云端 API）、
  `pymupdf`（页面渲染）、`mcp>=1.20,<2`；`requires-python` 维持 `>=3.12,<3.13`。
  本地 loader（`provider=local`）需要环境里另有 mineru CLI，不再作为项目依赖。
- careercrew 环境卸载 `pymilvus`、`markitdown`；`data/db/milvus` 遗留目录手动清理
  （旧数据不迁移，源文件完整）。
- 删除 milvus/chroma 相关测试（`test_milvus_backend.py`、`test_vector_store_switch.py`
  的 milvus/chroma 用例）；`test_smoke_imports.py` 依赖列表更新。

## 对外接口

- MCP 工具签名如上；内部新增 `MultimodalIngestionPipeline.ingest_file/ingest_text` 与
  `MultimodalSearch.search(query, image_path?, top_k, filters?)`，返回类型对齐现有
  `QueryResult`（新增 `image_path/type/page` 字段，默认值向后兼容）。
- `rag_query` 输出扩展为「纯文本块 + `[image: path]` 标记行」，旧文本格式兼容。
- `BaseVectorStore` 契约不变（兼容记忆系统），QdrantStore 额外提供 `count` 与
  `list_docs`；记忆系统（episodic）必须使用 episodic collection 的 store 实例
  （R5，`VectorIndex` 注入时显式传 `collection_name=careercrew_episodic` 的实例）。

## 测试计划

**单元（不依赖 Qdrant/GPU）**：
- settings 校验：qdrant/mineru 合法；milvus_lite/markitdown 报错且信息含迁移指引；
  vlm.api_key 缺失报错。
- 加权 `rrf_fuse`（weights 生效、默认权重与旧行为一致）；`MultimodalSearch` 精排
  失败回退 RRF 序。
- MinerU loader 契约：mock 子进程输出 → `ParsedDocument` 页面+对象解析正确；
  Markdown 直读走原 `BaseLoader` 路径。
- MinerU API loader：mock 上传 → 轮询 → 下载 zip → 解压组装全流程
  （`tests/unit/test_mineru_api_loader.py`），含等待状态、超时与路径穿越防护。
- `QueryResult` 新字段默认值兼容旧构造；`FakeVectorStore.count()`。
- `rag_query` 输出：纯文本格式不变 + `[image: ...]` 标记行。

**集成（真实 Qdrant 容器，`pytest.mark.integration` + 容器不可用时 skip）**：
- QdrantStore 双 schema roundtrip：dense/sparse、payload 过滤与
  `delete_by_metadata`、`count`、`get_by_ids`（with_vectors）。
- 端到端用 `data/uploads/求职简历.pdf`（2 页）与 `resume.png`：ingest → 文本查询命中
  简历要点 → 图片查询命中对应页面 → `query` 看图回答正确引用来源。
- episodic 回归：`VectorIndex` 写入/检索落到 `careercrew_episodic`（不与知识库混库）。
- resume 上传回归：PDF 上传走 MinerU 解析成功；损坏文件返回 `doc_type=error`。

**回归**：既有 agent 单测改用 FakeVectorStore 继续通过（补 count）；`test_smoke_imports`
更新为 qdrant_client/requests/pymupdf/mineru loader 组合；删除 milvus/chroma 相关测试。

**MCP**：stdio client 工具发现 + 四工具端到端；HTTP 传输冒烟；外部进程调用 `query`
返回图文来源（sources 含 image_path）。

## 假设与默认（修正）

- 生成与精排均走硅基流动 API（已有 key），模型固定
  `zai-org/GLM-4.5V`、`Qwen/Qwen3-VL-Reranker-8B`；rerank 文档图片用 base64
  data URI（R8 之外的接口细节）。
- 本地推理只有 BGE-M3（CPU）；MinerU 默认走云端 API（`provider=api`，本机零推理负载），
  `provider=local` 本地子进程仅作可选回退（R3 已被 v1.2/v1.3 覆盖）。
- 首启冷启动（BGE-M3 模型加载 ~16s + 可选本地 MinerU 拉起）可接受。
- 旧 Milvus 数据不迁移（源文件完整，直接重灌；`data/db/milvus` 手动清理）。
- v1 语料 = `data/uploads/` 下 PDF/PNG/DOCX（`data/knowledge` 不参与）；MCP `ingest_document`
  支持本地路径与 http(s) URL（URL 先下载到 `data/uploads/`）。
- 记忆系统（episodic）继续文本向量化，落到 Qdrant `careercrew_episodic`，使用独立
  store 实例（R5）。
- MCP SDK 锁定 `mcp>=1.20,<2`（R1）；SigLIP2 不在 v1 使用（R7）。

## 风险与回退

| 风险 | 回退 |
|---|---|
| 云端 API 间歇断连/限流（v1.3） | 上传/轮询/下载指数退避重试；失败记录 `doc_type=error` 跳过；可切 `provider=local` 回退 |
| MinerU 解析失败或产物结构变化 | 记录 `doc_type=error` 跳过；解析层单点封装（解析器可替换） |
| VL rerank API 失败/限流 | 回退 RRF 融合序（dense/sparse 等权） |
| VL 生成 API 失败 | 回退文本生成（DeepSeek + 文本块），sources 仍返回 |
| mcp 2.0 依赖树被其他包拉起 | `pyproject` 显式 `mcp>=1.20,<2`；CI/启动时冒烟 import FastMCP |
| 服务端 RRF 诱惑回潮 | 代码评审点：`query()` 禁止用 Qdrant `Fusion.RRF`（无法加权，R4） |
