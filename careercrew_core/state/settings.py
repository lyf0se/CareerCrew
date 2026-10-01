"""配置加载与校验（A3）。

读取 config/settings.yaml -> 递归替换 ${VAR} 环境变量 -> pydantic 嵌套模型校验
-> 语义校验（api_key / backend 取值）。缺关键字段或语义非法时 fail-fast，
错误信息含字段路径（如 vector_store.backend），不吞异常。

设计：
- 不做网络/IO，只读配置文件 + 环境变量；首次调用探活留到 A4（create_llm）。
- ${VAR} / ${VAR:-default} 占位用正则递归替换，仅作用于字符串值，保留其他类型。
- pydantic v2 嵌套模型：ValidationError 的 loc 即字段路径，格式化为可读错误。
"""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError, field_validator

# ── 默认配置路径：careercrew_core/state/settings.py -> parents[2] = 项目根 ──
# 容器部署可用 CAREERCREW_SETTINGS_PATH 指向 config/settings.docker.yaml 等变体
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_settings_override = os.environ.get("CAREERCREW_SETTINGS_PATH", "").strip()
DEFAULT_CONFIG_PATH = (
    Path(_settings_override) if _settings_override else PROJECT_ROOT / "config" / "settings.yaml"
)

# ${VAR} / ${VAR:-default} 环境变量占位（仅 A-Za-z_ 开头的标识符）
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

# 向量库后端合法取值（多模态 RAG 全面替换后仅剩 Qdrant）
_VALID_VECTOR_BACKENDS = {"qdrant"}
_VALID_LOADER_BACKENDS = {"mineru"}
_VALID_LOADER_PROVIDERS = {"api", "local"}
_VALID_LOADER_MODEL_VERSIONS = {"pipeline", "vlm", "MinerU-HTML"}

# 旧值 -> 迁移指引（fail-fast 时提示，不静默替换）
_VECTOR_BACKEND_MIGRATION = {
    "milvus_lite": "qdrant",
    "milvus_docker": "qdrant",
    "chroma": "qdrant",
}
_LOADER_BACKEND_MIGRATION = {
    "markitdown": "mineru",
    "pymupdf": "mineru",
    "python-docx": "mineru",
}


class SettingsError(Exception):
    """配置加载或校验失败（fail-fast）。"""


# 仅开发进程内的随机回退：不把可预测密钥带入生产，也避免写入仓库。
_DEVELOPMENT_AUTH_SECRET = secrets.token_urlsafe(48)


# ── 嵌套模型：镜像 config/settings.yaml 结构 ──


class LLMSettings(BaseModel):
    provider: str
    model: str
    base_url: str
    api_key: str
    temperature: float = 0.7
    max_tokens: int = 4096


class EmbeddingSettings(BaseModel):
    provider: str
    model: str
    model_path: str
    use_fp16: bool = False
    batch_size: int = 12
    device: str = "cpu"  # BGE-M3 推理设备：cpu（默认，避免 GPU OOM）| cuda（有卡机型显著提速）


class RerankSettings(BaseModel):
    backend: str  # none | dashscope | local_bge
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    top_m: int = 30


class VectorStoreSettings(BaseModel):
    backend: str
    url: str = "http://localhost:6333"
    api_key: str = ""
    collections: dict[str, str]
    # M7：ColBERT 原生 multivector（MAX_SIM 服务端精排）。开启后建表带 text_colbert
    # 向量；存量集合自动 patch 补加（旧点需重新摄取才有该向量）。与
    # rag.retrieval.colbert_rerank 配合使用。
    colbert_multivector: bool = False


class RetrievalSettings(BaseModel):
    mode: str
    fusion_algorithm: str
    top_k_dense: int
    top_k_sparse: int
    top_k_final: int
    crag: bool = False            # M5：检索质量自评估 + incorrect 重写重检（默认关）
    colbert_rerank: bool = False  # M7：ColBERT late-interaction 本地精排（默认关）
    colbert_store: bool = False   # M7：摄取时把 colbert token 矩阵写入 payload（默认关，库膨胀）


