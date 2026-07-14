"""命令行入口：运行 Workflow Planner（第二阶段）。

用法::

    python src/run_planner.py --profile_path outputs_real/profiles/profile.json --output_dir outputs_real/plans

可选::

    python src/run_planner.py --profile_path outputs_real/profiles/profile.json --output_dir outputs_real/plans --analysis_goal "..."

行为：
1. 读取 profile.json。
2. 若未提供 analysis_goal，使用默认目标。
3. 生成 workflow_plan.json 与 workflow_plan_report.md。
4. 终端打印简短摘要。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本无论从哪里调用都能 import 同级模块
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from planner import DEFAULT_ANALYSIS_GOAL, WorkflowPlanner  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the Workflow Planner Agent.")
    p.add_argument(
        "--profile_path",
        default="outputs_real/profiles/profile.json",
        help="Path to profile.json (default: outputs_real/profiles/profile.json)",
    )
    p.add_argument(
        "--output_dir",
        default="outputs_real/plans",
        help="Directory to write workflow_plan.json and workflow_plan_report.md (default: outputs_real/plans)",
    )
    p.add_argument(
        "--analysis_goal",
        default=None,
        help="Downstream analysis goal. If omitted, the default 5d-return / factor-analysis goal is used.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    profile_path = Path(args.profile_path)
    output_dir = Path(args.output_dir)
    analysis_goal = args.analysis_goal or DEFAULT_ANALYSIS_GOAL

    if not profile_path.exists():
        print(
            f"[run_planner] ERROR: profile not found: {profile_path}. "
            "Run run_profile.py first.",
            file=sys.stderr,
        )
        return 1

    planner = WorkflowPlanner()
    profile = planner.load_profile(profile_path)
    plan = planner.build_plan(profile, analysis_goal)
    # 回填输入路径，便于追溯
    plan["input_profile_path"] = str(profile_path).replace("\\", "/")

    json_path = output_dir / "workflow_plan.json"
    md_path = output_dir / "workflow_plan_report.md"
    planner.save_plan(plan, json_path)
    planner.save_markdown_report(plan, md_path)

    n_steps = len(plan["workflow_steps"])
    n_checks = len(plan["validation_plan"]["checks"])

    print("[run_planner] done.")
    print(f"  workflow steps: {n_steps}")
    print(f"  validation checks: {n_checks}")
    print(f"  output path: {json_path}")
    print(f"  report path: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
