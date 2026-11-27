"""运行时编排中心（原单文件 runtime.py 的包化拆分，对外导入路径不变）。

拆分布局（纯搬家不改行为，见 docs/架构与规划/技术债务计划.md #6）：
- common.py        模块级 helper + 异常 + logger
- heavy.py         HeavyInitMixin：两级惰性初始化（_ensure_stores 轻量存储 / _ensure_heavy AI 重组件）
- lifecycle.py     TurnLifecycleMixin：会话轮次生命周期 + 标题生成 + 线程 CRUD（ThreadService 职责）
- streaming.py     StreamingMixin：match/resume/planner/knowledge 四条流式编排
- regenerate.py    RegenerateMixin：重新生成全套
- tools_agents.py  ToolsAgentsMixin：agent 工厂/工具集装配/历史装载/HITL/effective tools
- services.py      ServicesMixin：记忆操作 + consult 流式 + health
- knowledge.py     KnowledgeDocsMixin：文档摄取/知识库管理/上下文资源/mentions

组装逻辑与 `careercrew_core/workflow/job_cycle.py` 的 `JobCycle` 保持一致（区别仅是
streaming callback 与 streaming=True）。初始化为惰性重组件（lifespan / uvicorn 启动）。
"""
from __future__ import annotations

import threading

from careercrew_api.runtime.common import (
    RegenerateConflictError,
    ResourceNotFoundError,
    RuntimeInitError,
    _cap_sources,
    _capture_langsmith_run_id,
    _norm_path,
    _observability_from_result,
    _rag_query_retrievals,
    _read_image_paths,
)
from careercrew_api.runtime.heavy import HeavyInitMixin
from careercrew_api.runtime.knowledge import KnowledgeDocsMixin
from careercrew_api.runtime.lifecycle import TurnLifecycleMixin
from careercrew_api.runtime.regenerate import RegenerateMixin
from careercrew_api.runtime.services import ServicesMixin
from careercrew_api.runtime.streaming import StreamingMixin
from careercrew_api.runtime.tools_agents import ToolsAgentsMixin


class CareerCrewRuntime(
    KnowledgeDocsMixin,
    ServicesMixin,
    ToolsAgentsMixin,
    RegenerateMixin,
    StreamingMixin,
    TurnLifecycleMixin,
    HeavyInitMixin,
):
    """进程级重组件单例 + 会话级 agent/JobCycle 工厂。"""


__all__ = [
    "CareerCrewRuntime",
    "RegenerateConflictError",
    "ResourceNotFoundError",
    "RuntimeInitError",
    "_cap_sources",
    "_capture_langsmith_run_id",
    "_norm_path",
    "_observability_from_result",
    "_rag_query_retrievals",
    "_read_image_paths",
    "get_runtime",
    "reset_runtime",
]

# ── 模块级双检锁单例 ──

_runtime: CareerCrewRuntime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> CareerCrewRuntime:
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = CareerCrewRuntime()
                # 进程启动即完成轻量初始化（settings+存储层）：请求路径
                # （如 compute_effective_tools）在 stores 初始化前读 settings
                # 会拿到 None → allowlist 空 → agent 工具被全部裁剪。
                _runtime._ensure_stores()
    return _runtime


def reset_runtime() -> None:
    """测试用：重置单例。"""
    global _runtime
    _runtime = None