class ChunkingSettings(BaseModel):
    strategy: str
    chunk_size: int
    chunk_overlap: int
    contextual: bool = False  # 全局默认；被 contextual_by_category 按分类覆盖
    # 知识库级开关：{category: true/false}，未声明的分类回落全局 contextual
    contextual_by_category: dict[str, bool] = {}


class LoadersSettings(BaseModel):
    """文档加载器配置（多模态 RAG：MinerU 云端 API 或本地子进程解析）。"""

    backend: str = "mineru"
    provider: str = "api"  # api（云端精准解析，推荐，本机零推理负载）| local（本地子进程）
    api_key: str = ""  # provider=api 时必填（MINERU_API_KEY）
    model_version: str = "vlm"  # pipeline | vlm（推荐）| MinerU-HTML
    poll_interval: int = 5  # API 轮询间隔（秒）
    timeout: int = 1800  # API 任务最长等待（秒）
    output_dir: str = "./data/parsed"  # MinerU 产物落盘（页面图/对象裁剪图/Markdown）
    device: str = "cpu"  # MinerU 子进程推理设备（cpu/cuda/mps；8GB 显存机型固定 cpu）
    method: str = "auto"  # 解析方法：auto | txt（纯文本 PDF 最快，跳过 OCR）| ocr（强制 OCR）
    formula: bool = True  # 公式识别（最重的模型之一；课件/简历等无公式文档可关掉提速）
    table: bool = True  # 表格识别
    language: str = "ch"  # 文档语言（API 参数，默认 ch）


class RagSettings(BaseModel):
    retrieval: RetrievalSettings
    chunking: ChunkingSettings
    loaders: LoadersSettings = LoadersSettings()


class VLMSettings(BaseModel):
    """多模态生成/精排（阿里云百炼平台）。"""

    model: str
    rerank_model: str
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = ""


class CheckpointerSettings(BaseModel):
    backend: str = "postgres"  # 唯一后端：postgres
    url: str = ""  # postgres DSN（DATABASE_URL）


class SupervisorSettings(BaseModel):
    checkpointer: CheckpointerSettings
    max_consecutive_agent_turns: int = 10


class PostgresSettings(BaseModel):
    """Postgres 记忆库连接（生产唯一持久化后端）。"""

    dsn: str = ""


class EpisodicSettings(BaseModel):
    collection: str = "careercrew_episodic_v2"  # Qdrant 情景记忆 collection
    vectorize: bool


class CompactionSettings(BaseModel):
    enabled: bool
    token_threshold_ratio: float
    retention_tokens: int


class MemoryRouterSettings(BaseModel):
    """LLM 路由检索配置（Claude Code 式：从事实清单选 top-N，非向量）。"""

    top_n: int = 5
    max_inject_tokens: int = 2000  # 自动注入总 token 预算


class ConsolidationSettings(BaseModel):
    """后台 consolidation 门控（Auto Dream 式）。"""

    min_interval_hours: int = 24
    min_sessions: int = 5
    # 每日自动合并时刻（本地时区，"HH:MM"）；"off" = 不调度（仅手动/测试触发）
    dream_schedule: str = "off"


class MemorySettings(BaseModel):
    """记忆子系统配置。enabled=false 时记忆默认关闭（Codex 式治理）。"""

    enabled: bool = False
    postgres: PostgresSettings = PostgresSettings()
    episodic: EpisodicSettings
    compaction: CompactionSettings
    router: MemoryRouterSettings = MemoryRouterSettings()
    consolidation: ConsolidationSettings = ConsolidationSettings()


class RegistrySettings(BaseModel):
    internal: list[str]
    mcp: list[str]


class ToolsHitlSettings(BaseModel):
    requires_confirmation: list[str]


class SearchSettings(BaseModel):
    """岗位搜索渠道（N1）：Boss CDP 后端默认禁用，配置调试端口后启用。"""

    boss_cdp_url: str = ""  # 例 http://127.0.0.1:9222；空=禁用 Boss 渠道
    boss_city: str = ""     # 可选城市代码（如北京 101010100）；空=全国


