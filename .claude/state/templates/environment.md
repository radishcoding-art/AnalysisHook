---
# 当前环境 / 工具 / 进程的存活状态. 任何关键 tool call 前必须先验证.
# PreToolUse hook 检查 last_verified_at, 如果距离当前时间 > stale_threshold_seconds → 强制重新验证.
# 通用结构: processes (任务相关的进程) + tools (debugger / analyzer / capturer)
last_verified_at: ""
stale_threshold_seconds: 300 # 5 分钟. 超过这个时间环境必须重新验证

# 任务相关的进程列表 (用户在 task.md 里列出, AI 验证后写到这里)
# 每个 entry: { name, pid, alive, base_address, last_check_method, last_check_at }
processes: []

# 工具列表 (debugger / analyzer / capturer / 等)
# 每个 entry: { name, type, attached, attached_pid, last_check_method, last_check_at }
# type: debugger | static_analyzer | memory_reader | network_capturer | other
tools: []
---

# 环境状态

(空, 等 AI 第一次验证)

## 用途

每次进程相关 tool call 之前, AI 必须先 read 这个文件, 确认目标进程 / 工具仍然存活. 死了 → 必须先恢复, 不能在死进程上 tool call.

## entry schema

### processes

```yaml
processes:
  - name: "<进程名, 例如 target.exe>"
    pid: <int 或 null>
    alive: <bool>
    base_address: "<0x... 或 null>" # 可选, 如果是固定基址或已知
    last_check_method: "<tasklist | mcp__... | other>"
    last_check_at: "<ISO 8601>"
```

### tools

```yaml
tools:
  - name: "<工具名, 例如 x64dbg>"
    type: "<debugger | static_analyzer | memory_reader | network_capturer | other>"
    attached: <bool>
    attached_pid: <int 或 null> # 必须等于某个 process 的 pid
    last_check_method: "<mcp__... 或 manual>"
    last_check_at: "<ISO 8601>"
```

## 验证方法 (通用)

| 字段                        | 验证方法                                                            | 注意事项                                                        |
| --------------------------- | ------------------------------------------------------------------- | --------------------------------------------------------------- |
| `processes[].pid` + `alive` | OS 级查询 (Windows: `tasklist`, Linux: `ps`, Mac: `ps`)             | **最权威**. 任何工具的内部 ping 都可能是缓存的, 必须 OS 级验证. |
| `processes[].base_address`  | 用 memory reader (如 `mcp__CheatEngine__read_memory`) 读已知偏移    | 读失败 → 进程死了或地址变了                                     |
| `tools[].attached`          | 用工具自己的 query (例如 `mcp__x64dbg__DbgValFromString` 读 `$pid`) | 比对 `attached_pid` 跟 `processes[].pid` 必须一致               |

## 失活处理

工具 / 进程死掉时, AI 必须**立刻**:

1. 更新此文件: 把对应 `alive` / `attached` 设为 false, 清空 pid
2. 更新 `last_verified_at` 为现在时间
3. **停止**所有依赖该工具 / 进程的 tool call
4. 输出明确的 "等待用户恢复" 消息
5. **不允许**继续探索性 tool call (例如尝试 ping 其他工具看是否还活着 - 这种应该一次性验证完)

## 强制规则 (复述 CLAUDE.md)

- **任何关键 tool call 前必须 read 此文件**.
- **last_verified_at 距离当前时间 > stale_threshold_seconds (默认 300s)** → PreToolUse hook 拒绝调用, 强制 AI 先重新验证环境.
- **last_verified_at 为空 (首次使用)** → PreToolUse hook **不阻断**, 但 log 警告. AI 应该在第一个 tool call 之前就用 OS 级 query 验证环境.
- **tool 的 attached_pid 必须跟 process 的 pid 一致**, 否则工具没附加到目标. 不允许在没附加的情况下 tool call.

## 重启 / 重连后的恢复流程

1. 用户告知 "进程已重启" / "x64dbg 已重新附加"
2. AI 第一件事: 用 OS 级 query 验证 PID
3. AI 第二件事: 用工具自己的 query 验证 attached
4. AI 第三件事: 把验证结果作为新 fact 写入 facts.md (subject 含 PID, 完整 verified_by_output)
5. 更新此文件全部字段
6. 检查 facts.md 中所有 `expires: until_session_end` 的 fact, 全部移到 `expired_facts.md`
7. 重新验证 plan.md 的 current_step 是否仍然有效 (依赖的 fact 是否还有效)

## 失败模式 (会话最容易犯的错)

- **工具 ping 缓存陷阱**: 工具自己的 ping / get_process_info 可能返回缓存值. 必须 OS 级验证.
- **跨工具假定一致**: 多个工具可能连接到不同进程. 必须比对 attached_pid.
- **重启后用旧 PID**: 进程重启后 PID 变了, AI 必须立刻重新验证, 不能用上次的 PID 推理.

---

## current state (空, 等首次验证)
