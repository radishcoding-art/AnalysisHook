# -*- coding: utf-8 -*-
"""
新任务初始化脚本. 由 /newAnalysis slash command 调用.

行为:
1. 把当前 .claude/state/{plan,facts,dead_ends,environment,task}.md 备份到 .claude/state/archive/<timestamp>/
2. 从 .claude/state/templates/ 复制全部模板到 .claude/state/
3. 输出引导信息, 提示用户编辑 task.md

**不动**:
- tool_constraints.md (项目级永久)
- hook.log (历史日志)
- expired_facts.md (历史归档)

不需要 stdin (这个脚本不是 hook, 是 slash command 调用的工具).
"""
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib import (
    log,
    state_dir,
    plan_path,
    facts_path,
    dead_ends_path,
    environment_path,
)

HOOK_NAME = "new_analysis"

# 要重置的文件 (filename → 是否必须从 templates 复制)
RESET_FILES = [
    "plan.md",
    "facts.md",
    "dead_ends.md",
    "environment.md",
    "task.md",
]

# 不动的文件
PRESERVE_FILES = [
    "tool_constraints.md",
    "hook.log",
    "expired_facts.md",
]


def main():
    log("new_analysis triggered", HOOK_NAME)
    state = state_dir()
    templates = state / "templates"

    if not templates.exists():
        print("ERROR: templates 目录不存在: {0}".format(templates))
        print("无法重置. 请确认 .claude/state/templates/ 存在 + 含完整模板.")
        sys.exit(1)

    # 检查模板完整性
    missing = []
    for fname in RESET_FILES:
        if not (templates / fname).exists():
            missing.append(fname)
    if missing:
        print("ERROR: 模板缺失: {0}".format(", ".join(missing)))
        print("请补充 .claude/state/templates/ 后重试.")
        sys.exit(1)

    # 1. 备份当前状态到 archive/<timestamp>/
    # P1-6 修复: 备份阶段加 try/except, 任一失败就 abort, 不进 reset 阶段,
    # 避免 "备份部分成功就 reset" 导致数据丢失.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = state / "archive" / timestamp
    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print("ERROR: 无法创建备份目录 {0}: {1}".format(archive_dir, e))
        print("终止重置, 当前 state 保持不变.")
        sys.exit(1)

    backed_up = []
    backup_failed = False
    for fname in RESET_FILES:
        src = state / fname
        if not src.exists():
            continue
        try:
            shutil.copy2(src, archive_dir / fname)
            backed_up.append(fname)
        except Exception as e:
            print("ERROR: 备份 {0} 失败: {1}".format(fname, e))
            backup_failed = True
            break

    if backup_failed:
        print()
        print("终止重置 (备份阶段失败). 当前 state 保持不变.")
        print("已成功备份: {0}".format(", ".join(backed_up) if backed_up else "无"))
        print("请检查 {0} 的权限和磁盘空间后重试.".format(archive_dir))
        sys.exit(1)

    log(
        "backed up {0} files to {1}".format(len(backed_up), archive_dir),
        HOOK_NAME,
    )

    # 2. 从 templates 复制 (备份全部成功后才进入这一步)
    copied = []
    reset_failed = False
    for fname in RESET_FILES:
        src = templates / fname
        dst = state / fname
        try:
            shutil.copy2(src, dst)
            copied.append(fname)
        except Exception as e:
            print("ERROR: 重置 {0} 失败: {1}".format(fname, e))
            reset_failed = True
            break

    if reset_failed:
        print()
        print("警告: 重置阶段部分失败. 已重置: {0}".format(", ".join(copied)))
        print("未重置的文件仍是旧状态. 备份在 {0}.".format(archive_dir))
        print("您可以手动从备份恢复或手动继续重置.")
        sys.exit(1)

    log("reset {0} files from templates".format(len(copied)), HOOK_NAME)

    # 3. 输出引导信息
    print("=" * 60)
    print("新任务初始化完成")
    print("=" * 60)
    print()
    print(
        "**已备份**: {0} 个文件 → `.claude/state/archive/{1}/`".format(
            len(backed_up), timestamp
        )
    )
    if backed_up:
        for f in backed_up:
            print("  - {0}".format(f))
    print()
    print("**已重置** (从 .claude/state/templates/ 复制):")
    for f in copied:
        print("  - {0}".format(f))
    print()
    print("**未动**:")
    for f in PRESERVE_FILES:
        p = state / f
        if p.exists():
            print("  - {0} (项目级永久)".format(f))
    print()
    print("---")
    print()
    print("**下一步 (用户)**:")
    print()
    print("1. 编辑 `.claude/state/task.md`, 填写任务详细描述 (10 个章节, 越详细越好)")
    print("2. 编辑完后告诉 AI: 例如 '任务写完了, 你读一下'")
    print()
    print("**下一步 (AI)**:")
    print()
    print("1. read `.claude/state/task.md` (完整内容)")
    print("2. read `.claude/state/tool_constraints.md` (工具约束)")
    print("3. 在 `.claude/state/plan.md` 的 '## 任务理解 checklist' 段输出:")
    print("   - 我对任务目标的理解 (用自己的话复述)")
    print("   - 我对范围的理解 (允许 / 禁止)")
    print("   - 关键假设 (打算验证, 不是默认相信)")
    print("   - 不明确的地方")
    print("   - 第一个 step 草稿")
    print("4. 停下汇报, 等用户审")
    print("5. 用户审过, 改 plan.md frontmatter `task_understanding_acked: true`")
    print("6. AI 才能 advance 第一个 step + 做关键 tool call")
    print()
    print(
        "**重要**: 在 `task_understanding_acked` 改成 true 之前, PreToolUse hook 拒绝任何关键 tool call."
    )
    print()
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("FATAL: {0}".format(e), HOOK_NAME)
        print("ERROR: {0}".format(e))
        sys.exit(1)
