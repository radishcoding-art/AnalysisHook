---
# ============================================================
# AI 工作约束 - frontmatter 段
# 这一段是给 hook 脚本解析的, 不是给 AI 看的
# 修改这一段无需改 hook 脚本, hook 自动读取
# ============================================================

# 任务定义文件. 真正的任务详细描述在 task.md 里, AI 必须先读它.
# 这个 frontmatter 字段只是给 hook 一个指针, 不直接含任务内容.
task_file: ".claude/state/task.md"

# 任务的简短摘要 (一行). 真正完整的任务在 task_file 指定的文件里.
# 用户可以在这里写一句话提示, 但 AI **必须**读 task.md 拿完整内容.
task_summary: "见 .claude/state/task.md (用户填写)"

# Stop hook 第 1 层: 模糊语言示例 (非穷尽快速黑名单)
# 重要: 这只是常见模糊词的示例, 不是穷尽列表.
# 真正的规则是 "任何形式的模糊 / hedging 语言都禁止", 包括没列在这里的词.
# AI 不允许通过用列表外的词 (例如 "觉得是", "倾向于", "印象中") 绕过检查.
# 第 2 层 (fact_claim_patterns + required_citation_pattern) 是真正的兜底.
forbidden_hedge_examples:
  # 经典 hedging
  - 可能
  - 似乎
  - 应该
  - 大概
  - 估计
  - 看起来
  - 推测
  - 我猜
  - 我觉得
  - 差不多
  - 应该是
  - 也许
  - 或许
  - 八成
  - 觉得是
  - 倾向于
  - 不出意外
  - 印象中
  - 我相信
  - 我记得
  - 多半
  - 大概率
  - 应当
  - 大致
  - 可能是
  - 也许是

  # 推断词 (逻辑跨越事实, 用来 "过度引申" 已 cite 的 fact)
  - 因此
  - 推断
  - 据此
  - 由此可知
  - 由此推出
  - 综上
  - 综上所述
  - 所以
  - 故而
  - 可推出
  - 可知
  - 显然

# Stop hook 第 1 层: 模糊词的合法上下文豁免
# 这些短语字面包含模糊词, 但语义上是合法的 (例如给用户建议)
# 第 1 层在扫禁用词时会先排除这些短语
hedge_whitelist_phrases:
  - "用户应该"
  - "您应该"
  - "建议您"
  - "应当先"

# Stop hook 第 2 层: 事实主张检测正则
# 任何句子匹配这些正则之一 → 视作 "事实主张" → 必须同时含 required_citation_pattern
# 这是兜底层, 抓 "AI 用新词绕过第 1 层但仍然做出裸技术主张" 的情况
fact_claim_patterns:
  - "0x[0-9a-fA-F]{4,}" # 内存地址
  - "\\bPID\\s+\\d+" # 进程 ID
  - "(?:port|端口)\\s*\\d{2,5}" # 端口号
  - "socket\\s*0x[0-9a-fA-F]+" # socket 句柄
  - "\\b0x[0-9a-fA-F]{8,}" # 长地址 / 函数偏移
  - "\\b[A-Z][a-zA-Z0-9]*_[a-zA-Z0-9_]+" # snake_case 含大写 (例如 SSL_write)
  # 模糊指代 (禁止用代词逃避具体引用). 只 lookbehind 排除前面是中文字 (避免 "其它" 等词误匹).
  # 不 lookahead, 因为 "它是 X" 这种正常用法后面就是中文, 应该被抓.
  # Python re 的 \b 不识别中文边界, 所以用 [\u4e00-\u9fff] 字符集做边界检查.
  # 用 (?:...) 非捕获组, 避免 findall 返回空串 bug.
  - "(?<![\\u4e00-\\u9fff\\w])(?:它|这个|该[\\u4e00-\\u9fff]{1,5}|上面那个|刚才那个|前面那个|此处的)"

# Stop hook 第 2 层: cite 正则. 事实主张必须同时匹配其中一个
# F<digits> 引用 facts.md 条目, memory/<file>.md 引用 memory 文件, user-told 标记用户告知
required_citation_pattern: "F\\d{3,}|memory/[a-zA-Z0-9_]+\\.md|user-told"