class BrowserAccessSettings(BaseModel):
    """宿主机 Chrome 控制接口（/api/browser/*）的来源校验策略。

    strict：只放行 loopback——后端与 Chrome 同机直跑时使用。
    container：后端跑在容器里，经端口映射进来的请求，客户端 IP 是网桥网关
        （如 172.18.0.1）而非 loopback，需要额外放行 RFC1918 私有地址。
        该策略只放宽"来自本机局域网"这一层；公网不可达由 compose 把发布端口
        绑定到 127.0.0.1 保证，两者必须同时成立。
    """

    local_guard: str = "strict"

    @field_validator("local_guard")
    @classmethod
    def known_guard_mode(cls, value: str) -> str:
        mode = (value or "").strip().lower() or "strict"
        if mode not in {"strict", "container"}:
            raise ValueError("tools.browser.local_guard 只能是 strict 或 container")
        return mode


class ToolsSettings(BaseModel):
    registry: RegistrySettings
    hitl: ToolsHitlSettings
    search: SearchSettings = SearchSettings()
    browser: BrowserAccessSettings = BrowserAccessSettings()


class HitlSettings(BaseModel):
    default_policy: str  # confirm | auto


class LangSmithSettings(BaseModel):
    """LangSmith 追踪（AGENT_LANGSMITH_SPEC Part B）。"""

    enabled: bool
    project: str = "careercrew"
    api_key: str = ""
    masking: bool = True
    max_chars: int = 2000


class AuthSettings(BaseModel):
    """本地账号认证配置。

    开发/测试允许进程内随机回退；测试可显式配置 jwt_secret 以获得确定性。
    任何其他环境都必须显式提供足够强度的 AUTH_JWT_SECRET。
    """

    environment: str = "development"
    database_url: str = ""   # Postgres DSN；空则回退 DATABASE_URL 环境变量
    trusted_origins: list[str] = [
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://localhost:5176",
        "http://127.0.0.1:5176",
    ]
    login_max_failures: int = 5
    login_failure_window_minutes: int = 15
    login_lock_minutes: int = 15
    cleanup_interval_hours: int = 6
    jwt_secret: str = ""
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = False
    # 反向代理（LB/Nginx）后部署时设 true：登录限流改按 X-Forwarded-For 首个
    # 地址取真实客户端 IP。直连部署保持 false——该头可被客户端伪造，不可信
    # 环境下启用等于给限流开了绕通道。
    trust_proxy_headers: bool = False

    @property
    def is_development(self) -> bool:
        return self.environment.strip().lower() in {"development", "dev", "test"}

    def signing_secret(self) -> str:
        secret = self.jwt_secret.strip()
        if secret:
            return secret
        if self.is_development:
            return _DEVELOPMENT_AUTH_SECRET
        raise SettingsError("auth.jwt_secret 未设置（生产环境必须设置 AUTH_JWT_SECRET）")


class OSSSettings(BaseModel):
    """阿里云 OSS 对象存储（头像等文件；access_key 四项全空=未配置，回退本地存储）。"""

    endpoint: str = ""          # 如 oss-cn-beijing.aliyuncs.com
    access_key_id: str = ""
    access_key_secret: str = ""
    bucket_name: str = ""
    dir_prefix: str = "uploads"  # 对象键前缀（头像最终落在 {dir_prefix}/avatars/...）


class HrMonitorSettings(BaseModel):
    """HR 回复监听（E 批次）：定时拉取 Boss 未读会话写入情景记忆。"""

    enabled: bool = False
    interval_minutes: int = 30
    cdp_url: str = ""  # 空=复用 tools.search.boss_cdp_url


class Settings(BaseModel):
    """顶层配置，结构与 config/settings.yaml 一一对应。"""

    llm: LLMSettings
    embedding: EmbeddingSettings
    rerank: RerankSettings
    vector_store: VectorStoreSettings
    rag: RagSettings
    vlm: VLMSettings
    supervisor: SupervisorSettings
    memory: MemorySettings
    tools: ToolsSettings
    hitl: HitlSettings
    langsmith: LangSmithSettings
    auth: AuthSettings = AuthSettings()
    oss: OSSSettings = OSSSettings()
    hr_monitor: HrMonitorSettings = HrMonitorSettings()


