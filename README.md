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
    │   ├── pre_tool_use.py                 # Tool call 前的 6 项检查 (含 config 文件保护)
    │   ├── stop.py                         # AI 结束回复前的 5 层检查 (L1-L5)
    │   └── new_analysis.py                 # /newAnalysis 命令的实现脚本 (不是 hook)
    ├── commands/
    │   └── newAnalysis.md                  # /newAnalysis slash command 定义
    ├── logs/                               # Hook 日志目录 (运行时自动创建 hook.log)
    └── state/
        ├── tool_constraints.md             # 工具白名单 / 禁止 (项目永久, 不随 /newAnalysis 重置)
        └── templates/                      # 状态文件模板 (/newAnalysis 时复制到 state/ 作为初始状态)
            ├── task.md                     # 任务定义模板
            ├── plan.md                     # 计划模板
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
- **结论声明但无自证段 + 未调用独立 reviewer (L5)** - 详见 "结论自证机制"
- CE 调试类工具 (只允许只读分析)
- 没 plan 就做关键 tool call
- 跟 dead_ends.md 冲突的尝试
- 5 个 tool call 没新 fact 不汇报 (软警告, 不 block)

### 7. 结论自证机制 (L5)

当 AI 在回复里说 "结论" / "已完成" / "已验证" / "最终" 等触发词时, Stop hook 会强制要求:

1. **回复必须含 `## 自证与审查` 段**, 至少包含:
   - 结论一句话
   - 依据的 facts (F<id> 列表)
   - 自我检查 4 项: 上下文偏见 / sunk cost / 逻辑漏洞 / 确认偏见
2. **本回合必须调用过 `superpowers:code-reviewer` Agent** 做独立审查 (该 subagent 没有当前会话上下文, 不会被偏见污染)
3. **循环**: reviewer 若说 not-passed, AI 必须修复后再调一次 (最多 3 轮)

这是防止 AI "得出看似合理但实际有偏见 / 逻辑漏洞 / 证据不足" 结论的最后防线.

详细格式见 CLAUDE.md 的 "结论自证与独立审查" 章节.

## 关键设计

### 4 个 Hook

| Hook                 | 触发                          | 作用                                                                                  |
| -------------------- | ----------------------------- | ------------------------------------------------------------------------------------- |
| **SessionStart**     | 会话开始 / 恢复 / 清空 / 压缩 | 注入全部状态文件 + memory + git log 到 system prompt                                  |
| **UserPromptSubmit** | 用户每次输入                  | 注入任务摘要 + task_understanding 状态 + current_step + dead_ends 标题                |
| **PreToolUse**       | 每次 tool call 前             | 6 项检查: config 保护 / task understanding / 工具白名单 / plan / dead_ends / environment |
| **Stop**             | AI 结束回复前                 | 5 层检查: 模糊词 / cite 形式 / cite 真实 / cite 相关性 / 结论自证+审查                |

### 5 层 Stop 检查

| 层     | 检查                                                                    | 抓什么                                                                                                                                                                                                                       |
| ------ | ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **L1** | 模糊词黑名单 (`forbidden_hedge_examples`)                               | `可能` / `似乎` / `觉得` / `因此` / `推断` / `综上` 等                                                                                                                                                                       |
| **L2** | 事实主张必须 cite (`fact_claim_patterns` + `required_citation_pattern`) | 含 `0x...` / PID / 函数名 / 模糊指代 (`它` / `这个`) 的句子必须带 `F<id>` / `memory/file.md` / `user-told`, 除非句首是行动词 (`下一步` / `我打算` ...)                                                                       |
| **L3** | cite 真实性                                                             | 引用 `facts.md` 不存在的 F<id>                                                                                                                                                                                               |
| **L4** | cite 相关性 (subject 匹配)                                              | 张冠李戴, 引用 F012 但句子里的技术细节不在 F012.subject 列表                                                                                                                                                                 |
| **L5** | 结论自证 + 独立审查 (`conclusion_self_review_check`)                    | 回复含 "结论" / "已完成" / "已验证" / "搞清楚" 等触发词 → 必须同时满足: (a) `## 自证` header 存在 (b) 段内正文含 `结论` / `依据` / `自我检查` / `独立审查` 4 个子项 + 正文 ≥ 100 字符 (c) 本回合调用过 `superpowers:code-reviewer` (精确匹配白名单). 任一缺失 → block |

**两条 Stop hook 额外行为**:

- **合并本回合 text**: Stop hook 读本回合**所有** assistant text (从最后一条 user message 往后) 做 L1-L5 检查, 防 AI 把违规内容藏在倒数第二条消息绕过
- **Retry 降级**: 连续 block 3 次 → 强制放行避免死循环 (stderr 会警告, 下轮 AI 必须主动补修)
- **Tool call 阈值**: 本回合 ≥ 5 个 tool call 但 `facts.md` 没新增 → log 警告 (目前软警告, 不 block, 因为无 baseline 机制)

### 6 项 PreToolUse 检查

所有检查按优先级顺序执行, 任一 block → exit 2 拒绝 tool call.

| 优先级 | 检查                             | 触发条件                                                                                      |
| ------ | -------------------------------- | --------------------------------------------------------------------------------------------- |
| 0      | **config 文件保护**              | Edit/Write/MultiEdit/Bash 碰到 `CLAUDE.md` / `task.md` / `tool_constraints.md` / hook 脚本 / `settings.json` → block; Edit `plan.md` 的 `task_understanding_acked` 字段 → block (含分段 Edit / Bash cd 绕过 / 大小写绕过检测) |
| 1      | **task_understanding_acked**     | 关键工具 + `plan.md` 字段为 false → block                                                     |
| 2      | **工具白名单**                   | `mcp__*` 前缀 + `tool_constraints.md` 白名单不含 → block                                      |
| 3      | **require_plan**                 | 关键工具 + `current_step` 为 null → block                                                     |
| 4      | **check_dead_ends**              | 关键工具 + 跟 `dead_ends.md` 某条冲突 (tool_name + 关键 input 值都出现在 dead end 段里) → block |
| 5      | **environment 时效**             | 关键工具 + `last_verified_at` 超过 5 分钟 → block (首次为空放行避免死锁)                      |

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

conclusion_self_review_check: # L5 结论自证 + 独立审查
  enabled: true
  trigger_phrases: # 正则列表, 覆盖同义词变体 (防 AI 换词绕过)
    - "(我的|本次|最终|整体|以上)?结论"
    - "(已经|已)(验证|确认|确定|证实)"
    - "(彻底|终于|已经)?搞清楚"
    - "(据此|由此|综上)(可知|可得|得出|推出|判断)"
    # ... (见 CLAUDE.md 完整列表)
  required_self_review_sections: ["## 自证", "## 自证与审查", "## Self-Review"]
  allowed_reviewer_subagents: # 精确白名单 (不是子串匹配)
    - "superpowers:code-reviewer"
    - "code-reviewer"
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
