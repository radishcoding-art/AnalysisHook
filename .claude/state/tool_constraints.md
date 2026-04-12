---
# 工具约束 (项目级永久, **不随 /newAnalysis 重置**).
# Hook 用 frontmatter, AI 看 markdown 正文.
# 修改这个文件: 用户手动改, 改完不需要重启 Claude Code (file watcher 自动加载).

# 工具白名单 (deny-by-default).
# 结构: { tool_name_prefix: [allowed_full_tool_names] }
# Hook 行为: tool_name 如果匹配某个 prefix (startswith), 则必须在 value 列表里, 否则 PreToolUse hook 拒绝.
# 添加新的工具家族: 加一个 prefix 条目.
tool_allowlists:
  # CE (Cheat Engine) - 仅允许只读分析
  mcp__CheatEngine__:
    # 进程 / 模块 / 线程 / 符号信息查询
    - mcp__CheatEngine__ping
    - mcp__CheatEngine__get_process_info
    - mcp__CheatEngine__enum_modules
    - mcp__CheatEngine__get_memory_regions
    - mcp__CheatEngine__enum_memory_regions_full
    - mcp__CheatEngine__get_thread_list
    - mcp__CheatEngine__get_address_info
    - mcp__CheatEngine__get_physical_address
    - mcp__CheatEngine__get_symbol_address
    - mcp__CheatEngine__get_rtti_classname
    # 内存读取
    - mcp__CheatEngine__read_memory
    - mcp__CheatEngine__read_integer
    - mcp__CheatEngine__read_string
    - mcp__CheatEngine__read_pointer
    - mcp__CheatEngine__read_pointer_chain
    - mcp__CheatEngine__checksum_memory
    # 反汇编 / 静态分析
    - mcp__CheatEngine__disassemble
    - mcp__CheatEngine__analyze_function
    - mcp__CheatEngine__find_function_boundaries
    - mcp__CheatEngine__get_instruction_info
    - mcp__CheatEngine__find_call_references
    - mcp__CheatEngine__find_references
    - mcp__CheatEngine__dissect_structure
    - mcp__CheatEngine__generate_signature
    # 内存搜索
    - mcp__CheatEngine__aob_scan
    - mcp__CheatEngine__scan_all
    - mcp__CheatEngine__search_string
    - mcp__CheatEngine__get_scan_results

# 显式禁止列表 (供文档说明, hook 实际由 deny_unless_allowlisted 强制).
# 这个列表不被 hook 直接使用, 但展示哪些工具是被 deny 的, 让人看清规则.
tool_denylists_explicit:
  mcp__CheatEngine__:
    # 调试相关 (CE 不能调试, 用 x64dbg)
    - mcp__CheatEngine__set_breakpoint
    - mcp__CheatEngine__set_data_breakpoint
    - mcp__CheatEngine__remove_breakpoint
    - mcp__CheatEngine__clear_all_breakpoints
    - mcp__CheatEngine__list_breakpoints
    - mcp__CheatEngine__get_breakpoint_hits
    # DBVM 调试 (Dark Byte VM, 内核级调试器)
    - mcp__CheatEngine__poll_dbvm_watch
    - mcp__CheatEngine__start_dbvm_watch
    - mcp__CheatEngine__stop_dbvm_watch
    # 修改 / 执行 (危险)
    - mcp__CheatEngine__auto_assemble # 修改代码
    - mcp__CheatEngine__evaluate_lua # 任意 Lua 代码执行
---

# 工具约束 (Tool Constraints)

**重要**: 这是项目级永久配置. **不随 /newAnalysis 重置**. 改这个文件 = 改项目的工具策略.

## CE (Cheat Engine)

### 角色

仅允许只读分析. **绝对禁止**用 CE 做调试 (设断点 / 改内存 / 执行代码).

### 允许做的

- 内存读取 (read_memory, read_integer, read_string, read_pointer, read_pointer_chain, checksum_memory)
- 反汇编 (disassemble, analyze_function, find_function_boundaries, get_instruction_info)
- 引用查找 (find_call_references, find_references)
- 内存搜索 (aob_scan, scan_all, search_string, get_scan_results)
- 进程 / 模块 / 线程 / 符号信息查询 (ping, get_process_info, enum_modules, ...)
- 结构分析 (dissect_structure, generate_signature)

完整白名单见 frontmatter `tool_allowlists.mcp__CheatEngine__`.

### 禁止做的

- **任何调试操作**: set_breakpoint, set_data_breakpoint, remove_breakpoint, clear_all_breakpoints, list_breakpoints, get_breakpoint_hits — 用 x64dbg
- **DBVM 内核调试**: poll_dbvm_watch, start_dbvm_watch, stop_dbvm_watch — 风险高, 不允许
- **修改代码 / 执行任意代码**: auto_assemble, evaluate_lua — 副作用大, 不允许

### 为什么

CE 是 "Cheat Engine", 设计上是游戏作弊工具, 调试功能不专业. 用 CE 设断点容易被反调试发现, 且断点管理混乱. 调试统一用 x64dbg.

## 如何添加新工具家族的约束

如果你要约束一个新的 MCP 工具 (例如 `mcp__MyTool__`), 在 frontmatter 加:

```yaml
tool_allowlists:
  mcp__MyTool__:
    - mcp__MyTool__safe_op_1
    - mcp__MyTool__safe_op_2
```

PreToolUse hook 会自动应用 deny-by-default + 白名单. 不需要改 hook 脚本.

## 强制规则

- **AI 必须先 read 此文件**, SessionStart hook 会强制注入到 system prompt.
- **AI 不允许 cite 或修改此文件中的工具列表**. 这是用户层级配置, 改了等于改项目策略.
- **PreToolUse hook 在每次 `mcp__*` 类工具调用前比对 tool_allowlists**. 不在白名单 → exit 2.

## 紧急临时禁用

如果某个工具在白名单里但你想临时禁用 (例如 ping 引发问题):

1. 编辑此文件, 把对应工具从 `tool_allowlists.<prefix>` 列表移除
2. PreToolUse hook 下次执行时读到新值, 自动拒绝该工具
3. 不需要重启 Claude Code
