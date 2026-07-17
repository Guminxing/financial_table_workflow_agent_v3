"""项目内置的真实 A 股数据适配器（第八阶段）。

通过本项目 ``data_sources.astock`` 直接访问真实行情接口，输出约定的五张 CSV：

    price.csv         trade_date, ticker, open, high, low, close
    volume.csv        date, stock_code, volume, turnover
    fundamentals.csv  report_date, announce_date, ticker, pe, pb, roe
    industry.csv      ticker, industry_name
    calendar.csv      date, is_trading_day
    fetch_metadata.json

设计原则：
- 数据获取实现属于本项目，不动态导入或调用其他 Agent 项目。
- OHLCV 必须来自真实网络行情接口或当前 run 的隔离缓存；
  严格限制在 start_date ~ end_date；按 (date, ticker) 去重并排序；
  open/high/low/close/volume 转为数值；不允许用随机数/样例/前值填充伪造。
- 基本面时间点约束：数据接口返回的 PE/PB/ROE 是当前快照，不是历史 point-in-time
  数据库。当前快照的 announce_date 必须用真实抓取日期，不得用用户指定的
  历史 end_date 回填，不得复制到整个历史区间。无法获得可信历史基本面时
  输出只有表头的 fundamentals.csv，并在 metadata 中明确说明。
- 网络与数据源错误必须记录到 metadata，不静默吞掉。
- 路径用 pathlib，兼容 Windows，不写死绝对路径；新增代码带类型注解。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from data_sources.astock import AStockDataSource, DATA_SOURCE_VERSION, normalize_ticker

ADAPTER_VERSION = "0.2"

# 五张输出表的固定列契约
PRICE_COLUMNS = ["trade_date", "ticker", "open", "high", "low", "close"]
VOLUME_COLUMNS = ["date", "stock_code", "volume", "turnover"]
FUNDAMENTALS_COLUMNS = [
    "report_date",
    "announce_date",
    "ticker",
    "pe",
    "pb",
    "roe",
]
INDUSTRY_COLUMNS = ["ticker", "industry_name"]
CALENDAR_COLUMNS = ["date", "is_trading_day"]


@dataclass
class RealDataFetchConfig:
    """真实数据抓取配置。

    Attributes:
        tickers: A 股代码列表（6 位，可带 SH/SZ 前缀或 .SH/.SZ 后缀）。
        start_date: 起始日期 YYYY-MM-DD（含）。
        end_date: 结束日期 YYYY-MM-DD（含）。
        output_dir: 五张 CSV 与 fetch_metadata.json 的输出目录。
        cache_dir: 本项目 OHLCV 缓存目录；None 时使用 ``output_dir/cache``。
        snapshot_fundamentals: 是否抓取当前基本面快照（PE/PB/ROE）。
            默认 True。设为 False 时 fundamentals.csv 只输出表头。
    """

    tickers: list[str]
    start_date: str
    end_date: str
    output_dir: str | Path
    cache_dir: str | Path | None = None
    snapshot_fundamentals: bool = True
    # 内部记录每个 ticker 的错误/警告（不暴露给用户构造）
    per_ticker_errors: dict[str, str] = field(default_factory=dict)
    per_ticker_warnings: dict[str, list[str]] = field(default_factory=dict)

def _fetch_fundamentals_snapshot(
    data_source: AStockDataSource,
    ticker: str,
) -> dict[str, Any]:
    """获取单只 ticker 的当前基本面快照（PE/PB）。

    腾讯 PE/PB/ROE 是当前快照，不是完整历史 point-in-time 数据库。
    返回 dict: {pe, pb, roe, name, price}（缺失字段为 None）。
    """
    try:
        return data_source.fetch_quote_snapshot(ticker)
    except Exception:  # noqa: BLE001
        return {"pe": None, "pb": None, "roe": None, "name": None, "price": None}


def _build_price(ohlcv_by_ticker: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """把每只 ticker 的 OHLCV 拼成 price.csv。"""
    rows: list[pd.DataFrame] = []
    for ticker, df in ohlcv_by_ticker.items():
        if df.empty:
            continue
        sub = pd.DataFrame(
            {
                "trade_date": df["Date"].dt.strftime("%Y-%m-%d"),
                "ticker": ticker,
                "open": df["Open"].values,
                "high": df["High"].values,
                "low": df["Low"].values,
                "close": df["Close"].values,
            }
        )
        rows.append(sub)
    if not rows:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    price = pd.concat(rows, ignore_index=True)
    # 全局去重 + 排序
    price = (
        price.drop_duplicates(subset=["trade_date", "ticker"], keep="last")
        .sort_values(["trade_date", "ticker"])
        .reset_index(drop=True)
    )
    return price[PRICE_COLUMNS]


def _build_volume(ohlcv_by_ticker: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """volume.csv 必须来自真实 OHLCV 的 Volume 列。turnover 无可靠来源时留空。"""
    rows: list[pd.DataFrame] = []
    for ticker, df in ohlcv_by_ticker.items():
        if df.empty:
            continue
        sub = pd.DataFrame(
            {
                "date": df["Date"].dt.strftime("%Y-%m-%d"),
                "stock_code": ticker,
                "volume": df["Volume"].values,
                # 当前 OHLCV 接口输出不含成交额；不伪造，留空
                "turnover": pd.NA,
            }
        )
        rows.append(sub)
    if not rows:
        return pd.DataFrame(columns=VOLUME_COLUMNS)
    vol = pd.concat(rows, ignore_index=True)
    vol = (
        vol.drop_duplicates(subset=["date", "stock_code"], keep="last")
        .sort_values(["date", "stock_code"])
        .reset_index(drop=True)
    )
    return vol[VOLUME_COLUMNS]


def _build_calendar(
    ohlcv_by_ticker: dict[str, pd.DataFrame],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """calendar.csv：覆盖请求日期区间，根据真实行情日期标记交易日。

    有行情的日期标记 is_trading_day=1，区间内其余日期标记 0。
    不简单把周一至周五都当交易日。
    """
    start_dt = pd.to_datetime(start_date).normalize()
    end_dt = pd.to_datetime(end_date).normalize()
    all_days = pd.date_range(start=start_dt, end=end_dt, freq="D")

    real_trading_days: set[pd.Timestamp] = set()
    for df in ohlcv_by_ticker.values():
        if df.empty:
            continue
        for d in df["Date"].dropna().dt.normalize().tolist():
            real_trading_days.add(d)

    cal = pd.DataFrame(
        {
            "date": [d.strftime("%Y-%m-%d") for d in all_days],
            "is_trading_day": [1 if d in real_trading_days else 0 for d in all_days],
        }
    )
    return cal[CALENDAR_COLUMNS]


def _build_fundamentals(
    config: RealDataFetchConfig,
    data_source: AStockDataSource,
    fetch_date_str: str,
    tickers: list[str],
) -> tuple[pd.DataFrame, list[str]]:
    """构建 fundamentals.csv。

    严格遵守基本面时间点约束：
    - 腾讯 PE/PB/ROE 是当前快照，不是历史 point-in-time 数据库。
    - 当前快照的 announce_date 必须用真实抓取日期（fetch_date_str）。
    - 不得用用户指定的历史 end_date 作为 announce_date。
    - 不得把当前快照复制到整个历史区间。
    - snapshot_fundamentals=False 或抓取失败时，输出只有表头的 fundamentals.csv。
    返回 (df, warnings)。
    """
    warnings: list[str] = []
    if not config.snapshot_fundamentals:
        warnings.append(
            "snapshot_fundamentals=False: fundamentals.csv is header-only by request "
            "(--no_snapshot_fundamentals)."
        )
        return pd.DataFrame(columns=FUNDAMENTALS_COLUMNS), warnings

    rows: list[dict[str, Any]] = []
    for ticker in tickers:
        snap = _fetch_fundamentals_snapshot(data_source, ticker)
        if snap["pe"] is None and snap["pb"] is None and snap["roe"] is None:
            config.per_ticker_warnings.setdefault(ticker, []).append(
                "fundamentals snapshot unavailable (tencent quote failed or empty)"
            )
            continue
        rows.append(
            {
                # 当前快照没有 report_date 概念，留空
                "report_date": "",
                # announce_date 用真实抓取日期，不用历史 end_date
                "announce_date": fetch_date_str,
                "ticker": ticker,
                "pe": snap["pe"],
                "pb": snap["pb"],
                "roe": snap["roe"],
            }
        )

    if not rows:
        warnings.append(
            "No fundamentals snapshot could be fetched for any ticker; "
            "fundamentals.csv is header-only. This is expected when the network "
            "or quote source is unavailable; the pipeline will continue and emit "
            "a warning (not a failure)."
        )
        return pd.DataFrame(columns=FUNDAMENTALS_COLUMNS), warnings

    fund = pd.DataFrame(rows, columns=FUNDAMENTALS_COLUMNS)
    warnings.append(
        "fundamentals.csv contains CURRENT SNAPSHOT pe/pb/roe (announce_date = fetch date), "
        "NOT historical point-in-time fundamentals. Do NOT treat these as historical "
        "as-of values for dates before the fetch date; doing so would introduce look-ahead bias."
    )
    return fund, warnings


def _build_industry(
    config: RealDataFetchConfig,
    data_source: AStockDataSource,
    tickers: list[str],
) -> tuple[pd.DataFrame, list[str]]:
    """构建 industry.csv。

    优先获取真实行业信息；无法获取时用 unknown，并在 metadata 记录警告。
    不得因此中断 OHLCV 流程。
    """
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    got_any_real = False
    for ticker in tickers:
        industry_name: str | None = None
        try:
            industry_name = data_source.fetch_industry(ticker)
            if industry_name:
                got_any_real = True
        except Exception as exc:  # noqa: BLE001
            config.per_ticker_warnings.setdefault(ticker, []).append(
                f"industry fetch failed: {type(exc).__name__}: {exc}"
            )
        rows.append(
            {
                "ticker": ticker,
                "industry_name": industry_name if industry_name else "unknown",
            }
        )

    if not got_any_real:
        warnings.append(
            "Could not fetch real industry info for any ticker; "
            "industry_name set to 'unknown'. OHLCV pipeline is unaffected."
        )
    industry = pd.DataFrame(rows, columns=INDUSTRY_COLUMNS)
    return industry, warnings


def fetch_real_data(config: RealDataFetchConfig) -> dict[str, Any]:
    """真实数据抓取主入口。

    返回 fetch_metadata dict（同时落盘 fetch_metadata.json）。
    """
    out_dir = Path(config.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    # 默认缓存跟随本次输出目录，Agent 模式下即 run_root/raw_data/cache。
    if config.cache_dir:
        cache_dir = Path(config.cache_dir).resolve()
    else:
        cache_dir = (out_dir / "cache").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    data_source = AStockDataSource(cache_dir=cache_dir)

    fetch_date_str = date.today().strftime("%Y-%m-%d")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    resolved_tickers: list[str] = []
    ohlcv_by_ticker: dict[str, pd.DataFrame] = {}
    ohlcv_source_by_ticker: dict[str, str] = {}
    rows_by_ticker: dict[str, int] = {}

    for raw_ticker in config.tickers:
        try:
            norm = normalize_ticker(raw_ticker)
        except Exception as exc:  # noqa: BLE001
            config.per_ticker_errors[raw_ticker] = f"normalize failed: {type(exc).__name__}: {exc}"
            continue
        if norm in resolved_tickers:
            continue
        resolved_tickers.append(norm)
        try:
            df, source = data_source.fetch_ohlcv(
                norm, config.start_date, config.end_date
            )
            ohlcv_by_ticker[norm] = df
            ohlcv_source_by_ticker[norm] = source
            rows_by_ticker[norm] = int(len(df))
            if df.empty:
                config.per_ticker_warnings.setdefault(norm, []).append(
                    f"no OHLCV rows in [{config.start_date}, {config.end_date}] "
                    f"(source={source})"
                )
        except Exception as exc:  # noqa: BLE001
            config.per_ticker_errors[norm] = f"OHLCV fetch failed: {type(exc).__name__}: {exc}"
            ohlcv_by_ticker[norm] = pd.DataFrame(
                columns=["Date", "Open", "High", "Low", "Close", "Volume"]
            )
            rows_by_ticker[norm] = 0

    # 构建五张表
    price = _build_price(ohlcv_by_ticker)
    volume = _build_volume(ohlcv_by_ticker)
    calendar = _build_calendar(ohlcv_by_ticker, config.start_date, config.end_date)
    fundamentals, fund_warnings = _build_fundamentals(
        config, data_source, fetch_date_str, resolved_tickers
    )
    industry, ind_warnings = _build_industry(config, data_source, resolved_tickers)

    # 落盘
    price_path = out_dir / "price.csv"
    volume_path = out_dir / "volume.csv"
    fund_path = out_dir / "fundamentals.csv"
    industry_path = out_dir / "industry.csv"
    calendar_path = out_dir / "calendar.csv"

    price.to_csv(price_path, index=False, encoding="utf-8-sig")
    volume.to_csv(volume_path, index=False, encoding="utf-8-sig")
    fundamentals.to_csv(fund_path, index=False, encoding="utf-8-sig")
    industry.to_csv(industry_path, index=False, encoding="utf-8-sig")
    calendar.to_csv(calendar_path, index=False, encoding="utf-8-sig")

    all_warnings = list(fund_warnings) + list(ind_warnings)

    metadata: dict[str, Any] = {
        "project": "financial_table_workflow_agent",
        "adapter_version": ADAPTER_VERSION,
        "data_source_version": DATA_SOURCE_VERSION,
        "data_provider": "project_internal_astock_http",
        "volume_unit": "shares",
        "generated_at": generated_at,
        "fetch_date": fetch_date_str,
        "cache_dir": str(cache_dir).replace("\\", "/"),
        "requested_tickers": list(config.tickers),
        "resolved_tickers": resolved_tickers,
        "start_date": config.start_date,
        "end_date": config.end_date,
        "snapshot_fundamentals_enabled": bool(config.snapshot_fundamentals),
        "ohlcv_source_by_ticker": ohlcv_source_by_ticker,
        "rows_by_ticker": rows_by_ticker,
        "per_ticker_errors": {k: v for k, v in config.per_ticker_errors.items()},
        "per_ticker_warnings": {k: v for k, v in config.per_ticker_warnings.items()},
        "summary_rows": {
            "price": int(len(price)),
            "volume": int(len(volume)),
            "fundamentals": int(len(fundamentals)),
            "industry": int(len(industry)),
            "calendar": int(len(calendar)),
        },
        "output_files": {
            "price": str(price_path.resolve()).replace("\\", "/"),
            "volume": str(volume_path.resolve()).replace("\\", "/"),
            "fundamentals": str(fund_path.resolve()).replace("\\", "/"),
            "industry": str(industry_path.resolve()).replace("\\", "/"),
            "calendar": str(calendar_path.resolve()).replace("\\", "/"),
        },
        "fundamentals_limitation": (
            "fundamentals.csv contains CURRENT SNAPSHOT pe/pb/roe with announce_date = fetch "
            "date, NOT historical point-in-time fundamentals. Tencent PE/PB values are current "
            "snapshots, not a complete historical fundamentals database. Do NOT backfill "
            "these snapshots into historical dates; that would fabricate announce_date and "
            "introduce look-ahead bias. When snapshot_fundamentals=False or the quote source "
            "is unavailable, fundamentals.csv is header-only and the pipeline continues with "
            "a warning (not a failure)."
        ),
        "warnings": all_warnings,
        "errors": [f"{k}: {v}" for k, v in config.per_ticker_errors.items()],
    }

    meta_path = out_dir / "fetch_metadata.json"
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    return metadata


if __name__ == "__main__":
    # 直接运行时做最小自检：需要传参，否则打印用法
    print("real_data_adapter.py — import and call fetch_real_data(config) to use.")
