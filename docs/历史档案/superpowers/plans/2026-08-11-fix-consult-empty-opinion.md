# 修复会诊"薪资谈判师空输出"实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复多顾问会诊中薪资谈判师因循环检索 + recursion_limit 过早触发而输出空卡片的问题，并对任何顾问的空/失败意见给出可读兜底。

**Architecture:** 三层修复：(1) `run_agent` 的 recursion_limit 由 `max_iterations*2+6` 放宽到 `max_iterations*3+10`，保证 `MaxIterationsMiddleware` 的短路 marker（约 3*N+2 个 super-step）先于递归上限触发，并给失败加日志；(2) 薪资谈判师 prompt 限定最多检索 2-3 次、无数据必须直接作答，消除死循环诱因；(3) core/API 会诊层对空意见按 `stopped_reason` 输出可读兜底文案，前端不再出现"只有标题没有内容"。

**Tech Stack:** Python 3.12 / langchain 1.3.14 / langgraph 1.2.10 / FastAPI / pytest。

## Global Constraints

- 不改 `BaseAgent` 对外契约（`run(state)->dict`、`last_result: AgentResult`）。
- `opinion_fallback` 对 `last_result` 用 `getattr` 防御访问（FakeRuntime 等测试替身可能没有 `stopped_reason`）。
- 代码注释用中文，与仓库现有风格一致。
- 提交时只 `git add` 本次涉及文件，不动用户工作区其它未提交改动；commit 不加 `Co-Authored-By`。

---

### Task 1: 修复 recursion_limit 与失败日志（langchain_agent.py + 回归测试）

**Files:**
- Modify: `careercrew_ai/agents/langchain_agent.py:137`
- Test: `tests/unit/test_base_agent.py`

**Interfaces:**
- Consumes: 现有 `AgentExecState` / `MaxIterationsMiddleware` / `run_agent(agent, messages, stream_callback, max_iterations) -> AgentResult`。
- Produces: `run_agent` 在 `max_iterations` 轮循环后返回 `stopped_reason="max_iterations"`、`content="（已达最大迭代轮次）"`（而非 `stopped_reason="error"`、`content=""`）。

- [ ] **Step 1: 写失败回归测试**（追加到 `tests/unit/test_base_agent.py`）

```python
def test_max_iterations_short_circuit_high_limit() -> None:
    """回归：langchain 1.3 的 before_model 是独立图节点，每轮迭代消耗 3 个
    super-step。N=10 时 marker 需约 32 个 super-step，旧 recursion_limit
    （2*N+6=26）会先撞 GraphRecursionError → 空 content。"""
    llm = FakeChatModel([
        AIMessage(content="", tool_calls=[_tc("add", {"a": 1, "b": 1}, f"c{i}")])
        for i in range(12)
    ])
    agent = BaseAgent(name="x", system_prompt="sys", llm=llm, tools=[add], max_iterations=10)
    update = agent.run(_state())
    out = update["agent_outputs"]["x"]
    assert out["stopped_reason"] == "max_iterations"
    assert out["iterations"] == 10
    assert out["tool_calls_total"] == 10
    assert agent.last_result.content == "（已达最大迭代轮次）"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n careercrew pytest tests/unit/test_base_agent.py::test_max_iterations_short_circuit_high_limit -q`
Expected: FAIL（`stopped_reason == "error"`，`content == ""`）

- [ ] **Step 3: 实现修复**

`careercrew_ai/agents/langchain_agent.py` 顶部加：

```python
import logging
```

模块级加：

```python
logger = logging.getLogger(__name__)
```

`run_agent` 中 recursion_limit 改为：

```python
            # langchain 1.3 起 before_model 是独立图节点，每轮迭代实际消耗
            # 3 个 super-step（before_model + model + tools）；旧公式 2*N+6
            # 会在 MaxIterationsMiddleware 的 marker（约 3*N+2 处）触发前
            # 先撞 recursion_limit（实测 GraphRecursionError → 空 content）。
            # 3*N+10 保证中间件短路先于递归上限。
            config={"recursion_limit": max_iterations * 3 + 10},
```

