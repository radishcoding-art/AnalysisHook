# -*- coding: utf-8 -*-
"""
Hook 共享库. 所有 hook 脚本 import 此文件.

提供:
- 项目路径 (基于 CLAUDE_PROJECT_DIR 环境变量)
- stdin JSON 读取
- markdown frontmatter 解析 (用 pyyaml)
- 状态文件 (CLAUDE.md / facts.md / plan.md / dead_ends.md / environment.md) 读取
- 日志 (写到 .claude/state/hook.log)
- 通用工具

依赖: pyyaml (Python 标准库 + pyyaml). 不依赖其他第三方包.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# ============================================================
# 项目路径
# ============================================================


def project_dir():
    """项目根目录. 优先用 CLAUDE_PROJECT_DIR 环境变量."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    # fallback: 从 hook 脚本位置往上找 (.claude/hooks → 项目根)
    return Path(__file__).resolve().parent.parent.parent


def claude_dir():
    return project_dir() / ".claude"


def state_dir():
    return claude_dir() / "state"


def hooks_dir():
    return claude_dir() / "hooks"


def logs_dir():
    """Hook 日志目录. 独立于 state/, 不污染状态文件."""
    return claude_dir() / "logs"


def claude_md_path():
    return project_dir() / "CLAUDE.md"


def task_path():
    return state_dir() / "task.md"


def tool_constraints_path():
    return state_dir() / "tool_constraints.md"


def plan_path():
    return state_dir() / "plan.md"


def facts_path():
    return state_dir() / "facts.md"


def dead_ends_path():
    return state_dir() / "dead_ends.md"


def environment_path():
    return state_dir() / "environment.md"


def expired_facts_path():
    return state_dir() / "expired_facts.md"


def load_tool_constraints():
    """读 tool_constraints.md frontmatter, 返回 dict (含 tool_allowlists 等)."""
    content = read_file_safe(tool_constraints_path())
    fm, _ = split_frontmatter(content)
    return fm


def log_path():
    """Hook 日志路径. 独立 logs 目录, 方便归档和 gitignore."""
    return logs_dir() / "hook.log"


def compute_memory_dir():
    """
    自动计算 Claude Code memory 目录路径.

    基于 CLAUDE_PROJECT_DIR 环境变量 + Claude Code 的 slug 规则:
        E:\\WorkSpace\\Go\\my-project → E--WorkSpace-Go-my-project

    Slug 规则: `:` 和 `\\` 和 `/` 都替换成 `-`.
    例如 E:\\WorkSpace → E- (from E:) + -WorkSpace (from \\WorkSpace) = E--WorkSpace

    返回 Path 或 None (如果 CLAUDE_PROJECT_DIR 未设).
    """
    proj = os.environ.get("CLAUDE_PROJECT_DIR")
    if not proj:
        # fallback 用 _lib.py 所在路径推算
        proj = str(project_dir())
    slug = proj.replace(":", "-").replace("\\", "-").replace("/", "-")
    userprofile = os.environ.get("USERPROFILE") or os.environ.get("HOME", "")
    if not userprofile:
        return None
    return Path(userprofile) / ".claude" / "projects" / slug / "memory"


def resolve_memory_dir(config):
    """
    根据 CLAUDE.md frontmatter 的 memory_dir 字段解析最终路径.
    支持:
    - 固定路径 (含环境变量例如 %USERPROFILE%)
    - "auto" 或空 → 用 compute_memory_dir() 自动计算
    """
    memory_dir_str = config.get("memory_dir", "") or ""
    memory_dir_str = str(memory_dir_str).strip()
    if memory_dir_str == "" or memory_dir_str.lower() == "auto":
        return compute_memory_dir()
    return Path(os.path.expandvars(memory_dir_str))


# ============================================================
# 日志
# ============================================================


