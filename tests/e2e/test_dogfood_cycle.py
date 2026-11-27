"""N5 场景 4：dogfood——用自身知识库跑完整求职周期。

知识库中已摄取 CareerCrew 自身文档（README/DEV_SPEC 片段，此处以检索桩模拟）：
KnowledgeAdvisor 基于自身文档回答架构问题（带来源引用）
-> JobCycle（supervisor 图驱动）完成 匹配 -> 简历定制，
验证"系统用自身能力服务自身求职周期"。
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from careercrew_core.agents.knowledge_advisor import KnowledgeAdvisor
from careercrew_core.tools.internal.rag_query import make_rag_query_tool
from careercrew_core.tools.registry import ToolRegistry, ToolSpec
from careercrew_core.workflow.job_cycle import JobCycle
from tests.fakes import FakeChatModel


class SelfDocsHS:
    """以 CareerCrew 自身文档片段充当知识库（dogfood 语义）。"""

    def search(self, query, top_k=5, filters=None):
        from careercrew_ai.vector_store import QueryResult

        return [
            QueryResult(id="d1", score=0.93,
                        text="CareerCrew 采用 LangGraph supervisor 编排多智能体："
                             "职位匹配/简历定制/面试模拟/薪资谈判/职业规划。",
                        metadata={"source": "docs/架构与规划/开发规范.md"}),
            QueryResult(id="d2", score=0.88,
                        text="自建多模态 RAG：BGE-M3 三合一向量写入 Qdrant，"
                             "混合召回 + RRF 融合 + rerank 精排。",
                        metadata={"source": "README.md"}),
        ][:top_k]


@pytest.mark.e2e
def test_dogfood_self_kb_then_job_cycle() -> None:
    # 1) 向自身知识库提问：回答必须引用检索到的文档内容与来源
    reg = ToolRegistry()
    reg.register(ToolSpec(tool=make_rag_query_tool(SelfDocsHS())))
    kb = KnowledgeAdvisor(
        llm=FakeChatModel([
            AIMessage(content="", tool_calls=[
                {"name": "rag_query", "args": {"query": "CareerCrew 的架构是什么", "top_k": 3},
                 "id": "c1", "type": "tool_call"},
            ]),
            AIMessage(content="CareerCrew 用 supervisor 编排匹配/简历/面试/谈薪/规划五个 agent，"
                              "RAG 采用 BGE-M3+Qdrant 混合召回。[来源: DEV_SPEC.md]"),
        ]),
        tools=reg, max_iterations=5,
    )
    state = {
        "thread_id": "t-dogfood", "user_id": "u1", "stage": "knowledge",
        "user_intent": "了解 CareerCrew 架构",
        "messages": [HumanMessage(content="这个项目自己的架构是什么样的？")],
        "pending_action": None, "agent_outputs": {}, "target_companies": [],
    }
    out = kb.run(state)
    produced = out["agent_outputs"]["knowledge_advisor"]
    assert "supervisor" in produced["content"] and "BGE-M3" in produced["content"]   # 基于检索而非幻觉
    assert produced["tool_calls_total"] == 1

    # 2) 同一周期继续：意向 -> 匹配 -> 选 JD -> 简历（supervisor 图自动流转）
    class FakeAgent:
        def __init__(self, content: str) -> None:
            self.last_result = type("R", (), {"content": content})()
            self.run_calls = 0

        def run(self, s) -> None:
            self.run_calls += 1

    jm = FakeAgent("1. 字节 大模型应用工程师 0.93\n2. 腾讯 大模型应用开发 0.86")
    ra = FakeAgent("按 JD 定制：突出 supervisor 多智能体编排与 BGE-M3 混合检索经验")
    cycle = JobCycle(jm, ra, user_id="u1")
    final = cycle.run(
        "了解完架构后，帮我投大模型应用岗并定制简历",
        user_id="u1",
        select_jd=lambda m: "字节 大模型应用工程师 JD",
    )
    assert "定制" in final                       # 最终产出为按 JD 定制的简历
    assert jm.run_calls == 1 and ra.run_calls == 1
    # dogfood 完整链路：自身文档 -> 自我介绍 -> 自荐简历
