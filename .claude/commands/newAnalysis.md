---
description: 重置所有状态文件并初始化新分析任务. 备份当前状态到 archive/.
allowed-tools:
  - Bash(python:*)
  - Read
  - Edit
---

# /newAnalysis - 新任务初始化

你即将执行新任务初始化. 这会:

1. **备份**当前的 plan.md / facts.md / dead_ends.md / environment.md / task.md 到 `.claude/state/archive/<timestamp>/`
2. **重置**这 5 个文件为 templates 里的初始模板
3. **不动** tool_constraints.md (项目级永久) / hook.log / expired_facts.md / CLAUDE.md

## 执行

运行重置脚本:

!`python "$CLAUDE_PROJECT_DIR/.claude/hooks/new_analysis.py"`

## 重置完成后

- 用户应该立即编辑 `.claude/state/task.md` 写下新任务的详细描述
- 编辑完成后告诉你 (Claude)
- 你 (Claude) 必须严格按以下顺序工作:

### Step 1: 读两个文件 (强制)

1. `Read .claude/state/task.md` - 完整读完, 理解任务
2. `Read .claude/state/tool_constraints.md` - 完整读完, 理解工具约束

### Step 2: 在 plan.md 输出 "任务理解 checklist"

打开 `.claude/state/plan.md`, 在 `## 任务理解 checklist` 段下面填:

- **我对任务目标的理解** (用自己的话复述, 不复制 task.md)
- **我对范围的理解** (允许做什么 / 禁止做什么)
- **我对输入 / 输出的理解**
- **关键假设** (我打算验证, 不是默认相信)
- **不明确的地方** (open questions)
- **第一个 step 草稿** (description / rationale / verification_criteria / expected_tools)

### Step 3: 停下汇报

输出完任务理解后, 立刻停下. 不允许做任何关键 tool call. 等用户审.

### Step 4: 等用户批准

用户审过你的任务理解后, 会改 `plan.md` frontmatter:

```yaml
task_understanding_acked: true
```

### Step 5: 收到批准后开始工作

`task_understanding_acked` 改成 `true` 后, 你才能:

- 在 `plan.md` 的 `steps[]` 写第一个正式 step
- 设 `current_step` 为第一个 step 的 id
- 开始做关键 tool call

## 强制规则

- **`task_understanding_acked: false` 时**: PreToolUse hook 拒绝任何 `mcp__*` / `mcp__x64dbg__*` / `mcp__CheatEngine__*` / `mcp__IDAProMCP__*` 类工具调用
- **未读 task.md 就开始工作**: Stop hook 没法直接抓, 但用户审 plan.md 时会发现你的"任务理解" 是空的或瞎写, 会要求重做
- **不允许跳过 task understanding 步骤**: 即使用户口头说"开始吧", 也必须先有 `task_understanding_acked: true`