`except Exception` 块改为带日志：

```python
    except Exception as e:  # noqa: BLE001 - 任何执行异常标记 error，不吞给上层
        failed = True
        logger.exception("agent.stream 执行异常（run_agent 标记 stopped_reason=error）：%s", e)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n careercrew pytest tests/unit/test_base_agent.py -q`
Expected: PASS（含新增回归 + 原有 max_iterations 短路测试）

- [ ] **Step 5: Commit**

```bash
git add careercrew_ai/agents/langchain_agent.py tests/unit/test_base_agent.py
git commit -m "fix(agents): 放宽 recursion_limit 使迭代上限中间件先于递归上限触发"
```

---

### Task 2: 薪资谈判师 prompt 增加检索上限

**Files:**
- Modify: `careercrew_ai/prompts/salary_negotiator.txt`
- Test: `tests/unit/test_negotiator.py`

**Interfaces:**
- Consumes: 无（纯 prompt 文本）。
- Produces: prompt 明确"最多检索 2-3 次、无数据必须直接作答"，减少循环检索。

- [ ] **Step 1: 写测试**

追加到 `tests/unit/test_negotiator.py`：

```python
def test_negotiator_prompt_limits_rag_retries() -> None:
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "careercrew_ai" / "prompts" / "salary_negotiator.txt"
    text = path.read_text(encoding="utf-8")
    assert "最多检索 2-3 次" in text
    assert "禁止反复检索" in text
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n careercrew pytest tests/unit/test_negotiator.py::test_negotiator_prompt_limits_rag_retries -q`
Expected: FAIL（AssertionError）

- [ ] **Step 3: 修改 prompt**

`careercrew_ai/prompts/salary_negotiator.txt` 工作流程改为：

```text
## 工作流程
1. 明确目标公司、offer 薪资、用户期望与底线（从对话和画像获取）。
2. 调 `rag_query` 检索该公司/岗位的薪资范围与谈薪经验（如"大模型工程师 薪资 谈判"、"字节 薪资"），**最多检索 2-3 次**。
3. 若检索没有有效薪资数据，停止检索，直接基于市场普遍认知给出策略与话术，并明确标注为估算。
4. 制定谈薪策略：报价区间、谈判筹码（offer 竞争 / 技能稀缺 / 项目成果）、话术要点。
5. 输出：薪资策略 + 话术草稿 + 风险提示。
```

原则部分改为：

```text
## 原则
- 优先基于 rag_query 检索到的薪资数据；知识库无数据时基于常识给出并标注估算。
- 禁止反复检索、空转不输出；检索 2-3 次无结果后必须直接作答。
- 高 stakes：最终投递 / 接 offer 动作走 HITL 确认（K 阶段）。
- 用中文，话术具体可念。
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n careercrew pytest tests/unit/test_negotiator.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add careercrew_ai/prompts/salary_negotiator.txt tests/unit/test_negotiator.py
git commit -m "fix(prompt): 薪资谈判师限定 rag_query 检索次数，防止循环检索空转"
```

---

### Task 3: 会诊层空意见兜底（core + API + 测试）

**Files:**
- Modify: `careercrew_core/supervisor/consult.py`
- Modify: `careercrew_api/routers/consult.py`
- Test: `tests/unit/test_consult.py`

**Interfaces:**
- Consumes: `AgentResult.{content, stopped_reason}`（`getattr` 防御访问）。
- Produces: `opinion_fallback(content: str, stopped_reason: str) -> str`；`consult()` 与 API `_run_one` 的 `opinions[name]` 在内容为空时给出可读文案。

- [ ] **Step 1: 写测试**

追加到 `tests/unit/test_consult.py`：

