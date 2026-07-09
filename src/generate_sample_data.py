"""生成模拟金融表格数据。

本脚本生成 5 张 CSV，保存到 data/sample/，用于验证 Data Profiler。
故意注入若干"脏数据"特征（缺失值、重复 key、字段口径不一致、公告滞后等），
以便 profiler 能检测出问题。

设计要点：
- 设置随机种子，保证可复现。
- 不依赖外部网络。
- trade_date / date 使用字符串日期，模拟真实业务系统常见的"日期当字符串存"。
- price.csv 与 volume.csv 的日期列、证券代码列故意命名不一致，
  用于触发跨表 schema 不一致检测。
- fundamentals.csv 同时给出 report_date 和 announce_date，
  为后续 look-ahead bias 检查做铺垫。
"""

from __future__ import annotations

import random
from pathlib import Path

import numpy as np
import pandas as pd

# 随机种子，保证每次运行结果一致
RANDOM_SEED = 42

# 5 只标的（股票/ETF 混合），用于 price / volume / fundamentals / industry
TICKERS = ["000001", "600000", "AAPL", "510300", "000333"]

# 60 个交易日（字符串日期 YYYY-MM-DD），从 2024-01-02 开始的工作日
START_DATE = "2024-01-02"
N_TRADING_DAYS = 60


def _trading_day_strings(start: str, n: int) -> list[str]:
    """生成 n 个连续工作日（周一到周五）的字符串日期。"""
    dates = pd.bdate_range(start=start, periods=n)
    return [d.strftime("%Y-%m-%d") for d in dates]


def _gen_price(trading_days: list[str], rng: np.random.Generator) -> pd.DataFrame:
    """生成 price.csv：OHLC 行情，故意加入缺失值与重复 ticker-date。"""
    rows = []
    for ticker in TICKERS:
        # 每只标的不同起始价，模拟真实价格水平差异
        base = rng.uniform(5, 150)
        price_path = [base]
        for _ in range(1, len(trading_days)):
            # 简单随机游走，保证价格为正
            step = rng.normal(0, 0.02) * price_path[-1]
            price_path.append(max(price_path[-1] + step, 0.5))
        for i, d in enumerate(trading_days):
            close = round(price_path[i], 4)
            op = round(close * rng.uniform(0.98, 1.02), 4)
            hi = round(max(op, close) * rng.uniform(1.0, 1.03), 4)
            lo = round(min(op, close) * rng.uniform(0.97, 1.0), 4)
            rows.append(
                {
                    "trade_date": d,
                    "ticker": ticker,
                    "open": op,
                    "high": hi,
                    "low": lo,
                    "close": close,
                }
            )

    df = pd.DataFrame(rows)

    # 故意注入缺失值（约 1%）
    for col in ["open", "high", "low", "close"]:
        mask = rng.random(len(df)) < 0.01
        df.loc[mask, col] = np.nan

    # 故意注入 1-2 行重复 (trade_date, ticker)
    dup = df.sample(n=2, random_state=rng.integers(0, 10**9))
    df = pd.concat([df, dup], ignore_index=True)

    # 打乱顺序，模拟真实落库顺序
    df = df.sample(frac=1.0, random_state=rng.integers(0, 10**9)).reset_index(drop=True)
    return df


def _gen_volume(trading_days: list[str], rng: np.random.Generator) -> pd.DataFrame:
    """生成 volume.csv：成交数据，字段名与 price.csv 不一致，且部分日期缺失。"""
    rows = []
    for ticker in TICKERS:
        for d in trading_days:
            vol = int(rng.integers(100_000, 10_000_000))
            turnover = round(vol * rng.uniform(5, 150), 2)
            rows.append(
                {
                    "date": d,
                    "stock_code": ticker,
                    "volume": vol,
                    "turnover": turnover,
                }
            )
    df = pd.DataFrame(rows)

    # 故意注入缺失值
    for col in ["volume", "turnover"]:
        mask = rng.random(len(df)) < 0.01
        df.loc[mask, col] = np.nan

    # 故意让部分 (date, stock_code) 在 price 中存在但 volume 中不存在：
    # 随机删掉若干行
    drop_idx = rng.choice(len(df), size=5, replace=False)
    df = df.drop(index=drop_idx).reset_index(drop=True)

    # 打乱顺序
    df = df.sample(frac=1.0, random_state=rng.integers(0, 10**9)).reset_index(drop=True)
    return df