# Stop hook 第 2 层: 行动短语豁免
# 句子开头或主谓含这些短语 → 视作 "行动 / 计划 / 询问", 不是事实主张, 跳过 cite 检查
# 这避免 hook 误伤工作流交流 (例如 "下一步在 0x12345 设 bp" 是计划, 不是断言)
action_phrase_exemptions:
  - "下一步"
  - "我打算"
  - "我准备"
  # 不用孤立的 "建议", 太宽, 会让 "建议采纳我的结论: X" 被豁免 (P0-2 修复)
  # 只用具体搭配:
  - "建议先"
  - "建议您"
  - "建议用户"
  - "我去"
  - "我设"
  - "我读"
  - "我先"
  - "AOB scan"
  - "AOB 扫"
  - "扫描"
  - "等待"
  - "请您"
  - "需要您"
  - "您是否"
  - "是否要"

# Stop hook 第 3 层: cite 真实性检查
# 任何引用的 F<id> 必须真实存在于 facts.md 中
# 解析 facts.md, 拿出所有合法 id, 任何 cite 必须在这个集合里
# 防御 "假 cite" (写 F999 但 facts.md 没有)
# **注意**: 当前 stop.py 硬编码启用 L3 检查, 不读 enabled 字段.
# 想临时禁用 L3 → 改 stop.py 的 check_l3_cite_realness 函数, 不能改这里.
cite_realness_check:
  enabled: true
  facts_file: ".claude/state/facts.md"

# Stop hook 第 4 层: cite 相关性检查 (subject 匹配)
# facts.md 每条 fact 必须有 subject 字段 (列表)
# AI 引用 F<id> 的句子里, 出现的所有 fact_claim_patterns 匹配项必须全部在该 F<id> 的 subject 列表中
# 防御 "错 cite" (引用了 F012 但句子说的是无关的地址)
# **注意**: 当前 stop.py 硬编码启用 L4 检查, 不读 enabled 字段.
cite_subject_match_check:
  enabled: true
  required_field: "subject" # facts.md entry 必填字段

# Stop hook: tool call 阈值
# 本回合 tool call 数 ≥ 此值, 但 facts.md 没有新增条目 → 拒绝结束回复
tool_call_threshold: 5

# Stop hook: 结论自证检查 (L5)
# 触发: 回复中含 "结论声明触发正则" 之一 (strip 代码块 + 全角标点规范化后)
# 要求: 必须同时满足两项
#   (a) 回复中含 "## 自证" / "## 自证与审查" / "## Self-Review" section
#       (line-anchored header, P0-1/P1-4 修复防伪装)
#   (b) 本回合 transcript 含 Agent 工具调用 + subagent_type 在 allowed_reviewer_subagents 白名单
#       (精确匹配, P0-4 修复防假 reviewer)
# 任一不满足 → block, AI 必须补齐. 连续 block 3 次后降级 warning (P0-3 修复防死循环)
conclusion_self_review_check:
  enabled: true
  # 触发正则 (P0-2 修复): 每条是 Python regex, re.compile + re.search(UNICODE) 匹配
  # 覆盖同义词变体, 防止 AI 换词绕过 ("我判断" / "经验证" / "得出" / "初步认为" 等)
  trigger_phrases:
    # 结论 / 结果类 (触发后跟任意标点 / 空白 / 动词都算)
    - "(我的|本次|最终|整体|以上)?结论"
    - "最终(结果|判断|答案)"
    - "结果(是|为)[\\s:,.,。]"
    - "综合结论"
    # 完成 / 完毕类
    - "(已经|已|就|终于|成功)(完成|搞定|做完|走完)"
    - "(step|任务|task|分析).{0,5}(已|就)?完成"
    # 验证 / 确认类
    - "(已经|已)(验证|确认|确定|证实|核实|查实)"
    - "经(过)?验证"
    - "(可以|能够|足以)(确定|确认|证明|得出|推出|判断)"
    # 搞清楚类
    - "(彻底|终于|已经|已)?(搞清楚|弄清楚|搞明白)"
    - "(已经|已)(定位|锁定|找到)"
    # 判断 / 推论类
    - "我(的)?(判断|观点|看法|结论|推断|意见)(是|为)"
    - "我(觉得|判断|认为|推断|推论|断定|肯定|相信)"
    - "初步认为"
    - "(据此|由此|综上)(可知|可得|得出|推出|判断)"
  # 必须包含的自证 section 标题之一 (line-anchored header, P0-1 修复)
  required_self_review_sections:
    - "## 自证"
    - "## 自证与审查"
    - "## Self-Review"
  # 合法 reviewer subagent 白名单 (精确匹配, P0-4 修复)
  allowed_reviewer_subagents:
    - "superpowers:code-reviewer"
    - "code-reviewer"

