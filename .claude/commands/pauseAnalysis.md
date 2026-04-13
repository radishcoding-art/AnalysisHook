---
description: 暂停当前分析任务. 创建详细快照, 保存关键发现到 memory, 设置 plan.md paused=true.
allowed-tools:
  - Bash(python:*)
  - Read
  - Write
  - Edit
  - Glob
---

# /pauseAnalysis - 暂停当前分析任务

你即将执行任务暂停流程. 这会:

1. **校验**当前有 active 任务 (有 plan.md, status != empty/completed)
2. **创建空快照模板**到 `.claude/state/snapshots/<timestamp>.md`
3. **设置** plan.md frontmatter: `paused: true` / `paused_at` / `pause_snapshot`
4. **要求 AI** (你) 填完快照 + 写 memory + 汇报

## 执行第一阶段 (Python 脚本)

运行暂停脚本 (创建快照模板 + 改 plan.md 状态):

!`python .claude/hooks/pause_analysis.py`

## 第一阶段成功后, 你 (AI) 必须严格按顺序做以下事情

### Step 1: 读全部状态 + 记忆 (强制)

不依赖你的工作记忆, 必须重读. 用 **并行 Read** 同时读以下文件:

1. `Read .claude/state/task.md`
2. `Read .claude/state/plan.md`
3. `Read .claude/state/facts.md`
4. `Read .claude/state/dead_ends.md`
5. `Read .claude/state/environment.md`
6. **列 memory 目录** (用 Glob `~/.claude/projects/*/memory/*.md` 或 CLAUDE.md 配置的 memory_dir), Read 所有 .md
7. `Bash git log --oneline -20`

### Step 2: 填快照

打开 pause_analysis.py 输出里告诉你的 snapshot 路径 (形如 `.claude/state/snapshots/20260413_120000.md`), 用 **Edit / Write** 填完所有章节.

**每个章节的填写要求**:

- **任务摘要**: 一句话, 复述 task.md 的目标 (用自己的话, 不要复制)
- **当前 step 状态**:
  - **current_step**: plan.md 里的当前 step id
  - **已完成的部分**: 列举你在该 step 下已经做完的具体动作 (cite F<id>)
  - **剩余未做的部分**: 列举该 step 还没做的具体动作
  - **下一个具体动作**: 恢复后第一件要做的事 (越具体越好, 例如 "在 0x7ff6... 设 hbp w 4 然后触发战斗", 不是 "继续分析")
- **关键 facts**: 按相关度排序的 F<id> 列表, 每条一行, 含一句话描述
- **已证伪方向**: 列出迄今为止所有 D<id>, 提醒未来不要重试
- **当前环境状态**: 从 environment.md 摘要 (PID / 模块基址 / 关键句柄)
- **未解决的问题**: 当前还没答案的 open questions, 恢复后要重点处理
- **下次恢复关键上下文**: 一段话, 让未来的你 (AI) 重启会话后秒速进入状态. 包含: 任务在哪一步 / 关键 F<id> 是什么 / 下一动作是什么 / 注意什么坑
- **阻塞 / 等待**: 如果暂停时正在等用户做某事, 在这里写明

**质量要求**:

- 每个 fact 必须 cite F<id>, 不要写裸技术细节 (Stop hook L2 也会抓)
- 不允许 hedging 词 (可能 / 似乎 / 因此 / ...) (Stop hook L1 抓)
- 不允许结论声明触发词 (已完成 / 已验证 / ...) 除非走完 L5 自证流程

### Step 3: 写 memory

把快照里的关键发现作为 **project / reference** 类型 memory 存到 memory 目录 (跨会话长期记忆). **不要写 user / feedback 类型** (那是用户行为相关).

memory 内容应该包含:

- 任务的本质 (project 类型): 我们在做什么逆向 / 分析 / 调试
- 当前阶段的关键发现 (project 类型): 关键 F<id> 和它们的语义
- 重要资源指针 (reference 类型): 关键模块 / 关键地址 / 工具配置位置

每个 memory 一个文件, 按主题命名 (例如 `current_task_state.md`, `key_findings.md`, `tool_setup.md`).

更新 memory 目录的 `MEMORY.md` 索引 (一行一条 pointer).

### Step 4: 汇报

简短告诉用户:

- 快照路径
- 写了哪些 memory (列出文件名)
- 用户下次想恢复时执行 `/resumeAnalysis` 即可

## 强制规则

- **plan.md `paused: true` 后, Stop hook L8 不再 block 你说 '休息 / 暂停 / 改天再' 等词**. 但你仍然不允许说 '任务完成 / 已完成', 因为任务实际没完成.
- **执行此命令期间, 不允许做关键 tool call**. 这是状态总结时间, 不是分析时间.
- **快照必须真实详细**. 偷懒写空段或一句话 → 下次恢复时找不到上下文 → 浪费时间. 写得越详细, 恢复越快.
- **此命令不重置 plan.md / facts.md / dead_ends.md**. 它们保留, 等 /resumeAnalysis 时继续用.