# ── 环境变量替换 ──


def _substitute_env(value: Any) -> Any:
    """递归替换字符串值中的 ${VAR} 为 os.environ[VAR]（未设置则替换为空串）。"""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(
            lambda m: os.environ.get(m.group(1), m.group(2) or ""),
            value,
        )
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


def _format_validation_errors(err: ValidationError) -> str:
    lines = ["配置校验失败（字段缺失或类型错误）:"]
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"])
        lines.append(f"  - {loc}: {e['msg']}")
    return "\n".join(lines)


def _resolve_path(value: str | None) -> str | None:
    """相对路径解析为基于项目根的绝对路径（否则按 CWD 解析，换目录跑就挂）。"""
    if not value:
        return value
    p = Path(value)
    if p.is_absolute():
        return str(p)
    return str(PROJECT_ROOT / p)


def _resolve_paths(settings: Settings) -> Settings:
    """把所有相对路径字段解析为基于项目根的绝对路径。"""
    settings.embedding.model_path = _resolve_path(settings.embedding.model_path)
    settings.rag.loaders.output_dir = _resolve_path(settings.rag.loaders.output_dir)
    return settings


def validate_settings(settings: Settings) -> None:
    """语义校验（构造后的跨字段约束），失败抛 SettingsError。

    与 pydantic 字段级校验互补：这里查「字段值合法但语义不对」的情况，
    如 api_key 未设置、backend 取值非法。
    """
    problems: list[str] = []

    # LLM api_key：空或未解析的 ${VAR} 视为未设置
    if not settings.llm.api_key or "${" in settings.llm.api_key:
        problems.append("llm.api_key 未设置（检查环境变量 DASHSCOPE_API_KEY）")

    # Rerank api_key：backend=dashscope 时要求
    if settings.rerank.backend == "dashscope" and (
        not settings.rerank.api_key or "${" in settings.rerank.api_key
    ):
        problems.append(f"rerank.api_key 未设置（backend={settings.rerank.backend} 时需配置 API Key）")

    # VLM api_key（多模态生成/精排）
    if not settings.vlm.api_key or "${" in settings.vlm.api_key:
        problems.append("vlm.api_key 未设置")

    # LangSmith api_key：enabled=true 时必须提供 LANGSMITH_API_KEY
    if settings.langsmith.enabled and (
        not settings.langsmith.api_key or "${" in settings.langsmith.api_key
    ):
        problems.append("langsmith.api_key 未设置（enabled=true 时需 LANGSMITH_API_KEY）")

    # 向量库后端取值
    if settings.vector_store.backend not in _VALID_VECTOR_BACKENDS:
        hint = _VECTOR_BACKEND_MIGRATION.get(settings.vector_store.backend)
        migration = f"，已迁移，请改为 {hint}" if hint else ""
        problems.append(
            f"vector_store.backend 取值非法: {settings.vector_store.backend}，"
            f"应为 {sorted(_VALID_VECTOR_BACKENDS)} 之一{migration}"
        )

    # 文档加载器后端取值
    if settings.rag.loaders.backend not in _VALID_LOADER_BACKENDS:
        hint = _LOADER_BACKEND_MIGRATION.get(settings.rag.loaders.backend)
        migration = f"，已迁移，请改为 {hint}" if hint else ""
        problems.append(
            f"rag.loaders.backend 取值非法: {settings.rag.loaders.backend}，"
            f"应为 {sorted(_VALID_LOADER_BACKENDS)} 之一{migration}"
        )

    # 文档加载器 provider 取值
    if settings.rag.loaders.provider not in _VALID_LOADER_PROVIDERS:
        problems.append(
            f"rag.loaders.provider 取值非法: {settings.rag.loaders.provider}，"
            f"应为 {sorted(_VALID_LOADER_PROVIDERS)} 之一"
        )

    # provider=api 时必须提供 MinerU API key
    if settings.rag.loaders.provider == "api" and (
        not settings.rag.loaders.api_key or "${" in settings.rag.loaders.api_key
    ):
        problems.append("rag.loaders.api_key 未设置（provider=api 时需 MINERU_API_KEY）")

    # model_version 取值
    if settings.rag.loaders.model_version not in _VALID_LOADER_MODEL_VERSIONS:
        problems.append(
            f"rag.loaders.model_version 取值非法: {settings.rag.loaders.model_version}，"
            f"应为 {sorted(_VALID_LOADER_MODEL_VERSIONS)} 之一"
        )

    # 认证密钥与 Cookie：生产环境绝不能静默使用开发默认值。
    try:
        auth_secret = settings.auth.signing_secret()
        if not settings.auth.is_development:
            if len(auth_secret) < 32:
                problems.append("auth.jwt_secret 过短（生产环境至少 32 个字符）")
            if not settings.auth.cookie_secure:
                problems.append("auth.cookie_secure 必须为 true（生产环境）")
    except SettingsError as err:
        problems.append(str(err))

    # checkpointer 唯一后端：postgres
    if settings.supervisor.checkpointer.backend != "postgres":
        problems.append(
            f"supervisor.checkpointer.backend 取值非法: "
            f"{settings.supervisor.checkpointer.backend}，应为 postgres"
        )

    if problems:
        raise SettingsError("配置语义校验失败:\n" + "\n".join(f"  - {p}" for p in problems))