# SessionStart hook: 强制注入这些文件到 system prompt
# task.md 和 tool_constraints.md 必须读 (它们是任务源头 + 工具规则源头)
must_read_on_session_start:
  - .claude/state/task.md # 任务详细描述 (用户填), AI 必须先理解
  - .claude/state/tool_constraints.md # 工具白名单 / 禁止 (项目永久)
  - .claude/state/plan.md
  - .claude/state/facts.md
  - .claude/state/dead_ends.md
  - .claude/state/environment.md

# Memory 目录 (跨会话长期记忆, 必须仔细读)
# 值可以是:
# - "auto": 根据 CLAUDE_PROJECT_DIR 环境变量自动计算 slug (推荐, 项目间可移植)
# - 固定路径: 例如 "%USERPROFILE%/.claude/projects/my-slug/memory"
memory_dir: auto

# UserPromptSubmit hook: 每次用户输入注入这些字段
must_inject_on_user_prompt:
  - source: frontmatter.task_summary
  - source: ".claude/state/plan.md"
    extract: current_step
  - source: ".claude/state/plan.md"
    extract: task_understanding_acked
  - source: ".claude/state/dead_ends.md"
    extract: headers

# 状态文件路径 (hook 脚本使用)
state_files:
  task: .claude/state/task.md
  tool_constraints: .claude/state/tool_constraints.md
  plan: .claude/state/plan.md
  facts: .claude/state/facts.md
  dead_ends: .claude/state/dead_ends.md
  environment: .claude/state/environment.md

# PreToolUse 拦截规则 (文档, hook 不直接读 frontmatter, 但记录于此)
# **重要**: 当前 pre_tool_use.py 硬编码以下规则, 不读 frontmatter.
# 想新增 / 修改拦截规则 → **必须改 pre_tool_use.py**, 改这里没用.
# 改 settings.json 的 PreToolUse matcher 只能改 hook 触发范围, 不能改检查逻辑.
pretool_intercepts:
  # 任务理解锁: task_understanding_acked=false → 拒绝任何关键 tool call
  - check: require_task_understanding
    requires: "plan.md frontmatter task_understanding_acked=true"
    reason: "未完成任务理解前不允许做关键 tool call. 先 read task.md, 写任务理解 checklist 到 plan.md, 等用户审."

  # CE: deny-by-default + 白名单允许 (规则在 tool_constraints.md, 不在这里)
  - tool_pattern: "mcp__CheatEngine__.*"
    action: deny_unless_allowlisted
    allowlist_source: ".claude/state/tool_constraints.md frontmatter tool_allowlists.mcp__CheatEngine__"
    reason: "CE 仅允许只读分析. 调试 / 修改 / 执行类工具一律禁止. 见 .claude/state/tool_constraints.md"

  # 关键工具调用前必须有 plan
  - tool_pattern: "mcp__IDAProMCP__.*|mcp__CheatEngine__.*|mcp__x64dbg__.*"
    action: require_plan
    reason: "调用前必须有 plan.md, current_step 不能为空"

  # 任何关键工具前必须比对 dead_ends
  - tool_pattern: "mcp__IDAProMCP__.*|mcp__CheatEngine__.*|mcp__x64dbg__.*"
    action: check_dead_ends
    reason: "比对 dead_ends.md, 不允许重复已证伪的尝试"

# 紧急逃生 (用户手动操作)
escape_hatch:
  disable_all: "rename .claude/settings.local.json → .disabled"
  disable_one: "comment out hook in .claude/settings.local.json"
---

# 通用 AI 工作约束系统

## 任务定义 (绝对)

任务的**完整描述**在 `.claude/state/task.md`. 这是 **AI 必须先读** 的文件.

任何回合开始时, 如果 `plan.md` 的 `task_understanding_acked` 是 `false`, AI 必须:

1. `Read .claude/state/task.md` (完整读完)
2. `Read .claude/state/tool_constraints.md` (完整读完)
3. 在 `plan.md` 的 "## 任务理解 checklist" 段输出: 任务目标 / 范围 / 输入输出 / 关键假设 / 不明确处 / 第一个 step 草稿
4. 停下汇报, 等用户审
5. 用户改 `task_understanding_acked: true` 后才能 advance step + 做关键 tool call

**`task_understanding_acked: false` 时, PreToolUse hook 拒绝任何关键 tool call.**

