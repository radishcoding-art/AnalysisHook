# AnalysisHook - Claude Code AI 工作约束系统模板

通用的 AI 约束系统, 防止 AI 在长期复杂项目里跑偏 / 凭假设行动 / 产生幻觉.
复制到任何项目根目录即可启用.

## 目录结构

```
AnalysisHook/
├── README.md                               # 本文件
├── CLAUDE.md                               # 项目级约束规则 (frontmatter 给 hook 解析, 正文给 AI 看)
└── .claude/
    ├── settings.json                       # Hook 注册 (4 个 hook)
    ├── hooks/                              # Hook 脚本 (Python)
    │   ├── _lib.py                         # 共享库 (状态文件路径 / 日志 / yaml frontmatter / stdin JSON)
    │   ├── session_start.py                # 会话开始时注入状态文件 + memory + git log
    │   ├── user_prompt_submit.py           # 每次用户输入注入任务状态提醒
    │   ├── pre_tool_use.py                 # Tool call 前的 7 项检查 (含 config 文件保护 + plan 切换守卫)
    │   ├── stop.py                         # AI 结束回复前的 8 层检查 (L1-L8)
    │   ├── new_analysis.py                 # /newAnalysis 命令的实现脚本
    │   ├── pause_analysis.py               # /pauseAnalysis 命令的实现脚本
    │   └── resume_analysis.py              # /resumeAnalysis 命令的实现脚本
    ├── commands/
    │   ├── newAnalysis.md                  # /newAnalysis slash command 定义
    │   ├── pauseAnalysis.md                # /pauseAnalysis slash command 定义
    │   └── resumeAnalysis.md               # /resumeAnalysis slash command 定义
    ├── logs/                               # Hook 日志目录 (运行时自动创建 hook.log)
    └── state/
        ├── tool_constraints.md             # 工具白名单 / 禁止 (项目永久, 不随 /newAnalysis 重置)
        ├── snapshots/                      # /pauseAnalysis 创建的暂停快照存放目录 (audit trail, 不删除)
        └── templates/                      # 状态文件模板 (/newAnalysis 时复制到 state/ 作为初始状态)
            ├── task.md                     # 任务定义模板
            ├── plan.md                     # 计划模板 (含 paused / paused_at / pause_snapshot 字段)
            ├── facts.md                    # 事实库模板
            ├── dead_ends.md                # 已证伪方向模板
            └── environment.md              # 环境状态模板
```

## 快速开始

### 1. 复制到新项目

把整个 `AnalysisHook/` 目录的内容复制到目标项目根:

```powershell
# PowerShell
Copy-Item -Path "E:\Bak\AnalysisHook\*" -Destination "E:\Path\To\NewProject\" -Recurse -Force
```

或用 robocopy:

```cmd
robocopy "E:\Bak\AnalysisHook" "E:\Path\To\NewProject" /E
```

目标项目根结果:

```
NewProject/
├── CLAUDE.md                    ← 新增
├── .claude/                     ← 新增
│   ├── settings.json
│   ├── hooks/
│   ├── commands/
│   ├── state/
│   └── logs/
└── (项目原有文件)
```

### 2. 初始化状态文件

打开 Claude Code 后, 运行:

```
/newAnalysis
```

这会:

- 从 `templates/` 复制 5 个文件到 `state/` (task.md / plan.md / facts.md / dead_ends.md / environment.md)
- 提示你编辑 `task.md` 填任务描述

### 3. 填写 task.md

按 10 个章节填:

1. 任务目标 (一句话)
2. 详细描述
3. 范围 (允许 / 禁止)
4. 输入
5. 输出 / 验收标准
6. 关键约束 / 风险点
7. 工具与方法
8. 关键已知 / 假设
9. 不明确的地方
10. 参考资料

写得越详细, AI 偏离的概率越低.

### 4. 让 AI 读任务

跟 AI 说 "任务写完了". AI 会:

1. 读 `task.md` + `tool_constraints.md`
2. 在 `plan.md` 输出 "任务理解 checklist"
3. 停下等你审