_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def log(message, hook_name="?"):
    """
    写日志到 .claude/logs/hook.log.
    日志失败时静默 fail (不阻塞 hook 主流程).
    P2-2 修复: 简单的 log rotation. 文件 > 10MB 时 rename 成 hook.log.old 再新建.
    """
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # P2-2: log rotation (简单版)
        try:
            if path.exists() and path.stat().st_size > _LOG_MAX_BYTES:
                old_path = path.with_suffix(".log.old")
                if old_path.exists():
                    old_path.unlink()
                path.rename(old_path)
        except Exception:
            # rotation 失败不阻塞 log 写入
            pass
        ts = datetime.now().isoformat(timespec="seconds")
        line = "[{0}] [{1}] {2}\n".format(ts, hook_name, message)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ============================================================
# stdin JSON
# ============================================================


def read_stdin_json():
    """
    读 hook 的 stdin JSON.
    失败时返回 {} (不阻塞 hook).
    """
    try:
        raw = sys.stdin.buffer.read()
        if not raw.strip():
            return {}
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        log("failed to read stdin JSON: {0}".format(e), "_lib")
        return {}


def read_stdin_json_strict():
    """
    严格版 stdin JSON reader. parse 失败直接抛异常, 不 silent-swallow.
    PreToolUse hook 用这个, 保证 Fail Closed.
    """
    raw = sys.stdin.buffer.read()
    if not raw.strip():
        return {}
    return json.loads(raw.decode("utf-8"))


# ============================================================
# 文件读取
# ============================================================


def read_file_safe(path):
    """
    读 UTF-8 文件. 失败时返回空字符串 + log.
    """
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception as e:
        log("failed to read {0}: {1}".format(path, e), "_lib")
        return ""


# ============================================================
# Frontmatter 解析
# ============================================================


def split_frontmatter(content):
    """
    把 markdown 内容拆成 (frontmatter_dict, body_str).
    Frontmatter 必须以 --- 开头, 以 --- 结束.
    没有 frontmatter 时返回 ({}, content).
    解析失败时返回 ({}, content) 并 log.
    """
    if not content.startswith("---"):
        return {}, content

    lines = content.split("\n")
    fm_end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm_end = i
            break

    if fm_end == -1:
        return {}, content

    fm_text = "\n".join(lines[1:fm_end])
    body = "\n".join(lines[fm_end + 1 :])

    if not HAS_YAML:
        log("pyyaml not installed, frontmatter not parsed", "_lib")
        return {}, body

    try:
        fm_dict = yaml.safe_load(fm_text) or {}
        if not isinstance(fm_dict, dict):
            log(
                "frontmatter is not a dict (got {0})".format(type(fm_dict).__name__),
                "_lib",
            )
            return {}, body
        return fm_dict, body
    except Exception as e:
        log("failed to parse frontmatter yaml: {0}".format(e), "_lib")
        return {}, body


# ============================================================
# 状态文件配置加载
# ============================================================


def load_claude_md_config():
    """读 CLAUDE.md frontmatter, 返回配置 dict."""
    content = read_file_safe(claude_md_path())
    fm, _ = split_frontmatter(content)
    return fm


def load_state_file(path):
    """
    读状态文件, 返回 (frontmatter_dict, body_str).
    用于 plan.md / facts.md / dead_ends.md / environment.md.
    """
    content = read_file_safe(path)
    return split_frontmatter(content)


# ============================================================
# 输出辅助
# ============================================================


def emit_to_stdout(text):
    """
    把内容输出到 stdout (Claude Code 会注入到 system prompt).
    确保使用 UTF-8 (Windows 默认 cp936 会破坏中文).
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def emit_to_stderr(text):
    """把错误信息输出到 stderr (会被 Claude Code 注入到 AI context 当作反馈)."""
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.stderr.write(text)
    if not text.endswith("\n"):
        sys.stderr.write("\n")
    sys.stderr.flush()


def emit_block_decision(reason):
    """
    输出 JSON 阻断决定 (用于 UserPromptSubmit / Stop hook).
    Claude Code 会把这个解析为 block + reason.
    """
    out = {"decision": "block", "reason": reason}
    emit_to_stdout(json.dumps(out, ensure_ascii=False))
