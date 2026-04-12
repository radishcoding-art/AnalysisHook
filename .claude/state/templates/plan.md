---
# 当前任务的步骤计划. 没有 plan / current_step=null 或 task_understanding_acked=false → PreToolUse hook 拒绝任何关键 tool call.
task_file: ".claude/state/task.md" # 任务的源头, AI 必须先读
task_understanding_acked: false # 用户审过 AI 的"任务理解 checklist" 后改 true. 默认 false 阻止任何 tool call.
created_at: ""
last_updated: ""
status: empty # empty | active | completed | blocked
current_step: null # 字符串 (S001 / S002 ...), null 表示无 active step
last_id: 0 # 自动递增 step id, 永不复用
steps: []
---

# 当前 plan

(空, 等 AI 第一次写入)

## 工作流概述

新任务的标准流程:

1. 用户编辑 `.claude/state/task.md` 写下任务详细描述
2. AI 第一次回复必须先 read task.md + tool_constraints.md
3. AI 在本文件输出 "任务理解 checklist" 段 (在下方 ## 任务理解 区域写)
4. AI 停下汇报, 等用户审
5. 用户审过, 改 frontmatter `task_understanding_acked: true`
6. AI 收到信号后, 写第一个 step 到 `steps[]`, 设 `current_step`
7. AI 执行 step, 产生 fact, 更新 `current_step`, 重复

## 任务理解 checklist (AI 写, 用户审)

(等 AI 在第一次回复时填. 模板:)

```
### 我对任务的理解
- **任务目标**: <用自己的话复述, 不复制 task.md>
- **范围**: 允许 ... / 禁止 ...
- **输入**: ...
- **输出 / 验收**: ...

### 关键假设 (我打算验证, 不是默认相信)
- 假设 1: ...
- 假设 2: ...

### 不明确的地方
- 问题 1: ...
- 问题 2: ...

### 第一个 step 草稿 (待用户批准后才写到 steps[])
- description: ...
- rationale: ...
- verification_criteria: ...
- expected_tools: [...]
```

## step 的 schema

每个 step 是 `steps[]` 列表中的一个对象, 完整字段:

```yaml
- id: S001 # 自动递增, 永不复用
  description: "<这一步要做什么, 一句话>"
  rationale: "<为什么做这一步, 基于哪些 fact 或 assumption. 必须 cite F<id> / memory/<file>.md / user-told>"
  verification_criteria: "<完成的判定条件. 必须明确, 能用 tool 输出验证>"
  expected_tools: # 这一步预期使用的 tool 列表
    - "mcp__example__..."
  status: pending # pending | active | completed | failed
  created_at: "<ISO 8601>"
  started_at: null # status → active 时填
  completed_at: null # status → completed 时填
  result_facts: [] # 完成时填: 产生的 F<id> 列表
  failed_reason: null # 失败时填. 同时 append 到 dead_ends.md
```

## 工作流强制规则

1. **任何关键 tool call 之前**: PreToolUse hook 检查 `task_understanding_acked` 必须是 true + `current_step` 必须非 null. 任一不满足 → 拒绝.
2. **新任务 / 子任务时**: append 新 step 到 `steps[]`, id = `last_id + 1`, status=pending. 更新 frontmatter `last_id`.
3. **用户审 plan 通过后**: 把 `current_step` 设为该 step 的 id, status 改成 active, 填 `started_at`.
4. **step 完成**: 当 `verification_criteria` 满足 (有具体的 F<id> 证据), 把 status 改成 completed, 填 `completed_at` 和 `result_facts`. advance `current_step` 到下一个 pending step (或 null 如果 plan 完成).
5. **step 失败**: status → failed, 填 `failed_reason`, 把这个 dead end append 到 dead_ends.md.

## 强制规则 (复述 CLAUDE.md)

- 没有 plan 或 `current_step=null` → PreToolUse hook 拒绝关键 tool 调用
- `task_understanding_acked=false` → PreToolUse hook 拒绝关键 tool 调用
- step 的 `verification_criteria` 必须明确
- step 的 `rationale` 必须 cite 已有 F<id> / memory / user-told
- step 失败必须立刻 dead_ends.md, 不允许"换个角度再试同一个 step"

## 重要陷阱

- **沉没成本**: step 已经 failed, 不允许通过"我再试一次"重新 active
- **跑偏**: 新 step 的 description 必须跟 task.md 的目标直接相关
- **任务理解漂移**: 中途如果发现任务理解错了, 必须重新写 "任务理解 checklist", 把 `task_understanding_acked` 改成 false 重新审