### 5. 审批任务理解

查看 AI 的任务理解. 如果 OK, 编辑 `plan.md` frontmatter:

```yaml
task_understanding_acked: true
```

改成 `true` 后, AI 才能开始写 step + 做关键 tool call.

### 6. 开始工作

AI 会写 step → 做 tool call → 写 fact → 更新 current_step → 循环.

期间 hook 会阻止:

- 任何 hedging 词 ("可能" / "似乎" / "因此" / ...)
- 含技术细节但没 cite F<id> 的断言
- cite 不存在的 F<id>
- 张冠李戴的 cite (fact 的 subject 不含当前地址)
- **结论声明但无自证段 (L5)** - 默认要求自证段三段 (结论 / 依据 / 自我检查); 可选 reviewer 调用强制. 详见 "结论自证机制"
- **回复含数字运算但本回合未调 python 验证 (L6)** - 防心算错误, 详见 "计算强制验证"
- **未证伪当前方案就切换方向 (L7)** - 防情绪化换路, 详见 "方案切换证据要求"
- **任务未完成就自发停止 (L8)** - 防 AI 主动 "休息 / 暂停 / 改天再", 详见 "禁止自发停止"
- CE 调试类工具 (只允许只读分析)
- 没 plan 就做关键 tool call
- 跟 dead_ends.md 冲突的尝试
- 5 个 tool call 没新 fact 不汇报 (软警告, 不 block)

### 7. 结论自证机制 (L5)

当 AI 在回复里说 "结论" / "已完成" / "已验证" / "最终" 等触发词时, Stop hook 会强制要求:

**回复必须含 `## 自证` (或 `## 自证与审查`) 段**, 正文 ≥ 100 字符, 至少包含三个子段:

1. **结论** — 一句话复述
2. **依据的 facts** — F<id> 列表 (cite)
3. **自我检查** 4 项 — 上下文偏见 / sunk cost / 逻辑漏洞 / 确认偏见

这是防止 AI "得出看似合理但实际有偏见 / 逻辑漏洞 / 证据不足" 结论的最后防线.

**外部 reviewer 调用是可选的** (frontmatter `conclusion_self_review_check.require_reviewer_call`):

- `false` (默认, 用户偏好节省 token): 只要求自证段三段, **不**调用外部 reviewer
- `true`: 同时强制本回合 transcript 含 `superpowers:code-reviewer` (或白名单内其他) 的 Agent 调用, AI 必须 quote reviewer 输出, 不通过要循环修复 (最多 3 轮)

详细格式见 CLAUDE.md 的 "结论自证 (L5 检查)" 章节.

### 8. 计算强制验证 (L6)

心算 / 手算是 AI 最容易出错的环节之一 (例如把 `0x1000 + 0x20` 算成 `0x1200`).
L6 的机制:

1. 扫回复中的**算术痕迹** (正则匹配):
   - 十进制算术 `\d+ [+\-*/] \d+ =`
   - 十六进制算术 `0x... [+\-*/] 0x...` / `0x... [+\-*/] \d+`
   - 结果声明词: `计算得` / `共 N 字节` / `等于 N` / `约 N` / `合计 N` ...
2. 扫本回合的 tool_use 事件, 是否调用过 `Bash(python ...)` 或 `Bash(python3 ...)`
3. **有算术痕迹 && 无 python Bash → block**

**豁免**: 代码块 (` ``` `) / 行内 code (`` ` ``) / `>` 引用行自动跳过.
如果只是贴示例代码或引用用户原话, 不会误伤.

**修复**: 任何数字运算 (加减乘除 / 位运算 / 地址偏移 / 字节长度 / 进制转换 / 百分比 / 时间换算)
必须用 `Bash(python -c "...")` 执行一次, 把 python 输出贴回回复, 再写结论.

### 9. 方案切换证据要求 (L7)

AI 在长任务中容易出现 "挖了太多层, 换个思路" 的情绪化切换 — 在当前方案没有被**事实**证伪时就放弃, 反复横跳.
L7 的机制:

1. 扫回复中的**切换意图短语** (正则匹配):
   - `改走` / `换思路` / `另辟` / `放弃当前` / `重新规划` / `绕过这个`
   - `这条路径 ... 太绕远 / 走不通`
   - `像 F\d+ 一样` (纯类比不等于证据)
2. 触发后, 必须满足以下**任一**条件才放行:
   - **(a) 本回合 Edit/Write 了 `dead_ends.md`** — 用 fact 把当前方案登记为死胡同
   - **(b) 回复含 `## 方案切换评估` section**, 正文 ≥ 100 字符 + 至少 1 个 F<id> cite, 写明当前方案 / 已验证 / 剩余步骤 / 不可行理由 / 新方向
   - **(c) 回复含 `user-told:` cite** — 用户明确授权切换
3. 三条都不满足 → block

**豁免**: 头脑风暴 / 讨论多方案时句首加 `头脑风暴` / `讨论` / `考虑` / `比较` / `权衡` / `备选` 等前缀, L7 自动跳过.
代码块 / 行内 code / `>` 引用行也豁免.

**目的**: 让 "放弃当前路径" 变成一个必须出示证据的决策, 而不是情绪化逃避.
配合 L6 (算术必须 python 验证), 两层共同强制 AI 的 reasoning 走在 fact-driven 轨道上.

### 10. 禁止自发停止 (L8) + 暂停 / 恢复工作流

AI 在长任务中容易出现 "今天先到这, 改天再继续" 的自发停止 — 任务没完成就主动想休息. L8 拦截这种行为, 强制 AI 工作到任务真正完成或用户明确暂停.

**L8 机制**:

1. 扫回复中的**自发停止短语** (正则匹配):
   - `今天(先)?到这` / `今天就这样`
   - `(暂时|先)?休息 / 歇会 / 喘口气`
   - `(改天|明天|晚点|稍后|下次)再继续`
   - `(我|先)?暂停一下`
   - `我累了` / `不想继续`
   - `先 break`
   - `(收工|下班|打烊)`
2. 触发后, 必须满足以下**任一**条件才放行:
   - **(a) plan.md `status: completed`** — 任务已完成 (L5 也会要求自证段)
   - **(b) plan.md `paused: true`** — 用户已通过 `/pauseAnalysis` 主动暂停
   - **(c) 本回合最后一条 user message 含暂停指示词** ("休息 / 暂停 / 先到这 / ...")
   - **(d) 回复含 `user-told:` cite** — 引用用户的暂停指令
3. **handoff 豁免**: 回复中含等待用户操作的句式 ("等你触发后告诉我 / 等你确认 / 请去做 X 后回复 / 请运行 Y / 请触发") 视为合法 handoff, **不算自发停止**, 直接放行
4. 三条都不满足且无豁免 → block

**与暂停 / 恢复工作流配合**:

| 用户意图                | 操作                                                        |
| ----------------------- | ----------------------------------------------------------- |
| 想暂停, 跨会话休息      | 调用 `/pauseAnalysis`                                       |
| 想恢复之前暂停的任务    | 调用 `/resumeAnalysis`                                      |
| 临时让 AI 停下 (一句话) | 在消息里说 "休息 / 暂停 / 先到这", L8 扫到自动豁免          |
| AI 想停 (任务没完成)    | **不允许**. L8 会 block, AI 必须继续工作或改成 handoff 句式 |

### 11. /pauseAnalysis 工作流

`/pauseAnalysis` 触发**详细暂停流程**, 设计目的是跨会话保存状态, 让下次恢复时秒速进入.

**第一阶段 (Python 脚本)** — `.claude/hooks/pause_analysis.py`:

1. 校验当前有 active 任务 (plan.md 含 `task_understanding_acked: true` 且 `status` 不是 `empty/completed`)
2. 创建 `.claude/state/snapshots/<YYYYMMDD_HHMMSS>.md` 空快照模板
3. 原子改 plan.md frontmatter: `paused: true` / `paused_at: <ISO>` / `pause_snapshot: <相对路径>`
4. 输出引导给 AI