```python
from careercrew_core.supervisor.consult import build_consult_graph, consult, opinion_fallback


class FailingAgent:
    def __init__(self, name: str, stopped_reason: str) -> None:
        self._name = name
        self.last_result = type("R", (), {"content": "", "stopped_reason": stopped_reason})()

    def run(self, state) -> dict:
        return {}


def test_opinion_fallback_empty_on_error() -> None:
    assert opinion_fallback("", "error") == "（该顾问本次执行出错，未能给出意见）"


def test_opinion_fallback_empty_on_max_iterations() -> None:
    assert opinion_fallback("", "max_iterations") == "（该顾问达到最大分析轮次，未能给出完整意见）"


def test_opinion_fallback_keeps_content() -> None:
    assert opinion_fallback("  有效意见  ", "final_answer") == "有效意见"
    assert opinion_fallback("部分内容", "error") == "部分内容"


def test_consult_function_fallback_for_failed_agent() -> None:
    class FakeLLM:
        def invoke(self, prompt):
            return AIMessage(content="综合：见上")

    out = consult(
        {"salary_negotiator": FailingAgent("salary_negotiator", "error")},
        "这个 offer 要不要接",
        FakeLLM(),
    )
    assert out["opinions"]["salary_negotiator"] == "（该顾问本次执行出错，未能给出意见）"
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n careercrew pytest tests/unit/test_consult.py -q`
Expected: FAIL（`opinion_fallback` 不存在 / consult 仍存空串）

- [ ] **Step 3: 实现兜底 helper**

`careercrew_core/supervisor/consult.py` 增加：

```python
def opinion_fallback(content: str, stopped_reason: str) -> str:
    """空意见兜底：顾问失败/超限时给出可读提示，避免前端空卡片。

    content 非空时原样返回（可能是中断前的部分回答）。
    """
    content = (content or "").strip()
    if content:
        return content
    if stopped_reason == "max_iterations":
        return "（该顾问达到最大分析轮次，未能给出完整意见）"
    if stopped_reason == "error":
        return "（该顾问本次执行出错，未能给出意见）"
    return ""
```

`consult()` 中 `opinions[name] = agent.last_result.content` 改为：

```python
        r = agent.last_result
        opinions[name] = opinion_fallback(
            getattr(r, "content", ""), getattr(r, "stopped_reason", "")
        )
```

- [ ] **Step 4: API 路由使用兜底**

`careercrew_api/routers/consult.py` 内 `_worker_impl` 的 import 改为：

```python
                from careercrew_core.supervisor.consult import _synthesize, opinion_fallback
```

`_run_one` 中赋值改为：

```python
                    agent.run(state)
                    r = agent.last_result
                    content = opinion_fallback(
                        getattr(r, "content", ""), getattr(r, "stopped_reason", "")
                    )
                    opinions[name] = content
                    q.put({"type": "agent_end", "agent": name})
```

- [ ] **Step 5: 运行确认通过**

Run: `conda run -n careercrew pytest tests/unit/test_consult.py tests/api/test_consult_api.py -q`
Expected: PASS（FakeRuntime 的 last_result 无 stopped_reason，`getattr` 兜底不炸）

- [ ] **Step 6: Commit**

```bash
git add careercrew_core/supervisor/consult.py careercrew_api/routers/consult.py tests/unit/test_consult.py
git commit -m "fix(consult): 空意见按 stopped_reason 输出可读兜底，避免前端空卡片"
```

---

### Task 4: 真实环境验证

- [ ] **Step 1: 复现脚本回归**

Run: 会诊两个 agent（`salary_negotiator` + `career_planner`），确认：
- salary_negotiator `stopped_reason != "error"` 且 `content` 非空（或为 `max_iterations` 兜底文案）；
- 不再出现 `GraphRecursionError`。

- [ ] **Step 2: 全量单元测试**

Run: `conda run -n careercrew pytest tests/unit -q`
Expected: PASS（无新增失败）

---

## Self-Review

**Spec coverage:** Task 1 覆盖 recursion_limit 根因（所有 agent 共享）；Task 2 覆盖薪资谈判师循环诱因；Task 3 覆盖空卡片表现层（core consult + API consult 一致兜底）。无遗漏。

**Placeholder scan:** 无 TBD/TODO；每个改代码步骤都给出完整代码。

**Type consistency:** `opinion_fallback(content: str, stopped_reason: str) -> str` 在 core 定义，API 与 `consult()` 均按同一签名调用；`getattr(r, "stopped_reason", "")` 兼容测试替身无该属性的情况。
