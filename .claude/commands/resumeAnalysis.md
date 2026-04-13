---
description: 恢复之前暂停的分析任务. 强制 AI 重读所有状态文件 + 快照 + 记忆, 输出恢复 brief, 等用户确认后才能继续.
allowed-tools:
  - Bash(python:*)
  - Bash(git:*)
  - Read
  - Glob
  - Edit
---

# /resumeAnalysis - 恢复暂停的分析任务

你即将执行任务恢复流程. 这会:

1. **校验** plan.md 是 paused 状态 (有 pause_snapshot 字段)
2. **校验**快照文件存在
3. **清除** plan.md `paused` 标志 (但保留 paused_at + pause_snapshot 作 audit trail)
4. **要求 AI** (你) 重读全部状态文件 + 快照 + 记忆, 输出恢复 brief, 等用户确认

## 执行第一阶段 (Python 脚本)

运行恢复脚本 (校验 + 清 paused 标志 + 输出快照预览):

!`python .claude/hooks/resume_analysis.py`

## 第一阶段成功后, 你 (AI) 必须严格按顺序做以下事情

### Step 1: Read 暂停快照 (最高优先级)

resume_analysis.py 输出了快照路径. 用 **Read** 完整读快照, **不要只看 preview**. 快照含恢复所需的所有上下文.

### Step 2: 并行 Read 全部状态文件 (强制, 不依赖记忆)

恢复时必须重读, 防漂移. 用**单次消息发多个 Read tool call** 提速:

1. `Read .claude/state/task.md` (任务目标, 防漂移)
2. `Read .claude/state/tool_constraints.md` (工具约束)
3. `Read .claude/state/plan.md` (计划)
4. `Read .claude/state/facts.md` (已证实事实)
5. `Read .claude/state/dead_ends.md` (已证伪方向, 不要重试)
6. `Read .claude/state/environment.md` (环境状态, 看是否过期)

### Step 3: Read 跨会话记忆

- 用 **Glob** 列 memory 目录 (CLAUDE.md `memory_dir` 配置, 通常是 `~/.claude/projects/<slug>/memory/*.md`)
- **Read** 所有 memory 文件
- 重点关注 project / reference 类型的 memory (它们可能是上次 /pauseAnalysis 写入的)

### Step 4: 检查期间的变化

- `Bash git log --oneline -20` (看是否有 commits 进展)
- 看 environment.md 的 `last_verified_at`, 如果 > 5 分钟需要重新验证 (PreToolUse 也会要求)
- 用 **Glob** 看 `.claude/state/` 下的 mtime, 是否有用户手动改了 facts.md / plan.md

### Step 5: 输出 "恢复 brief"

在回复中输出以下结构 (用 markdown):

```markdown
## 恢复 brief

### 任务目标

(一句话复述 task.md, 用自己的话)

### 暂停时的状态

(引用快照里的 '当前 step 状态' 和 '下次恢复关键上下文')

### 当前 plan

- **current_step**: <S00X>
- **已完成 fact**: F001, F002, ...
- **未完成步骤**: ...
- **未解决的问题**: ...

### 期间的变化

- git log 显示: ... (或 "无变化")
- environment.md last_verified_at: ... (是否过期)
- 文件 mtime 变化: ... (或 "无")

### 第一个具体动作 (待用户确认后才执行)

(具体到 tool call 级别. 例如: "Bash 用 mcp**x64dbg**ListDebuggerCommands ping x64dbg 看进程是否还在, 然后用 mcp**x64dbg**GetAllRegisters 读 0x7ff6... 处的寄存器")
```

### Step 6: 停下汇报, 等用户确认

**强制规则**: 输出完恢复 brief 后**立即停下**, 不允许做任何关键 tool call. 等用户:

- 确认 brief 准确 (任务目标 / 当前进度 / 下一动作 没问题)
- 或者纠正你的理解 (例如 "目标变了" / "这个 fact 不对了")

用户回复 "继续" / "ok" / "对的" / "开始吧" 之类的, 你才能 advance + 做关键 tool call.

## 强制规则

- **不允许跳过任何 Read 步骤**. 即使你"觉得"还记得上次的状态, 也必须重读. 工作记忆会漂移, 文件不会.
- **不允许在输出 brief 前做关键 tool call**. PreToolUse hook 不会拦你 (因为 plan.md current_step 还在), 但 Stop hook 的 cite 机制会让你回复里的任何裸 fact 都被 block.
- **brief 必须真实, 不允许凭快照胡编**. 如果快照不完整, 在 brief 里 explicit 写 "快照缺 X, 需要补".
- **此命令不重置 plan.md / facts.md / dead_ends.md**. 它们应该跟你暂停时一致, 直接继续用.
- **快照文件保留**, 不删除. 这是 audit trail.

## 如果遇到错误

- 脚本说 `paused: false` → 当前不在暂停状态. 如果想暂停请 /pauseAnalysis. 如果想新任务请 /newAnalysis.
- 脚本说快照文件不存在 → 检查 `.claude/state/snapshots/` 目录, 找最近的快照, 手动改 plan.md `pause_snapshot` 字段后重试.