**第二阶段 (AI 必须做)**:

1. **Read** 全部状态文件 (plan / facts / dead_ends / environment / task) + memory 目录 + git log
2. **Edit / Write** 暂停快照, 填完所有章节:
   - 任务摘要
   - 当前 step 状态 (已完成 / 剩余 / 下一动作)
   - 关键 facts (cite F<id>)
   - 已证伪方向
   - 当前环境状态
   - 未解决的问题
   - **下次恢复关键上下文** (一段话, 让未来 AI 秒速进入状态)
   - 阻塞 / 等待
3. **写 memory**: project / reference 类型 (任务本质 / 关键发现 / 重要资源指针), 不写 user / feedback
4. **汇报**: 快照路径 / memory 文件名 / 提示用户下次 `/resumeAnalysis`

**暂停后状态**: plan.md `paused: true` → L8 不再 block AI 说 "休息 / 暂停 / 改天再". 但 AI 仍然不允许说 "完成" (那是 L5 的事).

### 12. /resumeAnalysis 工作流

`/resumeAnalysis` 触发**详细恢复流程**, 强制 AI 重读所有状态防漂移.

**第一阶段 (Python 脚本)** — `.claude/hooks/resume_analysis.py`:

1. 校验 plan.md `paused: true` + `pause_snapshot` 字段非空
2. 校验快照文件存在
3. 原子改 plan.md frontmatter: `paused: false` (但保留 `paused_at` + `pause_snapshot` 作 audit trail)
4. 输出快照内容预览 + 强制行为指南给 AI

**第二阶段 (AI 必须做)**:

1. **Read 暂停快照** (完整读, 不只看 preview)
2. **并行 Read 全部状态文件** (task / tool_constraints / plan / facts / dead_ends / environment) — 不依赖工作记忆, 防漂移
3. **Read 跨会话记忆** (memory 目录所有 .md)
4. **Bash** 看期间变化 (`git log --oneline -20`, environment.md `last_verified_at` 是否过期, 文件 mtime)
5. **输出 "恢复 brief"** (在回复中):
   - 任务目标 (复述)
   - 暂停时的状态 (引用快照)
   - 当前 plan + 已完成 fact + 未完成步骤
   - 期间的变化
   - **第一个具体动作** (cite F<id> / step id, 具体到 tool call 级别)
6. **停下汇报**, 等用户确认 brief 后才能 advance + 做关键 tool call

**audit trail**: 暂停快照永不删除. 多次 pause/resume 累积出完整的工作历史.

**与 PreToolUse plan 切换守卫的配合 (双层防御)**:

- **L7 (Stop 层)** 抓**声明式切换** — AI 在回复文字里写 "改走 / 换思路 / 像 F<id> 一样". 在回合末拦截, 强制 AI 重写回复
- **PreToolUse 检查 0.5 (tool 层)** 抓**静默切换** — AI 不声明, 直接 Edit/Write `plan.md` 把 `current_step` 从 S001 改为 S002 但 S001 还是 `active`. 在工具执行前拦截, 避免已经浪费 tool call 才在回合末重写

两层合起来覆盖: (a) 声明式切换 → L7 抓; (b) 通过改 plan.md 的 tool 层切换 → PreToolUse 抓.
唯一漏网的是: AI 不改 plan.md 也不声明, 直接对新方向发 tool call — 这种情况 plan.md 的 `current_step` 仍指向旧 step, 后续 Stop hook 的 cite 机制会暴露 "回复里的新 fact 不在 current step 的范围内", 用户也能看出 plan 和实际工作脱节.

## 关键设计

### 4 个 Hook

