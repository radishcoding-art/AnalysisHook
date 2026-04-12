# -*- coding: utf-8 -*-
"""
PreToolUse hook.

触发时机: AI 准备调用任何 tool 之前 (tool 还未实际执行).

行为: 6 项检查, 任一不通过 → exit 2 拒绝 tool 调用, 把原因塞回 AI 上下文.

检查项:
1. **锁字段保护** (check_locked_field_edits): 禁止 AI 用 Edit/Write/MultiEdit 修改
   plan.md 的 task_understanding_acked 字段. 这是用户专属锁.
2. **任务理解锁** (check_task_understanding_acked): plan.md 的 task_understanding_acked
   必须为 true 才允许关键 tool call.
3. **工具白名单** (check_tool_allowlist): 从 .claude/state/tool_constraints.md 的 frontmatter
   tool_allowlists.<prefix> 字典读. deny-by-default: 不在白名单就拒绝.
4. **require_plan**: 关键工具 (mcp__x64dbg__*, mcp__CheatEngine__*, mcp__IDAProMCP__*)
   调用前 plan.md 必须有 active current_step.
5. **check_dead_ends**: 即将调用的 tool + tool_input 不能跟 dead_ends.md 里
   某条已证伪的方向相符 (只对地址 / 长 hex / 长数字类参数做严格匹配).
6. **environment 时效**: environment.md 的 last_verified_at 不能太老
   (默认 5 分钟阈值). 首次为空放行.

非关键工具 (Read / Bash / Edit 等) 不做 check 2/4/5/6, 但仍做 check 1/3.

stdin JSON 字段:
- tool_name: 即将调用的 tool 名 (例如 "mcp__CheatEngine__read_memory")
- tool_input: tool 参数 dict
- tool_use_id: 唯一 id
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib import (
    log,
    read_stdin_json_strict,
    load_claude_md_config,
    load_state_file,
    load_tool_constraints,
    plan_path,
    dead_ends_path,
    environment_path,
    emit_to_stderr,
    split_frontmatter,
    HAS_YAML,
)

HOOK_NAME = "pre_tool_use"

# 关键工具豁免 (以下 MCP 家族不触发 task_lock / plan / dead_ends / environment 检查).
# 默认策略: 所有 mcp__* 都是关键工具, 除非在此白名单. 新加 MCP 家族自动受保护.
CRITICAL_EXEMPTIONS = (
    "mcp__Pencil__",  # UI 设计工具, 非逆向
    "mcp__Context7__",  # 只读文档查询
    "mcp__ide__",  # IDE / notebook 辅助
)


def main():
    # 第四轮 P1-C 修复: 用 strict reader. parse 失败抛异常 → 外层 except Fail Closed.
    stdin = read_stdin_json_strict()
    if not isinstance(stdin, dict):
        # 非 dict (比如 AI 或 bug 给了 list / str) → Fail Closed
        raise ValueError("stdin JSON is not a dict: {0!r}".format(type(stdin).__name__))
    tool_name = stdin.get("tool_name", "")
    tool_input = stdin.get("tool_input", {}) or {}
    log("hook fired, tool={0}".format(tool_name), HOOK_NAME)

    config = load_claude_md_config()
    if not config:
        log("CLAUDE.md frontmatter empty, skipping all checks", HOOK_NAME)
        sys.exit(0)

    # ============ 检查 0: Config 文件保护 (最高优先级) ============
    # 第二轮 P0-5 + 新 P0-B 修复:
    # - 受保护文件 (CLAUDE.md / tool_constraints.md / hook 脚本) 完全禁止 AI 修改
    # - 半保护字段 (plan.md 的 task_understanding_acked) 用 "模拟应用 Edit + 比对字段值" 抓分段 Edit
    # - Bash 命令扫描, 检测 sed / python -c / tee / 重定向 > 等修改 config 的命令
    if not check_config_file_protection(tool_name, tool_input):
        return

    # ============ 检查 1: 任务理解锁 ============
    # task_understanding_acked=false → 任何关键 tool call 都拒绝
    if is_critical(tool_name):
        if not check_task_understanding_acked(tool_name):
            return

    # ============ 检查 2: 工具白名单 (从 tool_constraints.md 读) ============
    if tool_name.startswith("mcp__"):
        if not check_tool_allowlist(tool_name):
            return

    # ============ 检查 2: 关键工具必须有 plan ============
    if is_critical(tool_name):
        if not check_require_plan(tool_name):
            return

    # ============ 检查 3: dead_ends 比对 ============
    if is_critical(tool_name):
        if not check_dead_ends(tool_name, tool_input):
            return

    # ============ 检查 4: environment 时效 ============
    if is_critical(tool_name):
        if not check_environment_freshness(tool_name):
            return

    log("allowed: {0}".format(tool_name), HOOK_NAME)
    sys.exit(0)


def is_critical(tool_name):
    """
    是否是关键工具 (需要 task_lock + plan + dead_ends + environment 检查).

    默认: 所有 mcp__* 都是 critical, 防未来新加 MCP 家族漏保护.
    豁免: CRITICAL_EXEMPTIONS 里的家族 (UI 设计 / 文档查询 / IDE 辅助).
    """
    if not tool_name or not tool_name.startswith("mcp__"):
        return False
    for exempt in CRITICAL_EXEMPTIONS:
        if tool_name.startswith(exempt):
            return False
    return True


# ============================================================
# Config 文件保护 (第二轮 P0-5 + 新 P0-B 修复)
# ============================================================

# 受保护的配置文件 - AI 不允许修改 (用户专属)
# 第三轮 P0 修复:
# - 加 task.md (CLAUDE.md 明文保护但之前代码遗漏)
# - 加大小写不敏感匹配 (Windows)
# - 加目录前缀匹配 (PROTECTED_DIR_PREFIXES)
PROTECTED_FILES = [
    # 规则源文件
    "CLAUDE.md",
    # 任务定义 (CLAUDE.md 明文规定 AI 只读)
    ".claude/state/task.md",
    # 工具约束源 (项目永久策略)
    ".claude/state/tool_constraints.md",
    # Hook 脚本自身 (AI 不允许改 hook 来绕过自己)
    ".claude/hooks/_lib.py",
    ".claude/hooks/session_start.py",
    ".claude/hooks/user_prompt_submit.py",
    ".claude/hooks/pre_tool_use.py",
    ".claude/hooks/stop.py",
    ".claude/hooks/new_analysis.py",
    ".claude/settings.json",
    ".claude/settings.local.json",
]

# 受保护的目录前缀 - 目录下所有文件都禁止 AI 修改
# 第三轮 P0 修复: slash command / 模板目录完全保护
PROTECTED_DIR_PREFIXES = [
    ".claude/commands/",  # slash command 定义 (如果 AI 能改, 可以注入任意命令)
    ".claude/state/templates/",  # 重置模板 (如果 AI 能改, /newAnalysis 会注入恶意模板)
    ".claude/hooks/",  # 所有 hook 脚本 (包括未来新加的)
]

# 受保护的 basename 集合 - Bash 命令里只要含这些名字 + 危险模式就 block
# 第三轮 P0 修复: Bash 路径 basename 级检测, 抓 `cd dir && sed ... filename` 式绕过
PROTECTED_BASENAMES = {
    "CLAUDE.md",
    "task.md",
    "tool_constraints.md",
    "plan.md",
    "settings.json",
    "settings.local.json",
    "_lib.py",
    "session_start.py",
    "user_prompt_submit.py",
    "pre_tool_use.py",
    "stop.py",
    "new_analysis.py",
    "newAnalysis.md",
}

# 半保护: 可以修改, 但特定字段不能改
# { file_suffix: [禁止修改的字段名] }
SEMI_PROTECTED_FIELDS = {
    ".claude/state/plan.md": [
        "task_understanding_acked",
    ],
}

# Bash 危险命令模式 (改文件 / 执行任意代码 / 写文件)
# 第三轮 P0 修复: 补全多种 in-place 修改 + 文本编辑器 + VCS + 二进制写入模式
BASH_DANGER_PATTERNS = [
    # ===== in-place 修改类 =====
    # sed -i 原地修改
    r"\bsed\s+(-i|--in-place)\b",
    # awk -i inplace (gawk)
    r"\bawk\s+[^|&]*-i\s+inplace",
    # perl -i -pe
    r"\bperl\s+[^|&]*-i",
    # ===== 执行任意代码类 =====
    # python -c / -m
    r"\bpython[\w\.]*\s+-c\s+",
    r"\bpython[\w\.]*\s+-m\s+",
    # node -e / ruby -e / php -r / lua -e
    r"\b(node|ruby|php|lua)\s+[^|&]*-[ecr]\s+",
    # ===== 文本编辑器 (非交互写入) =====
    # vim/vi/nvim/ex/ed 非交互模式
    r"\b(vim|vi|nvim|ex|ed|nano)\s+[^|&]*-es?\b",
    r"\b(vim|vi|nvim)\s+[^|&]*-c\s+['\"]?(:?%?s|w|wq)",
    # ===== VCS 覆盖 =====
    # git checkout/restore/reset 可能覆盖文件
    r"\bgit\s+(checkout|restore|reset)\s+[^|&]*--",
    # ===== patch / diff 应用 =====
    r"\bpatch\s+",
    # dd 写入
    r"\bdd\s+[^|&]*\bof\s*=",
    # ===== tee / 重定向 / heredoc =====
    # tee 写文件 (tee / tee -a)
    r"\btee\s+",
    # > 或 >> 重定向写入 (扩大: 允许引号内路径)
    r"[>]{1,2}\s*[\"']?[./\\\w-]+\.(md|py|json|yaml|yml|txt|cfg|ini|toml)",
    # heredoc 写入 (<<EOF > file / <<< string > file)
    r"<<[-~]?[\w\"']+.*>",
    r"<<<\s*.*>",
    # printf / echo 重定向
    r"\b(printf|echo)\b[^|&]*[>]{1,2}",
    # ===== Windows PowerShell 写入 =====
    r"\bSet-Content\b",
    r"\bOut-File\b",
    r"\bAdd-Content\b",
    r"\bCopy-Item\b",
    r"\bMove-Item\b",
    # ===== mv / cp / rsync 覆盖 =====
    # 注意: \b + (mv|cp|rsync|install) + \s 确保是独立命令 (不会匹 cptool)
    r"(?:^|[\s;|&])(mv|cp|rsync|install)\s+",
    # ===== base64 解码写入 =====
    r"\bbase64\b[^|&]*\bd\b[^|&]*>",
    # ===== curl / wget 下载覆盖 =====
    r"\b(curl|wget)\s+[^|&]*(-o|-O|--output)",
]


def _normalize_path(path_str):
    """
    把路径规范化为小写 posix 风格 (forward slash, lowercased), 便于匹配.
    第三轮 P0 修复: 大小写不敏感 (Windows 上 .Claude / .CLAUDE 不再能绕过).
    """
    if not path_str:
        return ""
    s = str(path_str).replace("\\", "/").lower()
    # 去掉 drive letter 前缀 (Windows)
    if len(s) >= 2 and s[1] == ":":
        s = s[2:]
    # 去掉开头 "/"
    return s.lstrip("/")


def _is_protected_file(file_path):
    """
    检查是否是受保护文件.
    两种匹配:
    1. 完整路径匹配 (PROTECTED_FILES 的 endswith)
    2. 目录前缀匹配 (PROTECTED_DIR_PREFIXES 下的任意文件)
    所有匹配大小写不敏感.

    第四轮 P2-A 修复: 目录前缀匹配改成**段级**比对, 不用 `in` 子串.
    之前 `in` 会误伤 `docs/about-.claude/commands/foo.md` 这种路径.
    """
    norm = _normalize_path(file_path)
    if not norm:
        return False
    # 匹配 1: 完整文件路径. 要求严格匹配 path segment, 不能被 `my-CLAUDE.md` 误伤.
    for pf in PROTECTED_FILES:
        pf_lower = pf.lower()
        if norm == pf_lower or norm.endswith("/" + pf_lower):
            return True
    # 匹配 2: 目录前缀 - 段级比对
    segs = [s for s in norm.split("/") if s]
    for prefix in PROTECTED_DIR_PREFIXES:
        prefix_parts = [p for p in prefix.lower().rstrip("/").split("/") if p]
        if not prefix_parts:
            continue
        n = len(prefix_parts)
        # 在 segs 里找是否有**连续**子序列匹配 prefix_parts
        for i in range(len(segs) - n + 1):
            if segs[i : i + n] == prefix_parts:
                return True
    return False


def _get_semi_protected_fields(file_path):
    """返回 file_path 对应的半保护字段列表, 无匹配返回 []"""
    norm = _normalize_path(file_path)
    for suffix, fields in SEMI_PROTECTED_FIELDS.items():
        suffix_lower = suffix.lower()
        if norm == suffix_lower or norm.endswith("/" + suffix_lower):
            return fields
    return []


def _simulate_edit_and_get_result(file_path, tool_name, tool_input):
    """
    模拟 Edit / Write / MultiEdit 操作, 返回 post-edit 的文件内容.
    用于"分段 Edit 绕过"检测: AI 可能把禁用字符串拆成多次 Edit 写, 单次子串检查抓不到,
    但应用后的内容必然含完整字符串. 这里用应用后内容做检查.

    失败返回 None.
    """
    try:
        fp = Path(file_path)
    except Exception:
        return None

    if tool_name == "Write":
        return str(tool_input.get("content", "") or "")

    # Edit / MultiEdit 需要读当前文件内容
    try:
        current = fp.read_text(encoding="utf-8") if fp.exists() else ""
    except Exception:
        current = ""

    if tool_name == "Edit":
        old_s = str(tool_input.get("old_string", "") or "")
        new_s = str(tool_input.get("new_string", "") or "")
        replace_all = bool(tool_input.get("replace_all", False))
        if not old_s:
            return current
        if replace_all:
            return current.replace(old_s, new_s)
        return current.replace(old_s, new_s, 1)

    if tool_name == "MultiEdit":
        result = current
        edits = tool_input.get("edits", []) or []
        if isinstance(edits, list):
            for e in edits:
                if not isinstance(e, dict):
                    continue
                old_s = str(e.get("old_string", "") or "")
                new_s = str(e.get("new_string", "") or "")
                replace_all = bool(e.get("replace_all", False))
                if not old_s:
                    continue
                if replace_all:
                    result = result.replace(old_s, new_s)
                else:
                    result = result.replace(old_s, new_s, 1)
        return result

    return None


def check_config_file_protection(tool_name, tool_input):
    """
    Config 文件保护 (第二轮 P0-5 + 新 P0-B 修复).

    规则:
    1. **受保护文件** (CLAUDE.md / tool_constraints.md / hook 脚本 / settings.json) → AI 不允许用任何写入类工具修改
    2. **半保护字段** (plan.md 的 task_understanding_acked) → 对修改后的文件内容 (模拟应用 Edit)
       做子串检查, 防止分段 Edit 绕过
    3. **Bash 命令扫描** → 检测 sed / python -c / tee / 重定向 > 等可能修改 config 的命令
    """
    # ---- Bash 命令扫描 ----
    if tool_name == "Bash":
        cmd = str(tool_input.get("command", "") or "")
        if not cmd:
            return True
        cmd_lower = cmd.lower()

        # 第三轮 P0 修复: 两层检测
        # 1. 完整路径检测: cmd 里出现 PROTECTED_FILES / PROTECTED_DIR_PREFIXES / SEMI_PROTECTED 完整路径
        # 2. basename 级检测: cmd 里出现 PROTECTED_BASENAMES 的任意名字 (抓 `cd dir && sed ... plan.md` 式绕过)

        matched_path = None
        matched_reason = None

        # 1. 完整路径检测
        full_paths = (
            list(PROTECTED_FILES)
            + list(SEMI_PROTECTED_FIELDS.keys())
            + list(PROTECTED_DIR_PREFIXES)
        )
        for pf in full_paths:
            pf_lower = pf.lower()
            # 同时检查 forward 和 backslash 变体
            if pf_lower in cmd_lower or pf_lower.replace("/", "\\") in cmd_lower:
                matched_path = pf
                matched_reason = "完整路径 `{0}` 出现在命令里".format(pf)
                break

        # 2. basename 级检测 (抓 cd + 相对路径绕过)
        if matched_path is None:
            # 用单词边界匹配 basename (避免 "plan.md" 匹配到 "other_plan.md.bak")
            for basename in PROTECTED_BASENAMES:
                pattern = r"(?<![\w.\-])" + re.escape(basename.lower()) + r"(?![\w.\-])"
                try:
                    if re.search(pattern, cmd_lower):
                        matched_path = basename
                        matched_reason = "受保护文件名 `{0}` 出现在命令里 (可能是 cd + 相对路径绕过)".format(
                            basename
                        )
                        break
                except re.error:
                    continue

        if matched_path is None:
            return True

        # 检查是否有 "写入" 模式
        for pat in BASH_DANGER_PATTERNS:
            try:
                if re.search(pat, cmd, re.IGNORECASE):
                    reason_lines = [
                        "拒绝: Bash 命令试图修改受保护的 config 文件.",
                        "",
                        "**检测原因**: {0}".format(matched_reason),
                        "**危险模式**: `{0}`".format(pat),
                        "**完整命令**: `{0}`".format(cmd[:300]),
                        "",
                        "**规则**: AI 不允许通过 Bash (例如 sed / python / tee / 重定向 / vim -es / git checkout / patch 等) 修改以下文件:",
                        "  - `CLAUDE.md` (项目规则)",
                        "  - `.claude/state/task.md` (任务定义)",
                        "  - `.claude/state/tool_constraints.md` (工具约束)",
                        "  - `.claude/hooks/*.py` (hook 脚本自身)",
                        "  - `.claude/commands/*.md` (slash command)",
                        "  - `.claude/state/templates/*.md` (重置模板)",
                        "  - `.claude/settings.json` (hook 注册)",
                        "  - `.claude/state/plan.md` 的 `task_understanding_acked` 字段",
                        "",
                        "**绕过警告**: `cd dir && sed ... filename` / `cp /tmp/x file` 式绕过也被 basename 级检测捕获.",
                        "**大小写警告**: `.Claude/state/plan.md` 式大小写绕过也被捕获.",
                        "",
                        "**修复**: 如果你真需要改这些文件, 停下汇报, 让用户手动改.",
                    ]
                    block("\n".join(reason_lines))
                    return False
            except re.error:
                continue
        return True

    # ---- 文件修改类工具 ----
    if tool_name not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return True

    file_path = str(
        tool_input.get("file_path", "") or tool_input.get("notebook_path", "") or ""
    )
    if not file_path:
        return True

    # 规则 1: 完全保护的文件 → 完全禁止修改
    if _is_protected_file(file_path):
        reason_lines = [
            "拒绝: 受保护的 config 文件不允许 AI 修改.",
            "",
            "**文件**: `{0}`".format(file_path),
            "**工具**: `{0}`".format(tool_name),
            "",
            "**规则**: 以下文件是用户专属, AI 不允许修改:",
            "  - `CLAUDE.md` (项目规则)",
            "  - `.claude/state/tool_constraints.md` (工具约束)",
            "  - `.claude/hooks/*.py` (hook 脚本自身)",
            "  - `.claude/settings.json` (hook 注册)",
            "",
            "**修复**: 如果你需要改这些文件 (例如调整 forbidden_hedge_examples / 加新工具白名单),"
            " 请停下汇报, 告诉用户你想改什么 + 为什么, 让用户手动改.",
        ]
        block("\n".join(reason_lines))
        return False

    # 规则 2: 半保护字段 (plan.md 的 task_understanding_acked)
    semi_fields = _get_semi_protected_fields(file_path)
    if semi_fields:
        # 模拟应用 edit, 看**应用后的完整内容**是否改了字段值
        # 这能抓住分段 Edit 绕过 (AI 先写 "task_understanding" 再加 "_acked")
        post_content = _simulate_edit_and_get_result(file_path, tool_name, tool_input)
        if post_content is None:
            # 读文件失败 - 为安全, 检查 payload 子串 (保守策略)
            post_content = _collect_payload_text(tool_name, tool_input)

        # 读当前文件, 对比字段值变化
        try:
            fp = Path(file_path)
            current_content = fp.read_text(encoding="utf-8") if fp.exists() else ""
        except Exception:
            current_content = ""

        for field in semi_fields:
            current_value, cur_ok = _extract_yaml_field_value(current_content, field)
            post_value, post_ok = _extract_yaml_field_value(post_content, field)

            # 第四轮 P1-A 修复: post parse 失败 → 保守 block 可疑情况
            # AI 可能通过破坏 yaml 语法 (tab / 非法字符) 让 parse 失败,
            # 从而让 _is_field_flipped_to_true 返回 False 绕过.
            if not post_ok:
                # 检查 raw post_content 是否含 "<field>: <truthy>" 形态
                # 如果 raw 里字段被设成 true, 尽管 parse 失败, 也 block.
                truthy_pattern = re.compile(
                    r"(?im)^\s*" + re.escape(field) + r"\s*:\s*(true|yes|on|1|y)\b"
                )
                if truthy_pattern.search(str(post_content)):
                    reason_lines = [
                        "拒绝: post-edit frontmatter 解析失败, 且疑似把 `{0}` 设为 true.".format(
                            field
                        ),
                        "",
                        "**文件**: `{0}`".format(file_path),
                        "**检测**: yaml frontmatter 无法 parse, 但 raw 文本里含 `{0}: <truthy>` 模式.".format(
                            field
                        ),
                        "",
                        "**规则**: 如果 Edit 的结果让 frontmatter 不可 parse, AI 无法通过破坏 yaml 语法绕过字段翻转检查. 这次 Edit 被保守拒绝.",
                        "",
                        "**修复**: 检查你的 Edit 内容, 确保 yaml frontmatter 合法 (不引入 tab / 非法字符 / 不平衡引号).",
                    ]
                    block("\n".join(reason_lines))
                    return False
                # raw 里没有可疑模式, 但 parse 失败. 这是"AI 无意中破坏 yaml"的情况.
                # 让它通过, 后续 check_task_understanding_acked 会因为 parse 失败导致字段缺省 False → 自然 block 关键 tool call.
                log(
                    "post-edit yaml parse failed for {0}, but no truthy match, allowing".format(
                        file_path
                    ),
                    HOOK_NAME,
                )
                continue

            # 检查是否从 false → true / 从 None → true
            if _is_field_flipped_to_true(current_value, post_value):
                reason_lines = [
                    "拒绝: AI 不允许把 `plan.md` 的 `{0}` 字段改成 `true`.".format(
                        field
                    ),
                    "",
                    "**检测到**:",
                    "  - 当前值: `{0}`".format(current_value),
                    "  - 修改后值: `{0}`".format(post_value),
                    "",
                    "**这是用户专属的任务理解确认锁**. 只能由用户在审过你的任务理解 checklist 之后手动改.",
                    "",
                    "**修复**:",
                    "- 如果你想写任务理解 checklist, 在 `plan.md` 的正文段 `## 任务理解 checklist` 里写, 不要动 frontmatter",
                    "- 如果你已经写好了任务理解, **停下汇报**, 等用户审过后**用户**自己改 `{0}: true`".format(
                        field
                    ),
                    "- 不允许 AI 自己改这个字段, 即使你觉得任务理解已经足够",
                    "",
                    "**绕过警告**: 这个检查会**模拟应用你的 Edit**, 然后看应用后的字段值. 分段 Edit 绕不过.",
                ]
                block("\n".join(reason_lines))
                return False

    return True


def _collect_payload_text(tool_name, tool_input):
    """收集 tool_input 里所有文本字段, 用于保守子串检查."""
    parts = []
    if tool_name == "Write":
        parts.append(str(tool_input.get("content", "") or ""))
    elif tool_name == "Edit":
        parts.append(str(tool_input.get("old_string", "") or ""))
        parts.append(str(tool_input.get("new_string", "") or ""))
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits", []) or []
        if isinstance(edits, list):
            for e in edits:
                if isinstance(e, dict):
                    parts.append(str(e.get("old_string", "") or ""))
                    parts.append(str(e.get("new_string", "") or ""))
    return "\n".join(parts)


def _extract_yaml_field_value(content, field):
    """
    从 yaml frontmatter 里提取指定字段的值.
    第三轮 P1 + 第四轮 P1-A 修复: 用 PyYAML 解析, 同时返回 parse_ok 标志.

    返回 (value, parse_ok):
    - value: Python 对象 (str/bool/int/list/None) 或 None
    - parse_ok: True = frontmatter 正确 parse; False = parse 失败 (AI 可能破坏了 frontmatter)

    调用方在 parse_ok=False 时应该保守 block, 不能把 None 值当成 "字段不存在" 放行.
    """
    if not content:
        return None, True  # 空内容算合法 (字段缺省)
    if not field:
        return None, True
    # 如果 content 没有 frontmatter 开头, 算合法 (无字段)
    if not content.startswith("---"):
        return None, True
    try:
        fm, _ = split_frontmatter(content)
    except Exception:
        return None, False
    if not isinstance(fm, dict):
        return None, False
    # frontmatter 是空 dict (parse 成功但没字段) 算合法
    if not fm:
        # 检查原文是否真的是空 frontmatter (`---\n---`)
        # 区分 "parse 失败被 silent swallow" 和 "真的空"
        # split_frontmatter 失败时返回 ({}, body), 这种也返回 parse_ok=False 更安全
        # 但为避免误伤, 我们只在 content 里字段"看起来"存在但 parse 返回空时报 False
        pattern = re.compile(r"^\s*" + re.escape(field) + r"\s*:", re.MULTILINE)
        if pattern.search(content):
            return None, False  # raw 含字段但 parse 不到 → parse 有问题
        return None, True
    return fm.get(field), True


def _is_field_flipped_to_true(current_value, post_value):
    """
    检查字段值是否从 "非 true" 翻转到 "true 类" (true / yes / 1 / Python bool True).
    post 值是 truthy AND current 不是 truthy → 返回 True.

    支持 Python bool (PyYAML 自动解析 "true" 为 Python True).
    """

    def is_truthy(v):
        if v is None:
            return False
        # Python bool 直接判
        if isinstance(v, bool):
            return v
        # 数字
        if isinstance(v, (int, float)):
            return v != 0
        # 字符串
        s = str(v).strip().lower().strip('"').strip("'")
        return s in ("true", "yes", "1", "on", "y")

    return is_truthy(post_value) and not is_truthy(current_value)


def check_task_understanding_acked(tool_name):
    """task_understanding_acked=false → block. 这是最高优先级的检查."""
    plan_fm, _ = load_state_file(plan_path())
    acked = plan_fm.get("task_understanding_acked", False)
    if acked is True:
        return True
    reason_lines = [
        "拒绝: 任务理解未确认 (plan.md `task_understanding_acked: false`).",
        "",
        "**当前状态**: 用户还没批准你的任务理解, 任何关键 tool call 都被阻止.",
        "",
        "**修复 (按顺序)**:",
        "1. `Read .claude/state/task.md` (完整读完, 理解任务定义)",
        "2. `Read .claude/state/tool_constraints.md` (完整读完, 理解工具约束)",
        "3. 在 `.claude/state/plan.md` 的 '## 任务理解 checklist' 段填:",
        "   - 我对任务目标的理解 (用自己的话复述, 不复制 task.md)",
        "   - 我对范围的理解 (允许 / 禁止)",
        "   - 我对输入 / 输出的理解",
        "   - 关键假设 (我打算验证, 不是默认相信)",
        "   - 不明确的地方 (open questions)",
        "   - 第一个 step 草稿 (description / rationale / verification_criteria / expected_tools)",
        "4. **停下汇报**, 等用户审",
        "5. 用户审过, 改 plan.md frontmatter `task_understanding_acked: true`",
        "6. 然后才能 advance 第一个 step + 做关键 tool call",
        "",
        "**禁止**: 跳过 task understanding 直接 tool call. 即使用户口头说 '开始吧', 也必须先有 task_understanding_acked: true.",
    ]
    block("\n".join(reason_lines))
    return False


def check_tool_allowlist(tool_name):
    """
    通用工具白名单检查. 从 tool_constraints.md frontmatter 读 tool_allowlists.

    结构: tool_allowlists 是 dict, key 是 tool prefix, value 是允许的 tool 名列表.
    如果 tool_name 匹配某个 prefix, 则必须在 value 列表里, 否则 block.

    P1-3 修复: 校验 prefix 必须以 "__" 结尾 (例如 "mcp__CheatEngine__").
    不符合格式的 prefix 跳过并 log warning, 避免 "mcp__CheatEngine" (缺 __) 把
    "mcp__CheatEngineOther__foo" 也匹配进去.
    """
    constraints = load_tool_constraints()
    allowlists = constraints.get("tool_allowlists") or {}
    if not isinstance(allowlists, dict):
        log("tool_allowlists 配置错误 (不是 dict)", HOOK_NAME)
        return True  # 配置坏不阻塞

    # 找匹配的 prefix
    matched_prefix = None
    for prefix_raw in allowlists.keys():
        prefix = str(prefix_raw)
        # P1-3: 校验 prefix 格式
        if not prefix.endswith("__"):
            log(
                "tool_allowlists prefix '{0}' 不以 '__' 结尾, 跳过. 请改为 '{0}__'".format(
                    prefix
                ),
                HOOK_NAME,
            )
            continue
        if tool_name.startswith(prefix):
            matched_prefix = prefix
            break

    if matched_prefix is None:
        # 没有约束此 tool 家族, 通过
        return True

    allowed_list = allowlists.get(matched_prefix) or []
    if not isinstance(allowed_list, list):
        log("tool_allowlists.{0} 不是列表".format(matched_prefix), HOOK_NAME)
        return True

    if tool_name in allowed_list:
        return True

    # 不在白名单 → block
    reason_lines = [
        "拒绝: 工具 `{0}` 不在白名单 (`tool_constraints.md` → `tool_allowlists.{1}`).".format(
            tool_name, matched_prefix
        ),
        "",
        "**`{0}` 家族的白名单内仅有以下工具**:".format(matched_prefix),
        "",
    ]
    for t in allowed_list[:30]:  # 最多列 30 个
        reason_lines.append("- `{0}`".format(t))
    if len(allowed_list) > 30:
        reason_lines.append(
            "- ... ({0} 个总, 见 tool_constraints.md 完整列表)".format(
                len(allowed_list)
            )
        )
    reason_lines.extend(
        [
            "",
            "**修复**:",
            "- 如果这个工具应该被允许 (例如它是只读分析工具), 用户可以编辑 `.claude/state/tool_constraints.md`, 把它加到 `tool_allowlists.{0}` 列表".format(
                matched_prefix
            ),
            "- 如果这个工具是危险的 (调试 / 修改 / 执行), **不要绕过**, 用其他工具替代",
        ]
    )
    block("\n".join(reason_lines))
    return False


def check_require_plan(tool_name):
    """plan.md 必须有 active current_step."""
    plan_fm, _ = load_state_file(plan_path())
    current_step = plan_fm.get("current_step")
    if current_step and str(current_step).lower() != "null":
        return True
    reason_lines = [
        "拒绝: 调用关键工具 `{0}` 之前, `plan.md` 必须有 active `current_step`.".format(
            tool_name
        ),
        "",
        "当前 `plan.md` 的 `current_step` 为空, 说明没有正在进行的计划步骤.",
        "",
        "**修复**:",
        "1. 在 `plan.md` 写一个 step (含 `description`, `rationale`, `verification_criteria`, `expected_tools`)",
        "2. 把 `current_step` 设为该 step 的 id",
        "3. 把该 step 的 `status` 改为 `active`",
        "4. 然后再调用 tool",
    ]
    block("\n".join(reason_lines))
    return False


def check_dead_ends(tool_name, tool_input):
    """
    比对即将做的事是否在 dead_ends.md 里.

    简单实现: 找 dead_ends.md 里所有 ## D<id>: <title> 段, 检查段内是否
    同时出现 tool_name 和 tool_input 的关键值. 都出现 → 视作"重试已证伪方向" → block.

    这是粗略匹配, 可能误伤. 误伤时用户可手动改 dead_ends.md (例如把 tool_name 字符串改掉).
    """
    de_fm, de_body = load_state_file(dead_ends_path())
    count = de_fm.get("count", 0) or 0
    if count == 0 or not de_body:
        return True

    # 提取 tool_input 的关键 string/int 值, 作为匹配依据
    # P1-5 修复: 只对 "明显是 ID / 地址 / 长 hex" 类型值做严格匹配,
    # 避免通用字符串 (例如 "target.exe", "read_memory") 误伤
    key_values = []
    if isinstance(tool_input, dict):
        for k, v in tool_input.items():
            if not isinstance(v, (str, int)):
                continue
            s = str(v).strip()
            if len(s) < 6:
                continue
            # 只匹配这些类型:
            # 1. 十六进制地址 (0x 前缀)
            # 2. 长数字 (≥ 6 位, PID / 句柄 / 长度)
            # 3. 长 hex 字符串 (≥ 8 位, 可能是地址 / 字节序列)
            # 4. 以 F/D/S 开头的 id (F001 / D001 / S001)
            is_hex_addr = s.startswith("0x") and len(s) >= 6
            is_long_digit = s.isdigit() and len(s) >= 6
            is_long_hex = bool(re.fullmatch(r"[A-Fa-f0-9]{8,}", s))
            is_id = bool(re.fullmatch(r"[FDS]\d{3,}", s))
            if is_hex_addr or is_long_digit or is_long_hex or is_id:
                key_values.append(s)

    # 提取每个 dead end 的标题段
    pattern = re.compile(
        r"^##\s+(D\d+):\s*(.+?)(?=^##\s+D\d+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    for m in pattern.finditer(de_body):
        de_id = m.group(1)
        de_section = m.group(2)
        de_title = de_section.split("\n", 1)[0].strip()

        # tool_name 必须在 section 里
        if tool_name not in de_section:
            continue

        # tool_input 的至少一个关键值也要在 section 里 (避免误伤)
        if not key_values:
            # 没有 tool_input, 仅 tool_name 匹配, 不算 dead_end (太宽)
            continue

        if any(kv in de_section for kv in key_values):
            reason_lines = [
                "拒绝: 即将调用的 tool 跟已证伪的方向冲突.",
                "",
                "**冲突的 dead end**: `{0}: {1}`".format(de_id, de_title),
                "",
                "**即将做的**: `{0}` with input `{1}`".format(
                    tool_name, json.dumps(tool_input, ensure_ascii=False)
                ),
                "",
                "**修复 (沉没成本陷阱警告)**:",
                "如果有新证据表明应该重试, 必须在 `plan.md` 写一个新 step, 在 `rationale` 里 explicit 说明 '为什么之前的证伪不算数' (例如 cite 一个新的 F<id>), 让用户审过新 step 才允许重试.",
                "",
                "如果这是误伤 (tool_name + tool_input 跟 dead_end 看起来重叠但实际上是不同的事), 用户可以手动编辑 dead_ends.md 把 dead_end 的 disproven_by_input 改得更精确.",
            ]
            block("\n".join(reason_lines))
            return False

    return True


def check_environment_freshness(tool_name):
    """environment.md 的 last_verified_at 必须新鲜 (默认 5 分钟内).

    特殊情况: last_verified_at 为空 (首次使用 / 模板状态) → **不阻断**, 只 log warning.
    避免首次使用死锁 (因为验证环境本身要用 mcp__CheatEngine__read_memory 这种关键工具).
    AI 应该自觉先用 Bash tasklist + Edit environment.md 完成首次验证.
    """
    env_fm, _ = load_state_file(environment_path())
    last_verified = env_fm.get("last_verified_at", "")
    threshold = env_fm.get("stale_threshold_seconds", 300) or 300

    if not last_verified:
        # 首次使用: 不阻断, 但 log 警告. AI 应该自觉先验证环境.
        log(
            "environment.md last_verified_at 为空 (首次使用). 不阻断 {0}, 但 AI 应该尽快用 Bash + Edit 完成首次环境验证".format(
                tool_name
            ),
            HOOK_NAME,
        )
        return True

    try:
        last_dt = datetime.fromisoformat(last_verified)
        if last_dt.tzinfo:
            now = datetime.now(last_dt.tzinfo)
        else:
            now = datetime.now()
        age = (now - last_dt).total_seconds()
    except Exception as e:
        log("environment.md last_verified_at 解析失败: {0}".format(e), HOOK_NAME)
        # 解析失败时不阻塞 (避免 hook 自身的 bug 卡死工作)
        return True

    if age > threshold:
        reason_lines = [
            "拒绝: `environment.md` 上次验证 {0:.0f} 秒前 (超过 {1} 秒阈值).".format(
                age, threshold
            ),
            "",
            "**修复**: 重新验证环境后再调用. 见 environment.md 的 '验证方法' 表格.",
            "更新 `environment.md` 的 `last_verified_at` 字段为当前时间.",
        ]
        block("\n".join(reason_lines))
        return False

    return True


def block(reason):
    """exit 2 + stderr 注入 reason."""
    first_line = reason.split("\n", 1)[0]
    log("BLOCKED: " + first_line, HOOK_NAME)
    emit_to_stderr(reason)
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 第四轮 P1-B 修复: Fail Closed.
        # 原来异常时 exit 0 让 tool 通过, 这是 Fail Open - AI 可以通过触发 hook bug 绕过所有检查.
        # 改成 exit 2 + stderr 提示, 让用户看到并介入.
        import traceback

        tb = traceback.format_exc()
        log("FATAL: {0}\n{1}".format(e, tb), HOOK_NAME)
        emit_to_stderr(
            "**PreToolUse hook 内部错误 (Fail Closed)**: {0}\n\n"
            "为安全, 本次 tool call 被拒绝. 请检查 `.claude/logs/hook.log` 里的 traceback, 修复 hook 后重试.\n"
            "如果急需放行, 临时 rename `.claude/settings.json` → `.claude/settings.json.disabled` 禁用 hook.".format(
                e
            )
        )
        sys.exit(2)
