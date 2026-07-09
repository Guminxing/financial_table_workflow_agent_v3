"""命令行入口：运行 Code Executor（第三阶段）。

用法::

    python src/run_executor.py --input_dir data/sample --plan_path outputs/plans/workflow_plan.json --output_dir outputs/prepared

行为：
1. 读取 workflow_plan.json 与原始 CSV。
2. 按 plan 执行数据处理，生成 prepared_panel.csv。
3. 输出 prepared_panel.csv / data_dictionary.json / execution_log.json / execution_report.md。
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

from executor import CodeExecutor  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the Code Executor Agent.")
    p.add_argument(
        "--input_dir",
        default="data/sample",
        help="Directory containing raw CSV files (default: data/sample)",
    )
    p.add_argument(
        "--plan_path",
        default="outputs/plans/workflow_plan.json",
        help="Path to workflow_plan.json (default: outputs/plans/workflow_plan.json)",
    )
    p.add_argument(
        "--output_dir",
        default="outputs/prepared",
        help="Directory to write prepared outputs (default: outputs/prepared)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir)
    plan_path = Path(args.plan_path)
    output_dir = Path(args.output_dir)

    if not plan_path.exists():
        print(
            f"[run_executor] ERROR: plan not found: {plan_path}. "
            "Run run_planner.py first.",
            file=sys.stderr,
        )
        return 1

    ex = CodeExecutor()
    plan = ex.load_workflow_plan(plan_path)
    result = ex.execute(plan, input_dir)
    paths = ex.save_outputs(result, output_dir)
    report_path = ex.save_execution_report(result, output_dir)

    panel = result["panel"]
    pk_unique = not panel.duplicated(subset=["date", "ticker"]).any()

    print("[run_executor] done.")
    print(f"  output table: {paths['panel']}")
    print(f"  rows: {len(panel)}")
    print(f"  columns: {panel.shape[1]}")
    print(f"  primary key unique: {pk_unique}")
    print(f"  report path: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