| Hook                 | 触发                          | 作用                                                                                                            |
| -------------------- | ----------------------------- | --------------------------------------------------------------------------------------------------------------- |
| **SessionStart**     | 会话开始 / 恢复 / 清空 / 压缩 | 注入全部状态文件 + memory + git log 到 system prompt                                                            |
| **UserPromptSubmit** | 用户每次输入                  | 注入任务摘要 + task_understanding 状态 + current_step + dead_ends 标题                                          |
| **PreToolUse**       | 每次 tool call 前             | 7 项检查: config 保护 / plan 切换守卫 / task understanding / 工具白名单 / plan / dead_ends / environment        |
| **Stop**             | AI 结束回复前                 | 8 层检查: 模糊词 / cite 形式 / cite 真实 / cite 相关性 / 结论自证+审查 / 计算强制 / 方案切换证据 / 禁止自发停止 |

### 8 层 Stop 检查

| 层     | 检查                                                                    | 抓什么                                                                                                                                                                                                                                                                                                                           |
| ------ | ----------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **L1** | 模糊词黑名单 (`forbidden_hedge_examples`)                               | `可能` / `似乎` / `觉得` / `因此` / `推断` / `综上` 等                                                                                                                                                                                                                                                                           |
| **L2** | 事实主张必须 cite (`fact_claim_patterns` + `required_citation_pattern`) | 含 `0x...` / PID / 函数名 / 模糊指代 (`它` / `这个`) 的句子必须带 `F<id>` / `memory/file.md` / `user-told`, 除非句首是行动词 (`下一步` / `我打算` ...)                                                                                                                                                                           |
| **L3** | cite 真实性                                                             | 引用 `facts.md` 不存在的 F<id>                                                                                                                                                                                                                                                                                                   |
| **L4** | cite 相关性 (subject 匹配)                                              | 张冠李戴, 引用 F012 但句子里的技术细节不在 F012.subject 列表                                                                                                                                                                                                                                                                     |
| **L5** | 结论自证 (`conclusion_self_review_check`)                               | 回复含 "结论" / "已完成" / "已验证" / "搞清楚" 等触发词 → 必须满足: (a) `## 自证` header 存在 (b) 段内正文 ≥ 100 字符, 含 `结论` / `依据 (F<id>)` / `自我检查 (4 项)` 三个子项. **可选**: `require_reviewer_call: true` 时还要求本回合调用过 `superpowers:code-reviewer` (精确匹配白名单). 默认 `false`                          |
| **L6** | 计算强制验证 (`arithmetic_verification_check`)                          | 回复含数字运算痕迹 (算术表达式 / 结果声明词 `共 N 字节` / `等于 N` / `约 N` ...) 但本回合未调用 `Bash(python ...)` → block. 代码块 / 行内 code / `>` 引用行自动豁免                                                                                                                                                              |
| **L7** | 方案切换证据要求 (`approach_switch_check`)                              | 回复含切换意图 (`改走` / `换思路` / `放弃当前` / `重新规划` / `像 F<id> 一样` ...) 但无证据 → block. 三条通过路径: (a) 本回合 Edit/Write `dead_ends.md`; (b) 回复含 `## 方案切换评估` 段 + 正文 ≥ 100 字符 + F<id> cite; (c) cite `user-told:`. 头脑风暴 / 讨论前缀豁免                                                          |
| **L8** | 禁止 AI 自发停止 (`self_stop_check`)                                    | 回复含自发停止短语 (`今天先到这` / `休息` / `改天再` / `暂停` / `我累了` / `先 break` ...) 但任务未完成 / 未暂停 → block. 通过条件: (a) plan.md `status: completed`; (b) plan.md `paused: true`; (c) 本回合 user message 含暂停指示词; (d) `user-told:` cite. handoff 句式 (`等你触发后告诉我` / `请运行 X` / `请触发`) 自动豁免 |

**两条 Stop hook 额外行为**:

- **合并本回合 text**: Stop hook 读本回合**所有** assistant text (从最后一条 user message 往后) 做 L1-L8 检查, 防 AI 把违规内容藏在倒数第二条消息绕过
- **Retry 降级**: 连续 block 3 次 → 强制放行避免死循环 (stderr 会警告, 下轮 AI 必须主动补修)
- **Tool call 阈值**: 本回合 ≥ 5 个 tool call 但 `facts.md` 没新增 → log 警告 (目前软警告, 不 block, 因为无 baseline 机制)

