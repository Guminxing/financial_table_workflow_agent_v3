"""命令行入口：抓取真实 A 股数据并可选运行流水线（第八阶段）。

使用本项目内置 A 股数据源，输出约定的五张 CSV + fetch_metadata.json；
可选 --run_pipeline 直接运行完整流水线。

用法（仅抓取）::

    python src/run_fetch_real_data.py --tickers 600519,000001,300750 \
        --start_date 2024-01-01 --end_date 2024-06-30 \
        --output_dir data/real_market

抓取并直接运行完整流水线::

    python src/run_fetch_real_data.py --tickers 600519,000001 \
        --start_date 2024-01-01 --end_date 2024-06-30 \
        --output_dir data/real_market \
        --run_pipeline --output_root outputs_real

行为：
1. 调项目内部 real_data_adapter.fetch_real_data 抓取五张 CSV + fetch_metadata.json。
3. 若 metadata.errors 非空或 price.csv 为空（全部 ticker 抓取失败）→ 打印错误、返回 1，
   不运行后续流水线。部分失败时继续处理成功 ticker，metadata 记录失败项。
4. 若 --run_pipeline 且抓取有成功结果：调 PipelineRunner 运行完整流水线。
5. 退出码：metadata.errors 非空或 price.csv 为空或任一阶段 failed → 1，否则 0。

设计原则：
- 不依赖其他 Agent 项目；缓存默认写到 ``output_dir/cache``。
- 不用随机/样例/人工数据冒充真实行情；不把当前基本面快照回填到历史日期。
- 网络/数据源错误必须记录到 metadata。
- 路径用 pathlib，兼容 Windows，不写死绝对路径。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本无论从哪里调用都能 import 同级模块
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from real_data_adapter import (  # noqa: E402
    RealDataFetchConfig,
    fetch_real_data,
)
from pipeline_runner import PipelineRunner  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fetch real A-share data with the project-owned provider and optionally run the pipeline."
    )
    p.add_argument(
        "--tickers",
        required=True,
        help="Comma-separated A-share tickers (e.g. 600519,000001,300750).",
    )
    p.add_argument(
        "--start_date",
        required=True,
        help="Start date YYYY-MM-DD (inclusive).",
    )
    p.add_argument(
        "--end_date",
        required=True,
        help="End date YYYY-MM-DD (inclusive).",
    )
    p.add_argument(
        "--output_dir",
        default="data/real_market",
        help="Directory to write the 5 CSVs + fetch_metadata.json (default: data/real_market).",
    )
    p.add_argument(
        "--cache_dir",
        default=None,
        help="OHLCV cache directory (default: <output_dir>/cache).",
    )
    p.add_argument(
        "--no_snapshot_fundamentals",
        action="store_true",
        help="Do not fetch current PE/PB/ROE snapshot; fundamentals.csv will be header-only.",
    )
    p.add_argument(
        "--run_pipeline",
        action="store_true",
        help="After fetching, run the full pipeline on the fetched data.",
    )
    p.add_argument(
        "--output_root",
        default="outputs_real",
        help="Output root for the pipeline (default: outputs_real).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        print("[run_fetch_real_data] ERROR: no tickers parsed from --tickers", file=sys.stderr)
        return 1

    config = RealDataFetchConfig(
        tickers=tickers,
        start_date=args.start_date,
        end_date=args.end_date,
        output_dir=args.output_dir,
        cache_dir=args.cache_dir,
        snapshot_fundamentals=not args.no_snapshot_fundamentals,
    )

    print(
        f"[run_fetch_real_data] fetching {len(tickers)} ticker(s) "
        f"{args.start_date} ~ {args.end_date} -> {args.output_dir}"
    )
    metadata = fetch_real_data(config)

    # 打印摘要
    print()
    print("[run_fetch_real_data] fetch summary:")
    print(f"  resolved_tickers: {metadata.get('resolved_tickers')}")
    print(f"  rows_by_ticker:   {metadata.get('rows_by_ticker')}")
    print(f"  ohlcv_source:      {metadata.get('ohlcv_source_by_ticker')}")
    sr = metadata.get("summary_rows", {})
    print(
        f"  summary_rows:     price={sr.get('price')} volume={sr.get('volume')} "
        f"fundamentals={sr.get('fundamentals')} industry={sr.get('industry')} "
        f"calendar={sr.get('calendar')}"
    )
    if metadata.get("errors"):
        print(f"  errors:           {metadata.get('errors')}")
    if metadata.get("warnings"):
        print(f"  warnings:         {metadata.get('warnings')}")
    print(f"  metadata:         {args.output_dir}/fetch_metadata.json")

    # 全部 ticker 抓取失败或 price.csv 为空 → 不运行流水线
    price_empty = sr.get("price", 0) == 0
    if metadata.get("errors") or price_empty:
        print(
            "[run_fetch_real_data] ERROR: fetch produced no usable price data "
            "(all tickers failed or price.csv empty); not running pipeline.",
            file=sys.stderr,
        )
        return 1

    if args.run_pipeline:
        print()
        print("[run_fetch_real_data] running full pipeline on fetched data ...")
        runner = PipelineRunner(
            input_dir=args.output_dir,
            output_root=args.output_root,
            auto_repair=True,
        )
        runner.run_full_pipeline()
        session_log = runner.save_session_log()
        runner.print_dashboard()
        print()
        print(f"[run_fetch_real_data] session log: {session_log}")

        failed = [s for s, rec in runner.stages.items() if rec["status"] == "failed"]
        if failed:
            print(
                f"[run_fetch_real_data] pipeline completed with failed stages: {failed}",
                file=sys.stderr,
            )
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
