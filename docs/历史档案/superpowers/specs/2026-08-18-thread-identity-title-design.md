# 对话历史线程身份与摘要标题修复设计

**日期：** 2026-08-18  
**范围：** legacy thread_id/conversation UUID 映射、侧边栏线程列表、首轮摘要标题

## 目标

确保一个对话只对应一条侧边栏历史记录，并在首轮回答完成后把同一条记录的标题更新为模型生成的简短摘要；模型调用失败时保留用户首句作为兜底标题。

## 根因

前端首次发送使用 `t-*` 等 legacy ID。后端 conversation 表会为它创建 UUID，并在 done 事件返回 UUID；前端收到 done 后把当前会话切换到 UUID。下一轮 runtime 又用 UUID 调用 memory `ThreadStore`，由于原记录仍以 legacy ID 保存，遂新增第二条 memory 线程。侧边栏 `/api/threads` 读取 memory 线程列表，因此显示两条历史。

当前代码没有真正的 LLM 标题生成调用；标题来自用户首句。旧文档中的 LLM 标题生成描述与实现不一致。

## 设计

### 1. 线程身份

- 保留前端 legacy ID 作为稳定的 UI/请求 ID，兼容现有附件、历史恢复和旧 API。
- `streamStore` 收到同时包含 `thread_id=UUID` 与 `legacy_thread_id=当前 legacy ID` 的 done 事件时，只记录稳定 ID，不 remap 当前会话。
- runtime 的 memory 线程写入前，将已存在的 conversation UUID 解析回其 `legacy_thread_id`；旧客户端即使发送 UUID，也不会新增第二条 memory 线程。
- 对没有 legacy 映射的 UUID 或纯 legacy 旧线程保持原行为。

### 2. 标题生成

- 仅在 conversation 的第一轮完成后生成一次摘要标题，避免每轮回答重复调用模型。
- 输入为脱敏、截断后的用户问题和助手最终回答。
- 使用现有 `self.llm.invoke()`，要求模型只返回不超过 18 个汉字的标题；清理引号、Markdown 标记和多余换行后限制为 30 字符。
- 同时更新 canonical conversation 标题和对应 memory thread 标题，侧边栏下一次 nonce 刷新即可显示新标题。
- 标题模型调用异常、空响应或无有效文本时静默保留首句标题，不影响回答完成。

## 验收

- 首轮完成后只有一条历史记录；第二轮不会新增另一条同会话记录。
- 首轮标题先显示用户输入，回答完成后变为摘要标题；模型调用失败时仍是用户输入。
- UUID 请求与 legacy 请求最终写入同一个 memory thread。
- 既有线程列表、消息恢复、regenerate 和租户隔离测试不回归。
