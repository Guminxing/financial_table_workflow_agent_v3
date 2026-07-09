"""命令行入口：运行 Validity Critic（第四阶段）。

用法::

    python src/run_critic.py \
      --panel_path outputs/prepared/prepared_panel.csv \
      --data_dictionary_path outputs/prepared/data_dictionary.json \
      --execution_log_path outputs/prepared/execution_log.json \
      --plan_path outputs/plans/workflow_plan.json \
      --executor_source_path src/executor.py \
      --calendar_path data/sample/calendar.csv \
      --output_dir outputs/validation

行为：
1. 读取 prepared_panel.csv、data_dictionary.json、execution_log.json、workflow_plan.json、executor.py、calendar.csv。
2. 运行 15 项有效性检查。
3. 输出 validation_report.json / validation_report.md / approved_feature_columns.json。
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

from critic import ValidityCritic  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the Validity Critic Agent.")
    p.add_argument(
        "--panel_path",
        default="outputs/prepared/prepared_panel.csv",
        help="Path to prepared_panel.csv",
    )
    p.add_argument(
        "--data_dictionary_path",
        default="outputs/prepared/data_dictionary.json",
        help="Path to data_dictionary.json",
    )
    p.add_argument(
        "--execution_log_path",
        default="outputs/prepared/execution_log.json",
        help="Path to execution_log.json",
    )
    p.add_argument(
        "--plan_path",
        default="outputs/plans/workflow_plan.json",
        help="Path to workflow_plan.json",
    )
    p.add_argument(
        "--executor_source_path",
        default="src/executor.py",
        help="Path to executor.py source (for static checks)",
    )
    p.add_argument(
        "--calendar_path",
        default="data/sample/calendar.csv",
        help="Path to calendar.csv (for trading-day alignment check)",
    )
    p.add_argument(
        "--output_dir",
        default="outputs/validation",
        help="Directory to write validation outputs",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)

    # 基本存在性检查
    for label, path in [
        ("panel", args.panel_path),
        ("data_dictionary", args.data_dictionary_path),
        ("execution_log", args.execution_log_path),
        ("plan", args.plan_path),
        ("executor_source", args.executor_source_path),
    ]:
        if not Path(path).exists():
            print(
                f"[run_critic] ERROR: {label} not found: {path}",
                file=sys.stderr,
            )
            return 1

    critic = ValidityCritic()
    critic.load_inputs(
        panel_path=args.panel_path,
        data_dictionary_path=args.data_dictionary_path,
        execution_log_path=args.execution_log_path,
        plan_path=args.plan_path,
        executor_source_path=args.executor_source_path,
        calendar_path=args.calendar_path,
    )
    report = critic.run_all_checks()

    json_path = output_dir / "validation_report.json"
    md_path = output_dir / "validation_report.md"
    approved_path = output_dir / "approved_feature_columns.json"
    critic.save_json_report(report, json_path)
    critic.save_markdown_report(report, md_path)
    critic.save_approved_feature_columns(report, approved_path)

    s = report["summary"]
    print("[run_critic] done.")
    print(f"  overall status: {report['overall_status']}")
    print(f"  total checks: {s['total_checks']}")
    print(f"  passed: {s['passed']}")
    print(f"  warnings: {s['warnings']}")
    print(f"  failed: {s['failed']}")
    print(f"  report path: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
