# -*- coding: utf-8 -*-
"""
/pauseAnalysis 触发的初始化脚本.

行为:
1. 校验当前有 active 任务 (plan.md 含 task_understanding_acked: true 且 status != empty/completed)
2. 创建 .claude/state/snapshots/ 目录 (如果没有)
3. 生成 timestamp 和 snapshot 文件路径
4. 在 snapshots/ 写一个**空快照模板**, 含必填章节, 由 AI 接下来填
5. 原子改 plan.md frontmatter: paused=true / paused_at=<ISO> / pause_snapshot=<path>
6. 输出引导信息, 告诉 AI 接下来要做什么 (填快照 + 写 memory + 汇报)

不接收 stdin, 不是 hook, 由 slash command 调用.
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
    load_state_file,
    set_yaml_frontmatter_field,
)

HOOK_NAME = "pause_analysis"

SNAPSHOT_TEMPLATE = """---
created_at: "{created_at}"
plan_status_at_pause: "{plan_status}"
current_step_at_pause: "{current_step}"
---

# 暂停快照 {timestamp}

此快照由 /pauseAnalysis 创建. AI 必须填完所有章节, 然后通过 /resumeAnalysis 恢复.

## 任务摘要

(一句话复述 task.md 的目标)

## 当前 step 状态

- **current_step**: {current_step}
- **已完成的部分**: (列出该 step 下已经做完的具体动作)
- **剩余未做的部分**: (列出该 step 还没做的具体动作)
- **下一个具体动作**: (恢复后第一件要做的事, 越具体越好)

## 关键 facts (按相关度排序)

(列出对当前 step 最重要的 F<id>, 每条一行: `F012: <一句话>`)

## 已证伪方向 (D<id>)

(列出迄今为止的 dead ends, 提醒未来不要重试)

## 当前环境状态

(从 environment.md 摘要: 进程 PID / 模块基址 / 关键句柄等)

## 未解决的问题 (open questions)

(列出当前还没答案的问题, 恢复后要重点处理)

## 下次恢复关键上下文

(一段话, 让 AI 重启会话后秒速进入状态. 包含: 任务在哪一步 / 关键 F<id> 是什么 / 下一动作是什么 / 注意什么坑)

## 阻塞 / 等待 (如果有)

