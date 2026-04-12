# -*- coding: utf-8 -*-
"""
SessionStart hook.

触发时机: 会话开始 / 恢复 / 清空 / 压缩后.

行为:
- 读 CLAUDE.md frontmatter 拿 must_read_on_session_start 列表 + memory_dir
- 读所有列出的状态文件 (plan.md / facts.md / dead_ends.md / environment.md)
- 读 memory_dir 下所有 .md 文件 (跨会话长期记忆, 内容可能未经验证)
- 读 git log --oneline -20 (上次会话的进度)
- 拼接成一段 system prompt 注入文本, print 到 stdout
- 退出码 0

source 字段处理 (Claude Code 在 stdin JSON 里给):
- startup / clear: 清扫 facts.md 里 expires=until_session_end 的 entry
- resume / compact: 不清扫 (因为是同一个会话的延续)
"""
import os
import subprocess
import sys
from pathlib import Path

# 让脚本能 import 同目录的 _lib
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib import (
    log,
    read_stdin_json,
    load_claude_md_config,
    project_dir,
    read_file_safe,
    facts_path,
    expired_facts_path,
    split_frontmatter,
    HAS_YAML,
    emit_to_stdout,
    resolve_memory_dir,
)

HOOK_NAME = "session_start"


def main():
    stdin = read_stdin_json()
    source = stdin.get("source", "unknown")
    log("hook fired, source={0}".format(source), HOOK_NAME)

    config = load_claude_md_config()
    if not config:
        # CLAUDE.md frontmatter 缺失或解析失败
        log("CLAUDE.md frontmatter empty or missing", HOOK_NAME)
        emit_to_stdout(
            "# WARNING: CLAUDE.md frontmatter could not be loaded.\n"
            "# Hook 系统未完全激活. 请检查 CLAUDE.md 是否存在 + frontmatter 格式正确."
        )
        sys.exit(0)

    parts = []

    # ============ Header ============
    parts.append("=" * 70)
    parts.append("# SessionStart 注入 (source={0})".format(source))
    parts.append("=" * 70)
    parts.append("")
    parts.append(
        "以下是项目状态文件 + 长期记忆 + git history. 这是您 (Claude) 工作的事实基础."
    )
    parts.append(
        "**任何事实主张必须 cite 这里出现的 F<id> / memory/<file>.md / user-told.**"
    )
    parts.append("Stop hook 会扫描每个回复, 没 cite 的事实主张会被 exit 2 拒绝.")
    parts.append("")

    # ============ 任务摘要 (来自 frontmatter) ============
    task_summary = config.get("task_summary", "")
    if task_summary:
        parts.append("## 任务摘要 (一行, 完整定义见 task.md)")
        parts.append("")
        parts.append(str(task_summary).strip())
        parts.append("")
    parts.append(
        "**重要**: 任务的**完整描述**在 `.claude/state/task.md`. AI 必须先读它. "
        "如果 `plan.md` 的 `task_understanding_acked` 是 false, AI 必须先 read task.md + tool_constraints.md, "
        "在 plan.md 写任务理解 checklist, 等用户审过. 期间禁止任何关键 tool call."
    )
    parts.append("")

    # ============ 状态文件 ============
    must_read = config.get("must_read_on_session_start", []) or []
    if must_read:
        parts.append("## 状态文件 (`.claude/state/`)")
        parts.append("")
        proj = project_dir()
        for file_rel in must_read:
            file_path = proj / file_rel
            if not file_path.exists():
                parts.append("### {0} (FILE NOT FOUND)".format(file_rel))
                parts.append("")
                continue
            content = read_file_safe(file_path)
            parts.append("### {0}".format(file_rel))
            parts.append("")
            parts.append("````markdown")
            parts.append(content.rstrip())
            parts.append("````")
            parts.append("")

    # ============ Memory 文件 ============
    # 用 resolve_memory_dir 支持 "auto" (根据 CLAUDE_PROJECT_DIR 自动计算 slug)
    memory_dir = resolve_memory_dir(config)
    # P0-6 修复: 输出 memory dir 的绝对路径让用户核对 (slug 算法 silent failure 风险)
    parts.append("## Memory 目录诊断")
    parts.append("")
    if memory_dir is None:
        parts.append(
            "⚠️ **memory_dir 无法解析**. "
            "`memory_dir` 配置是 `auto` 但 `CLAUDE_PROJECT_DIR` 和 `USERPROFILE` 都没设. "
            "Memory 注入被跳过."
        )
        parts.append("")
    else:
        parts.append("- **解析后路径**: `{0}`".format(memory_dir))
        parts.append("- **存在**: {0}".format(memory_dir.exists()))
        if memory_dir.exists() and memory_dir.is_dir():
            md_files = sorted(memory_dir.glob("*.md"))
            parts.append("- **.md 文件数**: {0}".format(len(md_files)))
        parts.append("")
        parts.append(
            "**重要**: 如果路径不对 (例如项目搬家 / slug 算法跟 Claude Code 真实规则不匹配), "
            "用户应该手动在 CLAUDE.md frontmatter 写具体路径覆盖 `memory_dir: auto`."
        )
        parts.append("")
    if memory_dir is not None:
        if memory_dir.exists() and memory_dir.is_dir():
            parts.append("## Memory (跨会话长期记忆)")
            parts.append("")
            parts.append(
                "**重要**: memory 内容**可能未经事实验证**, 不能直接当作 fact. "
                "想在事实主张里 cite, 用 `memory/<file>.md` 标记."
            )
            parts.append("")
            md_files = sorted(memory_dir.glob("*.md"))
            if not md_files:
                parts.append("(memory 目录为空)")
                parts.append("")
            for md in md_files:
                content = read_file_safe(md)
                parts.append("### memory/{0}".format(md.name))
                parts.append("")
                parts.append("````markdown")
                parts.append(content.rstrip())
                parts.append("````")
                parts.append("")
        else:
            parts.append("## Memory (DIRECTORY NOT FOUND: {0})".format(memory_dir))
            parts.append("")

    # ============ Git history ============
    parts.append("## Git history (最近 20 条)")
    parts.append("")
    try:
        git_log = subprocess.run(
            ["git", "log", "--oneline", "-20"],
            cwd=str(project_dir()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        if git_log.returncode == 0 and git_log.stdout.strip():
            parts.append("```")
            parts.append(git_log.stdout.rstrip())
            parts.append("```")
        else:
            err = git_log.stderr.strip() if git_log.stderr else "no output"
            parts.append("(git log returned no commits or failed: {0})".format(err))
    except Exception as e:
        parts.append("(git log exception: {0})".format(e))
        log("git log failed: {0}".format(e), HOOK_NAME)
    parts.append("")

    # ============ 清扫 expired facts ============
    if source in ("startup", "clear"):
        cleaned = sweep_expired_facts()
        parts.append("## SessionStart 清扫")
        parts.append("")
        if cleaned > 0:
            parts.append(
                "已清扫 {0} 条 `expires=until_session_end` 的 fact 到 `expired_facts.md`. "
                "**这些 fact 在新会话中不再有效, 不允许 cite.**".format(cleaned)
            )
        else:
            parts.append("没有需要清扫的 expired fact.")
        parts.append("")
    elif source in ("resume", "compact"):
        parts.append("## SessionStart 清扫")
        parts.append("")
        parts.append(
            "source={0}, **不**清扫 expired facts (因为这是同一会话的延续).".format(
                source
            )
        )
        parts.append("")

    parts.append("=" * 70)
    parts.append("# (注入结束)")
    parts.append("=" * 70)

    output = "\n".join(parts)
    emit_to_stdout(output)
    log("injected {0} bytes".format(len(output)), HOOK_NAME)
    sys.exit(0)


def sweep_expired_facts():
    """
    把 facts.md 里 expires=until_session_end 的 entry 移到 expired_facts.md.
    返回清扫的数量.

    实现说明:
    - facts.md 当前是模板状态, entries 部分是 (empty)
    - 等 AI 真的写了 fact 之后, 这里会做实际的解析和移动
    - 解析逻辑: 找 entries 段下的 yaml list, 检查每个 entry 的 expires 字段
    - 把符合的 entry 从 facts.md 移到 expired_facts.md (保留 id)

    当前简化版: 只 log, 不做实际移动. 等 AI 写第一个 fact 后再增强.
    """
    if not HAS_YAML:
        log("cannot sweep without pyyaml", HOOK_NAME)
        return 0

    facts_p = facts_path()
    if not facts_p.exists():
        return 0

    content = read_file_safe(facts_p)
    fm, body = split_frontmatter(content)

    # facts.md 当前的 frontmatter 有 count 字段
    count = fm.get("count", 0)
    if count == 0:
        # 还没有任何 fact, 不需要清扫
        return 0

    # TODO: 等 AI 写实际 fact 后实现 entry 解析
    # 当前 facts.md 模板的 entries 部分是 markdown 文本 "(empty)", 不是真正的 yaml list
    # 等用户开始用之后, 我们要 (a) 决定 entries 的具体存储格式 (b) 实现解析和移动
    log(
        "sweep_expired_facts: count={0}, entry parsing not yet implemented".format(
            count
        ),
        HOOK_NAME,
    )
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("FATAL: {0}".format(e), HOOK_NAME)
        # 不阻塞: hook 失败时还是让 session 启动
        emit_to_stdout(
            "# WARNING: SessionStart hook failed: {0}\n"
            "# 详见 .claude/logs/hook.log".format(e)
        )
        sys.exit(0)
