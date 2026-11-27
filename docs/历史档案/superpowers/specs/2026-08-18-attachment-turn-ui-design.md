# 对话附件单轮引用与输入框交互修复设计

**日期：** 2026-08-18  
**范围：** Web 对话附件发送、PDF/图片上下文解析、输入框工具栏、拖拽 Tooltip

## 目标

修复对话附件只能可靠引用文本/Markdown 的问题，使 PDF、图片等附件也能进入本轮模型上下文；让附件只随当前用户消息发送并显示在该消息气泡上方；移除 `@` 提及和 Tools 入口；解决拖拽调整输入框时自制 Tooltip 停留在旧位置的问题。

## 现状与根因

- PDF 解析分支在 `runtime.py` 中引用了未导入的 `storage.resolve_under`，会在进入解析前抛出 `NameError`，随后被降级为解析失败块。
- 图片描述请求把所有图片写成 `data:image/png`，JPEG 等真实格式的 MIME 不匹配。
- `AttachmentPicker` 通过线程附件列表回填所有附件，发送后若组件刷新，旧附件可能重新进入待发送列表。
- `ChatMessage`/`UserMessage` 没有附件展示字段；历史恢复忽略了 conversation user message metadata 中的附件信息。
- `PromptComposer` 仍统一渲染 `+`、`@` 和 Tools 三类入口。
- 自制 `Tooltip` 是 fixed 定位，拖拽开始后没有隐藏逻辑，导致气泡保留旧坐标。

## 设计

### 1. 单轮附件生命周期

`AttachmentPicker` 管理当前输入框的 pending attachments，不在组件挂载或切换线程时自动把服务端历史附件回填为待发送附件。组件继续负责客户端校验、上传、状态 chip、删除和错误展示，并通过 ref 暴露两个命令：

- `pick()`：由输入框“添加附件”按钮在用户手势内同步触发文件选择器。
- `clear()`：发送成功启动前由页面清空 pending 状态并触发 `onAttachmentsChange([])`。

各对话页面发送时先快照 `attachments`，用快照构造请求体和用户消息，再清空 picker。请求体只发送本轮附件 id；后端继续按 id 校验所有权并读取附件内容。后端已有附件 metadata 保存链路保留，用于 regenerate 和历史恢复。

### 2. 用户消息附件展示与恢复

在共享消息类型中增加轻量 `MessageAttachment` 展示结构，至少包含 `id`、`filename`，可选包含 `sizeBytes`、`mimeType`、`kind`。`ChatMessage` 增加 `attachments?: MessageAttachment[]`。

`UserMessage` 在右对齐根节点内按以下顺序渲染：

1. 附件 chip 列表；
2. 原有用户消息气泡；
3. 原有 Copy/Edit/More 操作行。

`historyRestore` 从 conversation user message 的 `metadata.attachments` 提取展示摘要，仅保留 id、filename、kind 等 UI 所需信息，不把附件正文重新放入前端消息。六个对话页面创建用户消息时使用发送快照填入 `attachments`；`TurnSection` 增加附件透传参数，保证所有页面的用户消息一致展示。

### 3. PDF、文档与图片上下文解析

保留现有 `resolve_attachment_blocks()` 的所有权校验和文本块注入协议。

- Markdown/TXT：继续 UTF-8 读取并截断。
- 图片：`describe_image()` 接受真实 MIME 类型，优先使用附件数据库的 `mime_type`，否则按扩展名推断。
- PDF/文档：先使用现有 MinerU `parse_file()`；PDF 解析异常时使用已存在的 PyMuPDF 依赖提取页面文本作为回退。回退也失败时生成明确 error block，不让单个附件阻断整轮请求。
- 修复附件解析路径的错误 `storage` 引用，并保持路径校验在 attachments 根目录下。

解析结果继续使用现有块结构：

```python
{"id": "...", "filename": "...", "kind": "text|image|document|error", "content": "..."}
```

### 4. 输入框工具栏与 Tooltip

`PromptComposer` 保留 `toolbar` 能力但只渲染附件按钮；删除 `activeTool`、`onToolToggle`、`mentions`、`tools` 等前端入口和面板接线。各页面保留 AttachmentPicker 的 ref 与 pending state，移除 MentionPicker/ToolPicker 的 import、状态和请求字段。

`Tooltip` 在 wrapper 收到 `pointerdown` 时调用隐藏逻辑；原有滚动隐藏继续保留。这样拖拽输入框手柄时，拖拽开始即清理 fixed 气泡，不改变 Tooltip 其他定位行为。

## 错误处理

- 上传格式/大小错误：沿用客户端预检和现有错误提示。
- 附件不存在、越权或物理文件缺失：继续返回 422/明确错误。
- PDF MinerU 失败：回退 PyMuPDF；两者都失败时只将该附件标为 error block。
- 图片模型调用失败：只将该图片标为 error block，并保留文件名；其他附件仍继续处理。
- 发送失败：用户消息仍保留附件摘要，流错误按现有流程展示；pending 区已清空，避免隐式重复发送。

## 测试与验收

### 前端

- AttachmentPicker 的 `clear()` 清空 pending chips 并回调空列表。
- UserMessage 在气泡上方渲染附件摘要。
- historyRestore 能从 metadata 恢复附件摘要，且不带正文。
- PromptComposer 只渲染添加附件按钮，不渲染提及和 Tools。
- Tooltip pointerdown 后隐藏提示。

### 后端

- PDF 分支能够调用解析路径，MinerU 异常时通过 PyMuPDF 回退提取文本。
- JPEG/PNG 请求使用对应 MIME。
- 附件上下文块仍被组装进 human message，文本/图片/PDF 分支均覆盖。

### 命令

```powershell
cd F:\agent_develop\CareerCrew\careercrew_web
npm run test -- --run
npm run build

cd F:\agent_develop\CareerCrew
$env:PYTHONPATH=(Get-Location).Path
F:\Python_develop\miniconda3\envs\careercrew\python.exe -m pytest tests/unit/test_attachment_context.py tests/unit/test_attachment_validation.py -q
```

不删除或覆盖工作区中已有的未提交用户改动；本次实现只在相关附件、消息、输入框和 Tooltip 文件中做必要修改。