def load_settings(path: str | Path | None = None) -> Settings:
    """加载并校验配置。fail-fast：文件缺失 / 字段缺失 / 语义非法均抛 SettingsError。

    开发环境中项目根 .env 是本地配置的权威来源；生产环境保留进程注入的
    值，避免工作区 .env 覆盖真实目标端点或密钥。
    """
    protected_env = os.environ.get("CAREERCREW_ENV", "").strip().lower() == "production"
    load_dotenv(override=not protected_env)  # .env 仅作为开发环境便捷注入
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise SettingsError(f"配置文件不存在: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SettingsError(f"配置文件顶层应为映射，实际: {type(raw).__name__}")

    data = _substitute_env(raw)

    try:
        settings = Settings.model_validate(data)
    except ValidationError as err:
        raise SettingsError(_format_validation_errors(err)) from None

    validate_settings(settings)
    return _resolve_paths(settings)


def load_auth_settings(path: str | Path | None = None) -> AuthSettings:
    """只读取认证段，保证应用启动能单独对认证生产配置 fail-fast。

    API 应用的重组件仍按首请求惰性初始化；因此不能为了验证 JWT 密钥而要求
    LLM、向量库等所有配置在 import 时可用。环境变量 CAREERCREW_ENV 明确覆盖
    YAML 的 auth.environment，方便生产部署与隔离测试。
    """
    protected_env = os.environ.get("CAREERCREW_ENV", "").strip().lower() == "production"
    load_dotenv(override=not protected_env)  # 生产不得被工作区 .env 覆盖
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise SettingsError(f"配置文件不存在: {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SettingsError(f"配置文件顶层应为映射，实际: {type(raw).__name__}")
    auth_raw = _substitute_env(raw.get("auth", {}))
    if not isinstance(auth_raw, dict):
        raise SettingsError("auth 配置应为映射")
    environment_override = os.environ.get("CAREERCREW_ENV", "").strip()
    if environment_override:
        auth_raw["environment"] = environment_override
    try:
        auth = AuthSettings.model_validate(auth_raw)
    except ValidationError as err:
        raise SettingsError(_format_validation_errors(err)) from None
    try:
        secret = auth.signing_secret()
    except SettingsError:
        raise
    if not auth.is_development:
        if len(secret) < 32:
            raise SettingsError("auth.jwt_secret 过短（生产环境至少 32 个字符）")
        if not auth.cookie_secure:
            raise SettingsError("auth.cookie_secure 必须为 true（生产环境）")
    dsn = (auth.database_url or "").strip()
    if not dsn:
        dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise SettingsError(
            "auth.database_url 需要 AUTH_DATABASE_URL 或 DATABASE_URL"
        )
    auth.database_url = dsn
    if not auth.trusted_origins:
        raise SettingsError("auth.trusted_origins 不能为空")
    return auth