### 7 项 PreToolUse 检查

所有检查按优先级顺序执行, 任一 block → exit 2 拒绝 tool call.

| 优先级 | 检查                             | 触发条件                                                                                                                                                                                                                      |
| ------ | -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0      | **config 文件保护**              | Edit/Write/MultiEdit/Bash 碰到 `CLAUDE.md` / `task.md` / `tool_constraints.md` / hook 脚本 / `settings.json` → block; Edit `plan.md` 的 `task_understanding_acked` 字段 → block (含分段 Edit / Bash cd 绕过 / 大小写绕过检测) |
| 0.5    | **plan 切换守卫 (静默切换拦截)** | Edit/Write/MultiEdit 改 `plan.md` 时, 如果 `current_step` 从 A 变为 B (A 非空 A≠B), post-edit 内容里 A 的 status 必须是 `completed` 或 `failed`, 否则 block. 配合 Stop hook L7 双层防御                                       |
| 1      | **task_understanding_acked**     | 关键工具 + `plan.md` 字段为 false → block                                                                                                                                                                                     |
| 2      | **工具白名单**                   | `mcp__*` 前缀 + `tool_constraints.md` 白名单不含 → block                                                                                                                                                                      |
| 3      | **require_plan**                 | 关键工具 + `current_step` 为 null → block                                                                                                                                                                                     |
| 4      | **check_dead_ends**              | 关键工具 + 跟 `dead_ends.md` 某条冲突 (tool_name + 关键 input 值都出现在 dead end 段里) → block                                                                                                                               |
| 5      | **environment 时效**             | 关键工具 + `last_verified_at` 超过 5 分钟 → block (首次为空放行避免死锁)                                                                                                                                                      |

**"关键工具" 定义**: 所有 `mcp__*` 默认是关键工具, 豁免列表: `mcp__Pencil__` (UI 设计) / `mcp__Context7__` (文档查询) / `mcp__ide__` (IDE 辅助). 新加的 MCP 家族自动受 task lock + plan + dead_ends + environment 检查保护.

### 状态文件分类

| 类型     | 文件                                                    | 随 /newAnalysis 重置 | 说明               |
| -------- | ------------------------------------------------------- | -------------------- | ------------------ |
| 任务定义 | `task.md`                                               | ✓                    | 用户填             |
| 工具约束 | `tool_constraints.md`                                   | ✗                    | 项目永久           |
| 工作状态 | `plan.md`, `facts.md`, `dead_ends.md`, `environment.md` | ✓                    | AI 写              |
| 长期记忆 | `~/.claude/projects/<slug>/memory/*.md`                 | ✗                    | auto memory + 手动 |
| 日志     | `.claude/logs/hook.log`                                 | ✗                    | 运行时 append      |

## 配置要点

### CLAUDE.md frontmatter 关键字段

