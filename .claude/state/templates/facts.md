---
# 已验证事实数据库. AI 的所有事实主张必须 cite 此文件的 F<id> (Stop hook 第 3, 4 层强制).
count: 0
last_updated: ""
last_id: 0 # 自动递增, 永不复用. 即使 fact 被 expires 清扫, id 也不再被新 fact 使用
---

# 已验证事实

(空, 没有 fact 之前 AI 不允许做任何含技术细节的事实主张)

## fact entry 完整 schema

```yaml
- id: F001 # 自动递增 (基于 frontmatter 的 last_id), 永不复用
  subject: # 必填, 列表. Hook 第 4 层用此字段做 cite 相关性检查.
    # 列出该 fact 涉及的所有实体 (地址 / 函数名 / PID / 端口 / socket 句柄 / 概念名).
    # 引用此 fact 的句子里出现的所有技术细节都必须在此列表中, 否则 hook exit 2.
    - "0x..."
    - "..."
  fact: <一句话客观陈述, 可证伪>
  verified_at: "<ISO 8601 + 时区>"
  verified_by_tool: <完整 tool 名>
  verified_by_input: <完整调用参数>
  verified_by_output: |
    <完整 quote tool 输出, 禁止截断 / 总结 / 省略. 80 行就 80 行.>
  expires: never # never | until_session_end | "<ISO 时间戳>"
  source_session: null # 可选: 验证此 fact 的 session id
  notes: null # 可选: 自由注释, 不参与 hook 校验
```

## 写 fact 的强制流程

1. tool call 完成
2. 读 tool 输出
3. 提取一个**客观可证伪**的事实
4. 决定 `subject` 列表 - 必须包含 fact 涉及的**所有**技术细节
5. append 新 entry, id = `last_id + 1`
6. `verified_by_output` 必须是 tool 输出的**完整 quote**, 不允许截断 / 总结 / 省略
7. 选择 `expires`:
   - `never`: 永久事实 (代码常量, 协议格式, 固定配置)
   - `until_session_end`: 进程相关 (PID, socket 句柄, 堆地址, debugger attached 状态)
   - `"<ISO 时间戳>"`: 已知会过期的事实
8. 更新 frontmatter 的 `last_id` 和 `count`

## 强制规则 (复述 CLAUDE.md)

- **`subject` 必填且必须完整**. Hook 第 4 层会用它做 cite 相关性检查. AI 在某句中 cite F<id>, 句中所有 fact_claim_patterns 匹配项必须在该 F<id> 的 subject 中. 不在 → exit 2.
- **`verified_by_output` 必须完整 quote**. 不允许 "..." / "省略" / "类似" 等替代.
- **`expires=until_session_end`** 用于进程相关. SessionStart hook 自动清扫到 expired_facts.md.
- **`id` 永不复用**. 即使被清扫.
- **不允许编造 fact** (写一条 verified_by_output 是假的). 当前没有 hook 强制验证 quote 真实性, 但用户会审 facts.md, 发现假 fact 会清空整个 facts.md 并要求重新验证.

## 用户告知的事实 (source: user-told)

用户口头告知的事实也写到 facts.md, 但 schema 略不同:

```yaml
- id: F001
  subject: ["<端口号>", "TLS"]
  fact: "<端口号> 端口使用 TLS 1.2"
  verified_at: "<ISO>"
  verified_by_tool: user-told
  verified_by_input: null
  verified_by_output: |
    用户原话: "<原话>"
  expires: never
```

cite 时用 `user-told` 关键字, 例如 `user-told F001 已确认: <事实>`.

**绝对禁止**: 后续会话或后续推理中, 反向质疑 user-told 类型的 fact. 用户告知的事实是地基.

---

## entries

(empty)