新任务用 `/newAnalysis` 命令初始化 (备份当前状态 + 重置 5 个状态文件 + 引导用户填 task.md).

## 状态文件 (绝对真理源)

所有 "我知道什么" 的依据必须来自这些文件. 不在文件里的, 不算 fact, 不允许说.

| 文件                                    | 用途                                                                                                                                 | 谁写                                 |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------ |
| `.claude/state/task.md`                 | 任务详细描述 (10 个章节). 用户填. AI 必须先读. **AI 不允许动这里**.                                                                  | 用户写, AI 只读                      |
| `.claude/state/tool_constraints.md`     | 工具白名单 / 禁止 (项目永久, 不随 /newAnalysis 重置). 含 `tool_allowlists` frontmatter, hook 用它做 deny-by-default 检查.            | 用户写, AI 只读, hook 用 frontmatter |
| `.claude/state/plan.md`                 | 当前任务的步骤计划. 含 `task_understanding_acked`, `current_step`, `steps[]`. **没有 plan / understanding 未 ack 不允许 tool call**. | AI 写, 用户审                        |
| `.claude/state/facts.md`                | 已验证事实. 每条 fact 必须有完整 `verified_by_output` (tool 输出 quote, 不许截断/总结).                                              | AI 写, hook 校验                     |
| `.claude/state/dead_ends.md`            | 已证伪 / 已经试过不行的方向. **每次想做某件事前必须先比对这个文件, 不能重复.**                                                       | AI 写                                |
| `.claude/state/environment.md`          | 当前环境状态: 任务相关进程 PID + 工具 attached. **每次关键 tool call 前先验证.**                                                     | AI 写, 工具死/活时同步               |
| `~/.claude/projects/<this>/memory/*.md` | 长期记忆, 跨会话沉淀. 内容**可能未经验证**, 但每次会话开始**必须**读.                                                                | auto memory + AI 手动                |

## facts.md 的 entry schema

```yaml
- id: F001 # 自动递增, 永不复用
  subject: # 必填, 列表. 列出该 fact 涉及的所有实体 (地址 / 函数名 / PID / 端口 / socket / 概念名)
    - "<实体1 例如 0x...>"
    - "<实体2 例如 函数名>"
    - "<实体3 例如 概念名>"
  fact: <一句话客观陈述, 可证伪>
  verified_at: "<ISO 8601 + 时区>"
  verified_by_tool: <完整 tool 名>
  verified_by_input: <完整调用参数>
  verified_by_output: |
    <完整 quote tool 输出, 禁止截断 / 总结 / 省略>
  expires: never | until_session_end | "<ISO 时间戳>"
```

**强制规则**:

- `subject` 必填, 必须列出该 fact 涉及的所有实体. **Hook 第 4 层用 subject 做 cite 相关性检查**, 引用此 fact 的句子里出现的所有技术细节必须在 subject 列表里. 不在 → 拒绝. 这防止 "形式上 cite 了 F012, 但句子说的是无关地址" 的伪 cite.
- `verified_by_output` 必须是完整 quote, 80 行就 80 行. 禁止截断 / 总结 / 省略.
- `expires: until_session_end` 用于进程相关 (PID, socket, 堆地址), SessionStart hook 会清扫这些, 防止 AI 用上次会话的过期数据.
- `id` 永不复用. F001 一旦存在, 永远是 F001 (即使被清扫到 expired_facts.md, id 也不再被新 fact 使用).

## 工作流硬性规则

### 任何 tool call 之前必须做的

1. 检查 `plan.md` 的 `task_understanding_acked`. **false → 必须先 read task.md + tool_constraints.md, 写任务理解 checklist 到 plan.md, 等用户审过改成 true 才能继续**.
2. 读 `plan.md`, 检查 `current_step` 是否覆盖即将做的事. 不覆盖 → 不能做.
3. 读 `dead_ends.md`, 检查即将做的事是否已经被证伪. 是 → 不能做, 也不允许重新设计绕过.
4. 读 `environment.md`, 确认目标进程 / 工具仍然存活. 死了 → 必须先恢复环境, 不能在死进程上 tool call.
5. 比对 `tool_constraints.md` 的 `tool_allowlists`, 确保即将调用的工具在白名单内.

### 任何回复结束之前必须做的

1. 检查回复中所有事实型主张, 必须能 cite 一个 facts.md 的 F<id> 或 memory 文件. 不能裸说.
2. 检查本回合 tool call 数. 如果 ≥ 5 但 facts.md 没新增条目, **必须停下汇报**: "没产生新事实, 应该重新评估方向", 不能继续无脑 tool call.
3. 检查 `plan.md` 的 current_step 是否需要更新. 完成的 step 必须 advance.