```yaml
task_file: ".claude/state/task.md" # 任务定义文件
task_summary: "见 .claude/state/task.md" # 一行摘要
forbidden_hedge_examples: [...] # 模糊词黑名单 (非穷尽)
fact_claim_patterns: [...] # 事实主张检测正则
required_citation_pattern: "F\\d{3,}|..." # cite 必须匹配的正则
action_phrase_exemptions: [...] # 行动句子豁免短语
tool_call_threshold: 5 # 5 个 tool call 没新 fact 警告
must_read_on_session_start: [...] # 会话开始必读的文件
memory_dir: auto # auto = 根据 CLAUDE_PROJECT_DIR 自动计算

conclusion_self_review_check: # L5 结论自证
  enabled: true
  trigger_phrases: # 正则列表, 覆盖同义词变体 (防 AI 换词绕过)
    - "(我的|本次|最终|整体|以上)?结论"
    - "(已经|已)(验证|确认|确定|证实)"
    - "(彻底|终于|已经)?搞清楚"
    - "(据此|由此|综上)(可知|可得|得出|推出|判断)"
    # ... (见 CLAUDE.md 完整列表)
  required_self_review_sections: ["## 自证", "## 自证与审查", "## Self-Review"]
  # 是否强制外部 reviewer subagent 调用. false = 只要求自证段三段, 不要求 reviewer (节省 token)
  require_reviewer_call: false
  # 仅在 require_reviewer_call=true 时生效
  allowed_reviewer_subagents:
    - "superpowers:code-reviewer"
    - "code-reviewer"

arithmetic_verification_check: # L6 计算强制验证
  enabled: true
  arithmetic_patterns: # 算术表达式正则 (明确含运算符)
    - "\\d+\\s*[+\\-*/]\\s*\\d+\\s*="
    - "0x[0-9a-fA-F]+\\s*[+\\-*/]\\s*(?:0x[0-9a-fA-F]+|\\d+)"
    - "\\d+\\s*[+\\-*/]\\s*0x[0-9a-fA-F]+"
  result_claim_triggers: # 结果声明词 (隐含已做计算)
    - "计算得"
    - "共\\s*\\d+\\s*(字节|位|byte|bit|bytes|bits)"
    - "合计\\s*\\d+"
    - "等于\\s*(?:0x[0-9a-fA-F]+|\\d+)"
    - "约\\s*(?:为\\s*)?\\d+"
    # ... (见 CLAUDE.md 完整列表)
  python_bash_patterns: # 合法的 python 执行模式
    - "\\bpython\\b"
    - "\\bpython3\\b"

approach_switch_check: # L7 禁止未证伪就切换方向
  enabled: true
  trigger_phrases: # 切换意图短语
    - "改走"
    - "换(个|一种|一个)?(思路|方案|方向|路径|方法)"
    - "另辟"
    - "放弃(这条|当前|原来的?)?\\s*(路径|方案|方向|思路)?"
    - "这条(路径|路|线|方向).{0,15}(太|都是|全是|不行|走不通|没希望|绕远)"
    - "重新(规划|设计|开始|来过)"
    - "绕(过|开)这(个|条)"
    - "像\\s*F\\d{3,}\\s*一样" # 纯类比不等于证据
    - "从头(开始|来)"
  exemption_prefixes: # 句首豁免 (头脑风暴 / 讨论时允许)
    - "头脑风暴"
    - "讨论"
    - "考虑"
    - "比较"
    - "可选方案"
    - "备选"
    - "权衡"
  dead_ends_path: ".claude/state/dead_ends.md"
  required_section_headers: ["## 方案切换评估", "## Approach Switch"]
  min_section_body_length: 100

self_stop_check: # L8 禁止 AI 自发停止
  enabled: true
  trigger_phrases: # 自发停止短语 (Python regex)
    - "今天(先)?(到这|就(先)?这样|结束)"
    - "(暂时|先|稍微)?(休息|歇会|歇一会|喘口气|放一放|放松)"
    - "(改天|明天|晚点|稍后|下次|有空|过会|晚一些|过段时间)再(继续|做|看|分析|聊|说|来)"
    - "(我|先)?暂停(一下|分析|工作|任务)?"
    - "我累了"
    - "(暂时|先)?不想(继续|做|分析)"
    - "(先)?break(一下)?"
    - "(等下次|等下回|等后续|下次|改日)再"
    - "(收工|下班|打烊)"
  user_pause_phrases: # 用户的 message 含其一 → 视为用户主动暂停, 通过
    - "休息"
    - "暂停"
    - "停一下"
    - "歇会"
    - "先到这"
    - "今天就这样"
    - "改天再"
    - "明天再"
    - "晚点再"
    - "稍后"
    - "下次再"
    - "/pauseanalysis"
    - "/pauseAnalysis"
  exemption_phrases: # handoff 豁免 (回复含其一 → 视为合法等待, 不算自发停止)
    - "等你"
    - "等用户"
    - "等触发"
    - "等确认"
    - "等测试"
    - "等结果"
    - "等反馈"
    - "请告诉我"
    - "请触发"
    - "请去做"
    - "请操作"
    - "请运行"
    - "请提供"
    - "请确认"
    - "请回复"
```

