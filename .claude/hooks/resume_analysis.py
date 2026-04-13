# -*- coding: utf-8 -*-
"""
/resumeAnalysis 触发的初始化脚本.

行为:
1. 校验 plan.md 是 paused 状态
2. 读 pause_snapshot 路径, 校验快照文件存在
3. 输出引导信息 + 快照内容预览, 强制 AI 重读所有相关状态文件
4. 原子改 plan.md frontmatter: paused=false (但保留 paused_at 和 pause_snapshot 作为 audit trail)

不接收 stdin, 不是 hook, 由 slash command 调用.
"""
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib import (
    log,
    state_dir,
    plan_path,
    load_state_file,
    set_yaml_frontmatter_field,
)

HOOK_NAME = "resume_analysis"


def main():
    log("resume_analysis triggered", HOOK_NAME)

    plan_p = plan_path()
    if not plan_p.exists():
        print("ERROR: plan.md 不存在: {0}".format(plan_p))
        print("没有任务. 请 /newAnalysis 初始化.")
        sys.exit(1)

    plan_fm, _ = load_state_file(plan_p)
    paused = plan_fm.get("paused", False)
    snapshot_rel = plan_fm.get("pause_snapshot")
    paused_at = plan_fm.get("paused_at")

    if paused is not True:
        print("ERROR: plan.md `paused: false` (或字段缺失). 当前没有处于暂停状态.")
        print("如果想暂停, 请使用 /pauseAnalysis.")
        print("如果想开始新任务, 请使用 /newAnalysis.")
        sys.exit(1)

    if not snapshot_rel:
        print("ERROR: plan.md `pause_snapshot` 字段为空, 无法定位快照.")
        print(
            "请手动检查 .claude/state/snapshots/ 找到最近的快照, 然后修复 plan.md frontmatter."
        )
        sys.exit(1)

    state = state_dir()
    # snapshot_rel 形如 ".claude/state/snapshots/20260413_120000.md"
    # 相对项目根, 转成绝对路径
    snapshot_abs = state.parent.parent / snapshot_rel
    if not snapshot_abs.exists():
        # 兼容: 也试一下相对 state_dir 的路径
        alt = state / "snapshots" / Path(snapshot_rel).name
        if alt.exists():
            snapshot_abs = alt
        else:
            print("ERROR: 快照文件不存在: {0}".format(snapshot_rel))
            print("尝试过的路径:")
            print("  - {0}".format(state.parent.parent / snapshot_rel))
            print("  - {0}".format(alt))
            print(
                "请检查 .claude/state/snapshots/ 目录, 或手动修复 plan.md `pause_snapshot` 字段."
            )
            sys.exit(1)

    # 读快照内容预览 (前 80 行)
    try:
        snapshot_text = snapshot_abs.read_text(encoding="utf-8")
    except Exception as e:
        print("ERROR: 读快照失败: {0}".format(e))
        sys.exit(1)

    # 计算暂停时长
    pause_duration = ""
    if paused_at:
        try:
            paused_dt = datetime.fromisoformat(
                str(paused_at).strip().strip('"').strip("'")
            )
            now = datetime.now(paused_dt.tzinfo) if paused_dt.tzinfo else datetime.now()
            delta = now - paused_dt
            hours = delta.total_seconds() / 3600
            if hours < 1:
                pause_duration = "{0:.0f} 分钟".format(delta.total_seconds() / 60)
            elif hours < 24:
                pause_duration = "{0:.1f} 小时".format(hours)
            else:
                pause_duration = "{0:.1f} 天".format(hours / 24)
        except Exception:
            pass

    # 清 paused 标志 (保留 paused_at 和 pause_snapshot 作 audit trail)
    try:
        plan_text = plan_p.read_text(encoding="utf-8")
        plan_text = set_yaml_frontmatter_field(plan_text, "paused", "false")
        plan_p.write_text(plan_text, encoding="utf-8")
    except Exception as e:
        print("ERROR: 改 plan.md frontmatter 失败: {0}".format(e))
        sys.exit(1)

    log("cleared paused flag", HOOK_NAME)

    # 输出引导
    print("=" * 60)
    print("/resumeAnalysis 第一阶段完成")
    print("=" * 60)
    print()
    print("**暂停时长**: {0}".format(pause_duration or "(未知)"))
    print("**暂停快照**: `{0}`".format(snapshot_rel))
    print()
    print(
        "**已清除 plan.md `paused` 标志** (paused_at / pause_snapshot 保留为 audit trail)."
    )
    print()
    print("---")
    print()
    print("**强制行为 (AI 必须严格按顺序做)**:")
    print()
    print("1. **Read 暂停快照** (这是最重要的, 拿恢复上下文):")
    print("   - `{0}`".format(snapshot_rel))
    print()
    print("2. **Read 当前所有状态文件** (不依赖记忆, 防漂移):")
    print("   - `.claude/state/task.md` (任务目标)")
    print("   - `.claude/state/tool_constraints.md` (工具约束)")
    print("   - `.claude/state/plan.md` (计划)")
    print("   - `.claude/state/facts.md` (已证实事实)")
    print("   - `.claude/state/dead_ends.md` (已证伪方向, 不要重试)")
    print("   - `.claude/state/environment.md` (环境状态, 看是否过期)")
    print()
    print("3. **Read 跨会话记忆**: 列 memory 目录, Read 所有 .md")
    print()
    print("4. **Bash** 看期间是否有变化:")
    print("   - `git log --oneline -20` (commits)")
    print("   - 注意 environment.md `last_verified_at` 是否过期 (>5 分钟需要重新验证)")
    print()
    print("5. **输出 '恢复 brief'** (在回复中):")
    print("   - **任务目标**: 一句话复述 task.md")
    print("   - **暂停时的状态**: 引用快照里的 '当前 step 状态'")
    print("   - **当前 plan**: current_step 是什么, 已经做完哪些 fact")
    print("   - **期间的变化**: git log diff / mtime 看是否有用户改动")
    print("   - **第一个具体动作**: 恢复后要做的下一件事 (cite F<id> 或 step id)")
    print()
    print("6. **停下汇报**, 等用户确认 brief 后才能 advance + 做关键 tool call.")
    print(
        "   - **重要**: 即使 plan.md current_step 还是 active, 你也必须先停下让用户审 brief."
    )
    print("   - 用户确认后, 你才能继续工作.")
    print()
    print("---")
    print()
    print("**快照内容预览** (前 80 行):")
    print()
    preview_lines = snapshot_text.split("\n")[:80]
    for line in preview_lines:
        print("  | " + line)
    if len(snapshot_text.split("\n")) > 80:
        print("  | ... (更多内容请 Read 完整快照)")
    print()
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("FATAL: {0}".format(e), HOOK_NAME)
        print("ERROR: {0}".format(e))
        import traceback

        traceback.print_exc()
        sys.exit(1)