(如果暂停时正在等用户做某事, 在这里写明)
"""


def main():
    log("pause_analysis triggered", HOOK_NAME)

    # 1. 校验 plan.md 状态
    plan_p = plan_path()
    if not plan_p.exists():
        print("ERROR: plan.md 不存在: {0}".format(plan_p))
        print("没有 active 任务, 无法暂停. 请先 /newAnalysis 初始化任务.")
        sys.exit(1)

    plan_fm, _ = load_state_file(plan_p)
    acked = plan_fm.get("task_understanding_acked", False)
    plan_status = str(plan_fm.get("status", "") or "").strip().lower()
    current_step = plan_fm.get("current_step")
    already_paused = plan_fm.get("paused", False)

    if not acked:
        print("ERROR: plan.md `task_understanding_acked: false`, 任务还没正式开始.")
        print("没有进度可以暂停. 请先完成任务理解流程.")
        sys.exit(1)

    if plan_status in ("empty", "completed"):
        print(
            "ERROR: plan.md status=`{0}`, 没有 active 工作可以暂停.".format(plan_status)
        )
        sys.exit(1)

    if already_paused is True:
        print("WARNING: plan.md `paused: true`, 任务已经处于暂停状态.")
        print("如果要更新快照, 先 /resumeAnalysis 然后再 /pauseAnalysis.")
        print("如果要继续暂停, 不需要任何操作.")
        sys.exit(1)

    # 2. 准备 snapshot 目录
    state = state_dir()
    snapshots_dir = state / "snapshots"
    try:
        snapshots_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print("ERROR: 无法创建 snapshots 目录: {0}".format(e))
        sys.exit(1)

    # 3. 生成 timestamp 和文件路径
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    snapshot_filename = "{0}.md".format(timestamp)
    snapshot_path = snapshots_dir / snapshot_filename
    relative_snapshot = ".claude/state/snapshots/{0}".format(snapshot_filename)

    # 4. 写空 snapshot 模板
    try:
        snapshot_path.write_text(
            SNAPSHOT_TEMPLATE.format(
                created_at=now.isoformat(timespec="seconds"),
                plan_status=plan_status,
                current_step=current_step or "(null)",
                timestamp=timestamp,
            ),
            encoding="utf-8",
        )
    except Exception as e:
        print("ERROR: 写 snapshot 文件失败: {0}".format(e))
        sys.exit(1)

    log("created snapshot: {0}".format(snapshot_path), HOOK_NAME)

    # 5. 原子改 plan.md frontmatter
    try:
        plan_text = plan_p.read_text(encoding="utf-8")
        plan_text = set_yaml_frontmatter_field(plan_text, "paused", "true")
        plan_text = set_yaml_frontmatter_field(
            plan_text, "paused_at", '"{0}"'.format(now.isoformat(timespec="seconds"))
        )
        plan_text = set_yaml_frontmatter_field(
            plan_text, "pause_snapshot", '"{0}"'.format(relative_snapshot)
        )
        plan_p.write_text(plan_text, encoding="utf-8")
    except Exception as e:
        print("ERROR: 改 plan.md frontmatter 失败: {0}".format(e))
        # snapshot 文件已写, 但 plan.md 没改 → 用户应手动修复或重跑
        print("snapshot 已创建: {0}".format(snapshot_path))
        print("请手动改 plan.md 的 paused / paused_at / pause_snapshot 字段.")
        sys.exit(1)

    log("updated plan.md frontmatter (paused=true)", HOOK_NAME)

    # 6. 输出引导
    print("=" * 60)
    print("/pauseAnalysis 第一阶段完成")
    print("=" * 60)
    print()
    print("**已创建空快照**: `{0}`".format(relative_snapshot))
    print()
    print("**已更新 plan.md frontmatter**:")
    print("  - paused: true")
    print("  - paused_at: {0}".format(now.isoformat(timespec="seconds")))
    print("  - pause_snapshot: {0}".format(relative_snapshot))
    print()
    print("---")
    print()
    print("**下一步 (AI 必须做)**:")
    print()
    print("1. **Read** 当前所有状态文件:")
    print("   - `.claude/state/plan.md`")
    print("   - `.claude/state/facts.md`")
    print("   - `.claude/state/dead_ends.md`")
    print("   - `.claude/state/environment.md`")
    print("   - `.claude/state/task.md` (复习目标)")
    print()
    print("2. **Read** 跨会话记忆 (memory_dir 下的所有 .md)")
    print()
    print("3. **Bash** 看 git log: `git log --oneline -20`")
    print()
    print("4. **Edit / Write** 暂停快照 `{0}`, 填完所有章节:".format(relative_snapshot))
    print("   - 任务摘要")
    print("   - 当前 step 状态 (已完成 / 剩余 / 下一动作)")
    print("   - 关键 facts (cite F<id>)")
    print("   - 已证伪方向")
    print("   - 当前环境状态")
    print("   - 未解决的问题")
    print("   - 下次恢复关键上下文")
    print("   - 阻塞 / 等待")
    print()
    print(
        "5. **写 memory**: 把快照里的关键发现作为 project / reference 类型 memory 存到"
    )
    print("   memory 目录, 跨会话保留. 不写 user / feedback 类型 (那是用户行为相关).")
    print()
    print(
        "6. **汇报**: 简短告诉用户 - 快照路径 / memory 数量 / 下次 /resumeAnalysis 即恢复."
    )
    print()
    print("---")
    print()
    print(
        "**Stop hook L8 状态**: paused=true, 你现在可以在回复中说 '休息 / 暂停 / 改天再' 等词,"
    )
    print("L8 不会再 block (因为 plan.md paused 已设置).")
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