### Slash Commands

| 命令              | 用途                              | 文件                                                      |
| ----------------- | --------------------------------- | --------------------------------------------------------- |
| `/newAnalysis`    | 重置状态, 开始新任务              | `commands/newAnalysis.md` + `hooks/new_analysis.py`       |
| `/pauseAnalysis`  | 暂停当前任务, 创建快照, 写 memory | `commands/pauseAnalysis.md` + `hooks/pause_analysis.py`   |
| `/resumeAnalysis` | 恢复暂停的任务, 强制重读所有状态  | `commands/resumeAnalysis.md` + `hooks/resume_analysis.py` |

**典型工作流**:

```
用户: /newAnalysis
       ↓ (脚本备份 + 重置 5 个状态文件)
用户: 编辑 task.md 写任务
用户: "任务写完了"
       ↓ (AI 读 task.md / tool_constraints.md, 写任务理解 checklist 到 plan.md, 停下汇报)
用户: 审过, 改 plan.md task_understanding_acked: true
       ↓ (AI advance 第一个 step, 开始 tool call 工作)

... 工作期间 hook 持续监督 (PreToolUse 7 项 + Stop 8 层) ...

用户: /pauseAnalysis  (例如下班 / 切到别的事)
       ↓ (脚本创建空快照 + 改 plan.md paused=true)
       ↓ (AI 读全部状态 + memory + git log, 填快照, 写 project memory, 汇报)
用户: 关闭会话

[ 几天后 ]

用户: 打开新会话
用户: /resumeAnalysis
       ↓ (脚本校验 paused 状态, 清 paused 标志, 输出快照预览)
       ↓ (AI 读快照 + 全部状态 + memory + git log, 输出恢复 brief, 停下汇报)
用户: 审过 brief, 说 "继续"
       ↓ (AI 接着上次的 step 继续工作)

... 任务完成 ...

AI: 改 plan.md status=completed
AI: 输出含 "## 自证" 段 (结论 / 依据 F<id> / 自我检查 4 项) 的最终结论
```

### tool_constraints.md frontmatter 关键字段

```yaml
tool_allowlists:
  mcp__CheatEngine__:
    - mcp__CheatEngine__ping
    - mcp__CheatEngine__read_memory
    - ... # 只允许只读分析工具
```

想约束新的 MCP 工具, 加一个 prefix 条目.

## 紧急逃生

如果 hook 误伤导致工作无法进行:

1. **改 CLAUDE.md frontmatter**: 编辑特定字段 (例如清空 `forbidden_hedge_examples`). hook 下次执行时读新值.
2. **禁用单个 hook**: 编辑 `.claude/settings.json`, 注释掉对应的 hook 入口
3. **完全禁用**: 重命名 `.claude/settings.json` → `.claude/settings.json.disabled`

## 维护

- 更新 CLAUDE.md 或 hook 脚本时, **也要更新此模板** (`E:\Bak\AnalysisHook\`), 下次复制才会带新改动
- 每个项目的 `tool_constraints.md` 可能有特殊约束, 不建议盲目复制; 如有需要, 新项目创建后再根据具体情况调整

## Python 依赖

所有 hook 脚本用 Python 3.9+. 依赖:

- Python 标准库 (无需额外安装)
- **pyyaml** (推荐, 用于解析 frontmatter. 没装也能运行但会 log 警告并跳过 frontmatter 解析)

安装 pyyaml:

```bash
pip install pyyaml
```

## 测试脚本

如果要验证 hook 是否正常工作, 可以手动跑:

```powershell
$env:CLAUDE_PROJECT_DIR = "E:\Path\To\Project"
echo '{"source":"startup","session_id":"test","hook_event_name":"SessionStart"}' | python "$env:CLAUDE_PROJECT_DIR\.claude\hooks\session_start.py"
```

查看 `.claude/logs/hook.log` 确认记录.

---

## License

AGPL-3.0 or any private use. 随意修改 / 分发, 但修改后的版本应保持"通用 AI 约束系统"的本意.