### 失败处理 (硬性)

- 任何方向证伪 (例如断点命中数 = 0, AOB 找不到, 假设被 tool output 反驳) → **立刻** append 到 `dead_ends.md`, 不再回去试.
- tool 返回错误或暧昧结果 → 不允许编理由继续, 必须停下确认或换方向.
- 工具进程死掉 → 立刻更新 `environment.md`, 停下等用户恢复, 不允许继续探索性 tool call.
- 用户告知的事实 → 写到 facts.md, source: user. **绝对禁止反向质疑用户告知的事实.**

## 工具分工

**项目通用规则**: `tool_constraints.md` 是工具白名单 / 禁止的源头. AI 必须读它.

**任务特定的工具用法** (例如本任务用哪个 debugger / analyzer / capturer): 在 `task.md` 第 7 章写.

## AI 常见失败模式 (禁止)

下列行为模式是 AI 在长期复杂逆向项目里反复犯的错. 列在这里作为禁令. 不引用具体地址 / 函数名 / 字节模式 — 因为状态文件清空后那些都不存在, 这里只描述行为本身.

### 类别 A: 跑偏 / 沉没成本

1. **沉没成本**: 一旦某个方向被 tool 输出证伪 (例如 bp 命中数 = 0, AOB 找不到, 假设被反驳), 立刻 append 到 `dead_ends.md`. 之后**不允许重试**, 即使想到了"新角度". 想重试必须先在 `plan.md` explicit 写明"为什么之前的证伪不算数", 让用户审.
2. **承诺无效**: 嘴上承诺"不变方案" 没用, 下一个 tool result 又改方向. 唯一约束是 `plan.md` 的 `current_step` + Stop hook 检查. 不依赖自我承诺.
3. **频繁换方案**: 一个方向还没彻底走完就放弃换. 必须把当前 step 走到 plan 里写明的验证条件, 才能 advance 到下一个 step.

### 类别 B: 凭假设行动 / 不基于事实

4. **凭训练模式贴标签**: 看到字节模式 / 函数特征 / 数据形状, 不允许根据训练里的"常见模式"直接下结论. 任何识别必须用 tool 输出验证, 写到 `facts.md` 才能用.
5. **编理由继续**: tool 返回错误或暧昧结果, 不允许编"可能是 X"硬继续. 必须停下确认或换方向 (并把这个方向 append 到 dead_ends.md).
6. **不读 memory**: 会话开始如果 SessionStart hook 没自动注入 memory, 必须主动 Read 所有 memory 文件后再开始工作. 不允许"凭本会话现学的假设"工作.
7. **不读 git history**: 上次会话的 commits 可能有进度. 会话开始必须看 `git log --oneline -20`.

### 类别 C: 幻觉 / 想当然

8. **数据源混淆 (缓存 vs 实时)**: 工具返回的"进程信息" / "状态查询" 可能是缓存的, 不等于实时存活. 任何进程相关 tool call 前必须用独立机制 ping (例如 `tasklist`, 或工具自己的 health check) 验证.
9. **tool 命令成功 ≠ 实际生效**: 工具返回 OK 不等于副作用真的发生. 设了断点 / 改了 condition / 写了内存之后, 必须立刻用独立查询验证是否生效.
10. **术语漂移不修正**: 一旦发现某个对象 / 函数的实际行为跟之前命名假设矛盾, 必须**立刻**更新术语. `facts.md` 是术语唯一源, 改了 fact 必须改所有用词.
11. **跨工具数据假定一致**: x64dbg 和 CE 看到的进程未必是同一个 (CE 可能缓存了上次的). 切换工具时必须重新验证.
12. **反向推翻用户告知的事实**: 用户口头告知的事 (例如某协议是什么) 写到 `facts.md` 标 `source: user`. **绝对禁止后续推翻或质疑**, 哪怕几小时后想到了"反例".

### 类别 D: 流程 / 沟通

13. **设断点不通知用户去触发**: 设完 bp 立刻 explicit 输出 "请去做 X 触发它", 不要让用户白等.
14. **状态机不清晰**: 用户去做某件事时, 必须明确停下并输出 "现在等 X 完成". 等待期间**不允许**继续探索性 tool call.
15. **一次 dump 大量信息**: 一次只问一个问题, 一次只做一件事. 不给用户 5 个选项让他选.
16. **滥用 ScheduleWakeup**: 这是 `/loop dynamic mode` 专用工具. 普通任务不允许用它当 polling.

