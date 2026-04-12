# -*- coding: utf-8 -*-
"""
UserPromptSubmit hook.

触发时机: 用户每次提交输入时 (在 Claude 看到之前).

行为:
- 读 plan.md frontmatter 拿 task_goal + current_step
- 读 plan.md 正文里 current_step 对应的 step description
- 读 dead_ends.md frontmatter 拿 count, 读正文拿所有 D<id> 标题
- 拼接简短提醒, print 到 stdout (会被 Claude Code 注入到 AI 上下文)
- 退出码 0

设计原则:
- 注入要简短 (不是每个 user prompt 都 dump 全部状态)
- 重点是 "目标提醒" + "当前进度" + "禁止重试的方向"
- 如果 plan.md 没有 active step, 强烈警告 AI 不允许做 tool call
- 这是 "每个回合" 的提醒, 跟 SessionStart 的 "会话开始" 一次性注入互补
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib import (
    log,
    read_stdin_json,
    load_claude_md_config,
    load_state_file,
    plan_path,
    dead_ends_path,
    emit_to_stdout,
)

HOOK_NAME = "user_prompt_submit"


def main():
    stdin = read_stdin_json()
    log("hook fired", HOOK_NAME)

    config = load_claude_md_config()

    parts = []
    parts.append("---")
    parts.append("# 每回合提醒 (UserPromptSubmit hook 注入)")
    parts.append("")

    # ============ task_summary ============
    task_summary = config.get("task_summary", "")
    if task_summary:
        first_line = str(task_summary).strip().split("\n")[0]
        parts.append("**任务摘要**: {0}".format(first_line))
        parts.append("**任务完整定义**: `.claude/state/task.md` (必须读)")
    else:
        parts.append(
            "**WARNING: 任务摘要未配置 (CLAUDE.md frontmatter task_summary 缺失)**"
        )

    parts.append("")

    # ============ task_understanding_acked ============
    plan_fm, plan_body = load_state_file(plan_path())
    acked = plan_fm.get("task_understanding_acked", False)
    if acked is True:
        parts.append("**任务理解**: ✓ 已确认 (task_understanding_acked: true)")
    else:
        parts.append(
            "**任务理解**: ✗ **未确认** (task_understanding_acked: false). "
            "**禁止任何关键 tool call**. 必须先 read task.md + tool_constraints.md, "
            "在 plan.md 写任务理解 checklist, 等用户审."
        )

    # ============ plan.md current_step ============
    current_step_id = plan_fm.get("current_step")
    plan_status = plan_fm.get("status", "unknown")

    if current_step_id and current_step_id != "null":
        step_desc = extract_step_description(plan_fm, str(current_step_id))
        parts.append(
            "**当前 step**: `{0}` (status={1})".format(current_step_id, plan_status)
        )
        if step_desc:
            parts.append("  - description: {0}".format(step_desc))
    else:
        parts.append(
            "**当前 step**: 无 (plan.md `current_step: null`). 不允许关键 tool call."
        )

    # ============ dead_ends 标题列表 ============
    de_fm, de_body = load_state_file(dead_ends_path())
    de_count = de_fm.get("count", 0)

    if de_count and de_count > 0:
        titles = extract_dead_end_titles(de_body)
        if titles:
            parts.append("")
            parts.append("**已证伪方向 ({0} 条, 不允许重试)**:".format(de_count))
            for title in titles:
                parts.append("  - {0}".format(title))
    else:
        parts.append("")
        parts.append("**已证伪方向**: 无 (dead_ends.md 为空)")

    # ============ 行为提醒 ============
    parts.append("")
    parts.append("**强制行为**:")
    parts.append(
        "1. 任何事实主张必须 cite F<id> / memory/<file>.md / user-told (Stop hook L1-L4 检查)."
    )
    parts.append("2. 禁止 hedging 词 (可能 / 似乎 / 应该 / 因此 / 推断 ...).")
    parts.append("3. 5 个 tool call 没产生新 fact → 必须停下汇报.")
    parts.append("4. 任何 tool call 前比对 dead_ends.md, 已证伪的方向不允许重试.")
    parts.append("5. 用户告知的事实写到 facts.md source: user, **不允许反向质疑**.")
    parts.append(
        "6. **结论声明必须自证 + 独立审查** (Stop hook L5): 回复含 '结论' / '已完成' / '已验证' / '最终' 等触发词 → "
        "必须含 `## 自证与审查` 段 + 用 Agent 工具调用 `superpowers:code-reviewer` 做独立审查. "
        "reviewer 若说 not-passed, 必须修复后再审 (最多 3 轮). 详细见 CLAUDE.md."
    )
    parts.append("---")

    output = "\n".join(parts)
    emit_to_stdout(output)
    log("injected {0} bytes".format(len(output)), HOOK_NAME)
    sys.exit(0)


def extract_step_description(plan_fm, step_id):
    """从 plan.md frontmatter 的 steps 列表里找指定 id 的 step description."""
    steps = plan_fm.get("steps") or []
    if not isinstance(steps, list):
        return None
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("id", "")) == step_id:
            desc = step.get("description")
            if desc:
                return str(desc).strip()
    return None


def extract_dead_end_titles(body):
    """
    从 dead_ends.md 正文里抓所有 ## D<id>: <title> 形式的标题.
    例如: '## D001: 在 0x... 设 bp 找心跳加密入口'
    返回 [title, ...].
    """
    if not body:
        return []
    titles = []
    pattern = re.compile(r"^##\s+(D\d+:\s*.+)$", re.MULTILINE)
    for match in pattern.finditer(body):
        titles.append(match.group(1).strip())
    return titles


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("FATAL: {0}".format(e), HOOK_NAME)
        # 不阻塞: 失败时不影响用户输入提交
        emit_to_stdout(
            "# WARNING: UserPromptSubmit hook failed: {0}\n"
            "# 详见 .claude/logs/hook.log".format(e)
        )
        sys.exit(0)
