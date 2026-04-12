---
# 已证伪 / 已经试过不行的方向. AI 在做任何关键 tool call 前必须比对.
# PreToolUse hook 在每次关键 tool call (mcp__*) 调用前比对此文件.
count: 0
last_updated: ""
last_id: 0 # 自动递增 id, 永不复用
---

# 已证伪 / 试过不行的方向

(空, 还没有 dead end)

## 何时写 dead end

任何方向被 tool 输出证伪 → **立刻** append. 包括但不限于:

- 设的断点多次 hit count = 0, 路径明确不通
- AOB / 模式扫描指定 pattern 找不到, 排除了 "内存里存在该模式" 的假设
- 读 tool 输出反驳了之前的假设
- 工具持续返回错误, 指向方向不可行
- 用户明确告诉 AI "这条路不对"

## dead end entry schema

```yaml
- id: D001 # 自动递增 (基于 frontmatter 的 last_id), 永不复用
  description: "<一句话: 这条路是什么>" # 例如: "在 0x... 设 bp 找心跳加密入口"
  proposed_at: "<ISO>"
  proposed_in_step: "S<id>" # 来自 plan.md 的哪个 step
  disproven_at: "<ISO>"
  disproven_by_tool: "<tool 名>"
  disproven_by_input: "<完整 tool 调用参数>"
  disproven_by_output: |
    <证伪证据 - tool 输出的完整 quote 或关键片段>
  dont_retry_reason: "<为什么不允许重试. 必须明确, 不允许说 '重试有用'>"
  related_facts: [] # 可选: 跟这个 dead end 相关的 F<id> 列表
```

## 重要规则 (复述 CLAUDE.md)

- **PreToolUse hook 比对**: AI 即将做的事如果跟某个 D<id> 的 description 内容相符 → 拒绝.
- **想重试 dead end**: AI 必须先在 plan.md 写一个新的 step, 在 rationale 里 explicit 说明 "为什么之前的证伪不算数" (例如 cite 一个新的 F<id>). 用户审过新 step 才允许重试.
- **永远不删除 dead end**. 历史记录, 防止重复. 即使后来发现 dead end 是错的 (其实可行), 也只标记 `superseded_by: D<新 id>` 而不删.
- **dead end 的 description 必须具体**. 不允许笼统说法. 必须可对比的具体描述.

## 失败模式 (沉没成本陷阱)

AI 最容易犯的错: dead end 已经写了, 后来想 "换个角度再试同一件事". 这是 nonsense.

**判定标准**: 即将做的事跟某个 dead end 的 `disproven_by_input` 相同 (或仅参数微调) → **算重试**, 拒绝.

例外: 如果 AI 真的有新证据 (新 F<id>) 表明之前的 dead end 是错的, 必须写新 step 引用那个 F<id>, 在 rationale 里 explicit 说明.

---

## entries

(empty)
