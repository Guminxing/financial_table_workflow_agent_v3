"""命令行入口：运行 Remediation / Repair Loop（第五阶段）。

用法::

    python src/run_repair.py \
      --panel_path outputs/prepared/prepared_panel.csv \
      --validation_report_path outputs/validation/validation_report.json \
      --data_dictionary_path outputs/prepared/data_dictionary.json \
      --approved_features_path outputs/validation/approved_feature_columns.json \
      --output_dir outputs/repaired

行为：
1. 读取 prepared_panel.csv 与 validation_report.json 等。
2. 根据 Critic 的 failed 项生成修复方案并执行。
3. 输出 repair_plan.json / repaired_panel.csv / repair_log.json / repair_report.md。
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

from repair import RepairLoop  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the Remediation / Repair Loop.")
    p.add_argument(
        "--panel_path",
        default="outputs/prepared/prepared_panel.csv",
        help="Path to prepared_panel.csv",
    )
    p.add_argument(
        "--validation_report_path",
        default="outputs/validation/validation_report.json",
        help="Path to validation_report.json from the Critic",
    )
    p.add_argument(
        "--data_dictionary_path",
        default="outputs/prepared/data_dictionary.json",
        help="Path to data_dictionary.json",
    )
    p.add_argument(
        "--approved_features_path",
        default="outputs/validation/approved_feature_columns.json",
        help="Path to approved_feature_columns.json",
    )
    p.add_argument(
        "--output_dir",
        default="outputs/repaired",
        help="Directory to write repaired outputs",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)

    for label, path in [
        ("panel", args.panel_path),
        ("validation_report", args.validation_report_path),
        ("data_dictionary", args.data_dictionary_path),
        ("approved_features", args.approved_features_path),
    ]:
        if not Path(path).exists():
            print(f"[run_repair] ERROR: {label} not found: {path}", file=sys.stderr)
            return 1

    loop = RepairLoop()
    loop.load_inputs(
        panel_path=args.panel_path,
        validation_report_path=args.validation_report_path,
        data_dictionary_path=args.data_dictionary_path,
        approved_features_path=args.approved_features_path,
    )
    plan = loop.build_repair_plan()
    result = loop.apply_repairs(plan)
    paths = loop.save_outputs(result, output_dir)
    report_path = loop.save_report(result, output_dir)

    log = result["repair_log"]
    print("[run_repair] done.")
    print(f"  rows before: {log['rows_before']}")
    print(f"  rows after: {log['rows_after']}")
    print(f"  rows removed: {log['rows_removed']}")
    print(f"  output panel: {paths['panel']}")
    print(f"  report path: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