## 事实主张的强制句式

**核心规则**: 任何包含技术细节 (内存地址 / PID / 端口号 / 函数名 / socket 句柄) 的陈述句, 必须 cite 一个 `facts.md` 的 `F<id>`, 或 cite memory 文件名, 或标记 `user-told`. 不能 cite 的, 不允许说.

### 唯一允许的句式

| 句式                     | 例子                                        |
| ------------------------ | ------------------------------------------- |
| 引用已验证 fact          | `F012 已验证: <事实陈述>`                   |
| 引用 memory (注明非验证) | `memory/<file>.md 记录: <内容>, 未验证`     |
| 引用用户告知             | `user-told: <用户说过的事>`                 |
| 未知, 计划验证           | `未知: <X>. 计划用 <方法> 验证`             |
| 不含技术细节的一般说明   | `下一步打算...`, `建议先...`, `您审一下...` |

### 禁止的句式

| 句式                                  | 为什么禁止                               | 反面示例                                                                             |
| ------------------------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------ |
| 任何 hedging 词                       | 把猜测包装成事实                         | `X 可能是 Y` / `觉得 X 是 Y` / `看起来像 Y` / `倾向于是 Y`                           |
| 推断词 (因此 / 据此 / 综上 / 所以)    | 逻辑跨越事实, "过度引申" 已 cite 的 fact | `F012 已验证基址. 因此 0x... 是 SSL_write`                                           |
| 裸主张 (含技术细节但不 cite)          | 没有事实依据                             | 任何含 `0x...` / 函数名 / PID 的句子, 没跟 `F<id>` 或 `memory/...`                   |
| 假 cite (引用不存在的 F<id>)          | 编造证据                                 | `F999 已验证: ...` (facts.md 没有 F999)                                              |
| 错 cite (F<id> 存在但 subject 不匹配) | 张冠李戴, 用无关 fact 给猜测背书         | `F012 已验证: 0x7FF7A35CE460 是目标函数` (F012 的 subject 实际不含 `0x7FF7A35CE460`) |
| 模糊指代 (它 / 这个 / 该函数)         | 用代词逃避具体引用                       | `F012 验证了基址. 该函数是加密入口.`                                                 |
| 反向推翻用户告知                      | 用户告知的事实是地基                     | `user-told 说 X 是 Y, 但我觉得不是`                                                  |

### Hook 四层检查机制

Stop hook 用四层抓违规, 层层加码降低 AI 绕过概率.

**第 1 层 — 模糊词黑名单快速命中** (`forbidden_hedge_examples`):
扫描回复中是否含常见模糊词 + 推断词. 命中 → exit 2 拒绝. 排除 `hedge_whitelist_phrases` 里的合法短语.

**重要**: 这个列表是**非穷尽示例**, 不是完整规则. AI **不允许**通过用未列出的新词 (例如 "觉得是" / "倾向于" / "印象中" / "多半") 绕过. 真正的规则是 "**任何**模糊语言禁止", 第 1 层只抓常见的, 第 2 层是兜底.

**第 2 层 — 事实主张 cite 形式检查** (`fact_claim_patterns` + `required_citation_pattern`):
按句号 / 问号 / 感叹号分句, 扫描每个句子.

判定是否为事实主张:

- 句子开头或主谓含 `action_phrase_exemptions` (例如 "下一步" / "我打算" / "建议") → 视作行动 / 计划, 跳过此层
- 否则, 句子匹配 `fact_claim_patterns` (含地址 / PID / 函数名 / 模糊指代) → 视作事实主张

事实主张的句子必须同时匹配 `required_citation_pattern` (含 `F<id>` / `memory/<file>.md` / `user-told`). 没 cite → exit 2.

**第 3 层 — cite 真实性检查** (`cite_realness_check`):
解析 `facts.md`, 拿出所有合法 `F<id>`. 回复中的任何 `F<id>` 引用必须真实存在于 facts.md. 引用不存在的 id (例如 `F999`) → exit 2. 防御 "假 cite".

**第 4 层 — cite 相关性检查 (subject 匹配)** (`cite_subject_match_check`):
对每个 cite `F<id>` 的句子:

1. 提取该句子里所有 `fact_claim_patterns` 匹配项 (地址 / 函数名 / PID 等)
2. 读 `facts.md` 中该 F<id> 的 `subject` 字段 (列表)
3. 句子里的所有匹配项必须**全部**在 subject 列表中. 任何一个不在 → exit 2

例: F012 的 subject = `["0x7FF7A3540000", "<模块名>", "基址"]`. 引用 F012 的句子如果出现 `0x7FF7A35CE460` (不在 subject 里) → 拒绝. 防御 "错 cite".

**四层组合的兜底效果**:

| 绕过方式                 | 抓住的层                            |
| ------------------------ | ----------------------------------- |
| 用新 hedging 词          | L2 (含技术细节但句子没 cite)        |
| 用 "因此 / 推断 / 据此"  | L1 (推断词在黑名单)                 |
| 用模糊指代 (它 / 该函数) | L2 (模糊指代在 fact_claim_patterns) |
| 编 F999 假 cite          | L3 (id 不存在)                      |
| 引用 F012 给无关地址背书 | L4 (subject 不匹配)                 |
| 不含技术细节的纯说明     | 通过 (一般工作流交流豁免)           |

AI 没有 "既做出技术主张又避免被抓" 的路径. 唯一的合法路径是: 写真实的 fact 到 facts.md (含完整 subject + verified_by_output), 然后引用它.

## 结论自证与独立审查 (L5 检查)

**核心原则**: AI 得出任何**结论** (不只是事实, 而是从多个 fact **推导**出的判断) 之前, 必须先自证, 然后调用独立 reviewer. 这是防止 "上下文偏见 / sunk cost / 逻辑漏洞" 的最后防线.

### 什么算 "结论"

含以下任一关键词的陈述 (见 frontmatter `conclusion_self_review_check.trigger_phrases`):

- "我的结论" / "最终结论" / "结论是" / "结论:"
- "最终结果"
- "已经完成" / "step 完成" / "task 完成"
- "已经验证" / "已经确认" / "可以确定" / "我确定"
- "已经搞清楚" / "彻底搞清楚"

这些词在回复中任一次出现 → Stop hook 的 **L5 检查** 被触发.

### L5 检查要求

触发后, 回复**必须同时**满足两项:

1. **自证段**: 回复中含 `## 自证` 或 `## 自证与审查` 或 `## Self-Review` (完整 markdown header)
2. **独立审查**: 本回合的 transcript 必须含 `Agent` 工具调用, 且 `subagent_type` 含 "code-reviewer"

任一缺失 → Stop hook exit 2 拒绝结束回复, AI 必须补齐.

### 自证段的固定格式

```markdown
## 自证与审查

### 结论

<一句话复述结论>

### 依据的 facts

- F<id>: <说明依据的哪条事实>
- F<id>: <...>

### 自我检查 (必须回答每个问题)

**1. 上下文偏见**: 我这个结论是不是因为本会话里反复看到某个模式或数据, 导致我默认它是正确的? 检查:
<AI 填: 具体说是什么模式, 为什么它不是偏见>

**2. Sunk cost**: 我是不是因为已经在这个方向花了很多时间 / tool call 就不肯承认它错了? 检查:
<AI 填: 列出这个方向的证据 + 证伪的证据, 客观权衡>

**3. 逻辑漏洞**: 从 facts 到结论的每一步推理是什么? 有没有跳跃?
<AI 填: 每一步列出来. 例如 "F001 说 X, F002 说 Y, 因为 X + Y 不矛盾, 所以 Z" - 但要小心 "不矛盾" 不等于 "一定". >

**4. 确认偏见**: 我有没有故意忽略跟结论矛盾的证据?
<AI 填: 列出任何可能反驳结论的证据, 并说明为什么不足以推翻>

### 独立审查

Reviewer: superpowers:code-reviewer
调用参数: <Agent tool 的 description + prompt 概要>

Round 1 结果: <通过 / 不通过>
Round 1 findings: <reviewer 的 findings 列表>

(若不通过, 修复后 Round 2)
Round 2 结果: ...

### 最终结果

- 通过: ✓
- 通过轮数: 1 / 3
```

### 循环修复

- reviewer 如果说 "不通过", AI 必须修复 (改 fact / 改结论 / 收集更多证据) 后再审一次
- 最多 3 轮. 超过 3 轮 → 停下汇报, 让用户决定方向
- 如果 reviewer 说"通过", 才能把相关的 plan.md step 标记 completed

### 独立审查的调用方式

AI 必须用 Agent 工具调用 `superpowers:code-reviewer`, 类似这样:

```
Agent(
  subagent_type: "superpowers:code-reviewer",
  description: "独立审查 step <id> 结论",
  prompt: |
    请独立审查我对 <X> 的结论.

    ## 我的结论
    <一句话>

    ## 依据
    - F<id>: <内容> (验证来源: <tool>)
    - F<id>: <内容> (验证来源: <tool>)
    - ...

    ## 你要找的问题
    1. **上下文偏见**: 我是不是因为反复见到某个模式就默认?
    2. **逻辑漏洞**: 从 fact 到结论推理是否有跳跃?
    3. **证据充分性**: 这些 fact 够支撑结论吗?
    4. **sunk cost**: 是不是因为花时间就不肯放弃?
    5. **确认偏见**: 有没有忽略反驳证据?

    **不要做新的 tool call**, 基于我给的 fact 做逻辑审查.

    返回: 问题清单 (如有) + 明确的 "passed" 或 "not-passed" 标签.
)
```

reviewer 的输出要**完整 quote** 到自证段的 "Round N findings" 里.

### 为什么用 superpowers:code-reviewer

这个 subagent 是独立的, **没有**当前会话的上下文 (不会被本会话的偏见污染). 它只看我提供的 fact + prompt, 做纯逻辑推理. 这是"无偏见审查" 的理想工具.

### 一般说明的豁免 (避免误伤)

不含技术细节的句子 (例如 "下一步打算 X" / "您审一下" / "建议先 Y" / "这个方案可以吗") 不需要 cite. 这类是工作流交流, 不是事实主张. 第 2 层正则不会匹配它们.

行动 / 计划句子 (例如 "我打算在 0x12345 设 bp") 即使含技术细节也不需要 cite, 因为开头匹配 `action_phrase_exemptions`. 这是行动声明, 不是事实断言. 但**断言句子** (例如 "0x12345 是 SSL_write") 即使语法相似, 没有 action 短语开头, 仍会被 L2 抓.

### 模糊词的合法上下文豁免

frontmatter `hedge_whitelist_phrases` 列出豁免短语 (例如 "用户应该确认" 是给用户建议, "应当先" 是工作流术语). 第 1 层会跳过这些. 但豁免短语必须严格字符串匹配, 不能用模糊词构造其他意思绕过.

## 紧急逃生

如果 hook 误伤导致工作无法进行, 用户可以手动:

1. **临时改 frontmatter**: 编辑这个文件的 frontmatter (例如清空 forbidden_words), hook 下次执行时读到新值
2. **禁用某个 hook**: 编辑 `.claude/settings.local.json`, 注释掉对应的 hook 入口
3. **完全禁用**: rename `.claude/settings.local.json` → `.claude/settings.local.json.disabled`

---

## AI 必读 (每次会话开始)

如果你 (Claude) 正在读这段, 这是你必须遵守的:

1. **第一件事**: 读 `.claude/state/task.md` 完整内容. 这是任务定义.
2. **第二件事**: 读 `.claude/state/tool_constraints.md`. 工具白名单 + 禁止.
3. **第三件事**: 读 `.claude/state/plan.md`. 检查 `task_understanding_acked` 字段:
   - **false**: 你必须先在 `plan.md` 的 "## 任务理解 checklist" 段输出你对 task.md 的理解, 停下汇报, 等用户审过改成 true. **此期间不允许任何关键 tool call**.
   - **true**: 用户已审过你的任务理解, 你可以继续 (写 step + 做 tool call).
4. **第四件事**: 读 `.claude/state/facts.md` / `dead_ends.md` / `environment.md`. 知道哪些是已知, 哪些是死路, 环境状态如何.
5. **第五件事**: 读 memory 目录所有 `.md` 文件. 内容可能未验证, 但作为方向参考.
6. **任何 claim 必须 cite F<id> / memory/<file>.md / user-told**. 没 cite 的 claim, Stop hook 会拒绝.
7. **5 个 tool call 没新 fact** → 立刻停下汇报.
8. **遇到死路** → append dead_ends.md, 不重试.
9. **用户告知的事** → facts.md 加 `source: user-told`, 不质疑.
10. **新任务**: 用户运行 `/newAnalysis` 会重置所有状态文件 (备份到 archive/). 之后你必须重新读 task.md + 输出任务理解 + 等用户审.

如果你违反任何一条, hook 会通过 exit 2 拒绝你的动作, 把违规原因塞回你的 context, 你必须改正.
