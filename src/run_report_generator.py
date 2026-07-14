"""命令行入口：运行 Final Report Generator（第六阶段）。

用法::

    python src/run_report_generator.py \
      --profile_json outputs_real/profiles/profile.json \
      --workflow_plan_json outputs_real/plans/workflow_plan.json \
      --prepared_panel outputs_real/prepared/prepared_panel.csv \
      --execution_log outputs_real/prepared/execution_log.json \
      --initial_validation_report outputs_real/validation/validation_report.json \
      --repair_plan outputs_real/repaired/repair_plan.json \
      --repair_log outputs_real/repaired/repair_log.json \
      --repaired_panel outputs_real/repaired/repaired_panel.csv \
      --final_validation_report outputs_real/validation_repaired/validation_report.json \
      --approved_features outputs_real/validation_repaired/approved_feature_columns.json \
      --data_dictionary outputs_real/prepared/data_dictionary.json \
      --output_dir outputs_real/final_report

行为：
1. 只读前五阶段产物。
2. 汇总成 final_workflow_summary.json / final_workflow_report.md /
   final_workflow_one_page.md / pipeline_artifacts_index.json。
3. 终端打印输出路径与闭环摘要。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本无论从哪里调用都能 import 同级模块
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from report_generator import ReportGenerator  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the Final Report Generator.")
    p.add_argument("--profile_json", default="outputs_real/profiles/profile.json")
    p.add_argument("--workflow_plan_json", default="outputs_real/plans/workflow_plan.json")
    p.add_argument("--prepared_panel", default="outputs_real/prepared/prepared_panel.csv")
    p.add_argument("--execution_log", default="outputs_real/prepared/execution_log.json")
    p.add_argument(
        "--initial_validation_report",
        default="outputs_real/validation/validation_report.json",
    )
    p.add_argument("--repair_plan", default="outputs_real/repaired/repair_plan.json")
    p.add_argument("--repair_log", default="outputs_real/repaired/repair_log.json")
    p.add_argument("--repaired_panel", default="outputs_real/repaired/repaired_panel.csv")
    p.add_argument(
        "--final_validation_report",
        default="outputs_real/validation_repaired/validation_report.json",
    )
    p.add_argument(
        "--approved_features",
        default="outputs_real/validation_repaired/approved_feature_columns.json",
    )
    p.add_argument("--data_dictionary", default="outputs_real/prepared/data_dictionary.json")
    p.add_argument("--output_dir", default="outputs_real/final_report")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)

    inputs = [
        ("profile_json", args.profile_json),
        ("workflow_plan_json", args.workflow_plan_json),
        ("prepared_panel", args.prepared_panel),
        ("execution_log", args.execution_log),
        ("initial_validation_report", args.initial_validation_report),
        ("repair_plan", args.repair_plan),
        ("repair_log", args.repair_log),
        ("repaired_panel", args.repaired_panel),
        ("final_validation_report", args.final_validation_report),
        ("approved_features", args.approved_features),
        ("data_dictionary", args.data_dictionary),
    ]
    for label, path in inputs:
        if not Path(path).exists():
            print(f"[run_report_generator] ERROR: {label} not found: {path}", file=sys.stderr)
            return 1

    gen = ReportGenerator()
    gen.load_inputs(
        profile_json=args.profile_json,
        workflow_plan_json=args.workflow_plan_json,
        prepared_panel=args.prepared_panel,
        execution_log=args.execution_log,
        initial_validation_report=args.initial_validation_report,
        repair_plan=args.repair_plan,
        repair_log=args.repair_log,
        repaired_panel=args.repaired_panel,
        final_validation_report=args.final_validation_report,
        approved_features=args.approved_features,
        data_dictionary=args.data_dictionary,
    )
    paths = gen.save_all(output_dir)

    summary = gen.build_summary()
    cl = summary["closed_loop_result"]
    print("[run_report_generator] done.")
    print(f"  summary:        {paths['summary']}")
    print(f"  full report:    {paths['full_report']}")
    print(f"  one-page:       {paths['one_page']}")
    print(f"  artifacts index:{paths['index']}")
    print(f"  initial status: {summary['initial_validation_status']}")
    print(f"  final status:   {summary['final_validation_status']}")
    print(f"  rows removed:   {summary['rows_removed_by_repair']}")
    print(f"  closed loop:    {cl['one_line']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
