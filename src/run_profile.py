"""命令行入口：运行 Data Profiler。

用法::

    python src/run_profile.py --input_dir data/real_market --output_dir outputs_real/profiles

行为：
1. 读取 input_dir 下所有 CSV 并剖析。
2. 输出 profile.json 与 profile_report.md。
3. 终端打印简短摘要。

v3：不再自动生成合成样例数据。input_dir 不存在或无 CSV 时明确失败。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本无论从哪里调用都能 import 同级模块
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from profiler import FinancialTableProfiler  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the financial table Data Profiler.")
    p.add_argument(
        "--input_dir",
        default="data/real_market",
        help="Directory containing input CSV files (default: data/real_market)",
    )
    p.add_argument(
        "--output_dir",
        default="outputs_real/profiles",
        help="Directory to write profile.json and profile_report.md (default: outputs_real/profiles)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    if not input_dir.exists():
        print(
            f"[run_profile] ERROR: input_dir does not exist: {input_dir}. "
            "Download real market data first (see run_fetch_real_data.py).",
            file=sys.stderr,
        )
        return 1

    csv_files = sorted(input_dir.glob("*.csv"))
    if not csv_files:
        print(
            f"[run_profile] ERROR: no CSV files in {input_dir}. "
            "Download real market data first; synthetic sample generation has been removed in v3.",
            file=sys.stderr,
        )
        return 1

    # 剖析
    profiler = FinancialTableProfiler(input_dir)
    profile = profiler.run()

    # 3. 落盘
    json_path = output_dir / "profile.json"
    md_path = output_dir / "profile_report.md"
    profiler.save_json(profile, json_path)
    profiler.save_markdown(profile, md_path)

    # 4. 摘要
    n_tables = len(profile["tables"])
    total_issues = sum(len(t["potential_issues"]) for t in profile["tables"])
    total_issues += len(profile["cross_table_findings"]["global_potential_issues"])

    print("[run_profile] done.")
    print(f"  processed tables: {n_tables}")
    print(f"  total issues found: {total_issues}")
    print(f"  output path: {json_path}")
    print(f"  report path: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