def _gen_fundamentals(rng: np.random.Generator) -> pd.DataFrame:
    """生成 fundamentals.csv：财务数据，体现公告滞后（announce_date 晚于 report_date）。"""
    rows = []
    # 每只标的 4 个季度报告
    quarters = ["2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30"]
    for ticker in TICKERS:
        for q in quarters:
            report_date = q
            # 公告日通常滞后 1~3 个月
            announce_offset = rng.integers(30, 95)
            announce_date = (
                pd.Timestamp(report_date) + pd.Timedelta(days=int(announce_offset))
            ).strftime("%Y-%m-%d")
            rows.append(
                {
                    "report_date": report_date,
                    "announce_date": announce_date,
                    "ticker": ticker,
                    "pe": round(rng.uniform(5, 60), 2),
                    "pb": round(rng.uniform(0.5, 8), 2),
                    "roe": round(rng.uniform(-5, 30), 2),
                }
            )
    df = pd.DataFrame(rows)

    # pe/pb/roe 少量缺失
    for col in ["pe", "pb", "roe"]:
        mask = rng.random(len(df)) < 0.05
        df.loc[mask, col] = np.nan

    # 故意制造 1 条 pe 为负的异常值（亏损公司）
    df.loc[df.index[0], "pe"] = -12.5
    return df


def _gen_industry() -> pd.DataFrame:
    """生成 industry.csv：行业映射，故意让一个 ticker 行业缺失/拼写异常。"""
    industry_map = {
        "000001": "银行",
        "600000": "银行",
        "AAPL": "信息技术",
        "510300": "指数基金",
        "000333": "家电",
    }
    df = pd.DataFrame(
        [{"ticker": t, "industry_name": v} for t, v in industry_map.items()]
    )
    # 故意让一个 ticker 行业拼写异常
    df.loc[df["ticker"] == "000333", "industry_name"] = "家电 "
    # 故意让一个 ticker 行业缺失
    df.loc[df["ticker"] == "510300", "industry_name"] = np.nan
    return df


def _gen_calendar(trading_days: list[str], rng: np.random.Generator) -> pd.DataFrame:
    """生成 calendar.csv：交易日历，含少量非交易日用于后续对齐。"""
    # 取交易日前后一段范围，标记是否交易日
    start = pd.Timestamp(START_DATE)
    end = pd.Timestamp(trading_days[-1]) + pd.Timedelta(days=10)
    all_days = pd.date_range(start=start, end=end, freq="D")
    trading_set = {pd.Timestamp(d) for d in trading_days}

    rows = []
    for d in all_days:
        rows.append(
            {
                "date": d.strftime("%Y-%m-%d"),
                "is_trading_day": 1 if d in trading_set else 0,
            }
        )
    df = pd.DataFrame(rows)
    # 打乱顺序
    df = df.sample(frac=1.0, random_state=rng.integers(0, 10**9)).reset_index(drop=True)
    return df


def generate_sample_data(output_dir: Path) -> list[Path]:
    """生成全部 5 张 CSV，返回写入的文件路径列表。"""
    rng = np.random.default_rng(RANDOM_SEED)
    random.seed(RANDOM_SEED)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    trading_days = _trading_day_strings(START_DATE, N_TRADING_DAYS)

    tables = {
        "price.csv": _gen_price(trading_days, rng),
        "volume.csv": _gen_volume(trading_days, rng),
        "fundamentals.csv": _gen_fundamentals(rng),
        "industry.csv": _gen_industry(),
        "calendar.csv": _gen_calendar(trading_days, rng),
    }

    written = []
    for name, df in tables.items():
        path = output_dir / name
        df.to_csv(path, index=False, encoding="utf-8-sig")
        written.append(path)
    return written


def main() -> None:
    # 默认输出到与本脚本同级的 ../data/sample
    here = Path(__file__).resolve().parent
    out = here.parent / "data" / "sample"
    files = generate_sample_data(out)
    print(f"[generate_sample_data] wrote {len(files)} files to {out}")
    for f in files:
        print(f"  - {f.name}: {sum(1 for _ in open(f, encoding='utf-8-sig')) - 1} rows")


if __name__ == "__main__":
    main()
