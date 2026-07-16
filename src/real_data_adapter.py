"""真实 A 股数据适配器（第八阶段）。

把参考项目 TradingAgents-astock-main 的真实行情获取能力接入当前
financial_table_workflow_agent，输出本项目约定的五张 CSV：

    price.csv         trade_date, ticker, open, high, low, close
    volume.csv        date, stock_code, volume, turnover
    fundamentals.csv  report_date, announce_date, ticker, pe, pb, roe
    industry.csv      ticker, industry_name
    calendar.csv      date, is_trading_day
    fetch_metadata.json

设计原则：
- 必须复用参考项目的数据获取逻辑（_load_ohlcv_astock / _sina_kline_fallback /
  _normalize_ticker / _tencent_quote），不重新编造一套模拟数据逻辑。
- 参考项目是只读依赖，本模块不修改它。
- OHLCV 必须来自真实网络行情接口或 TradingAgents 的真实缓存；
  严格限制在 start_date ~ end_date；按 (date, ticker) 去重并排序；
  open/high/low/close/volume 转为数值；不允许用随机数/样例/前值填充伪造。
- 基本面时间点约束：参考项目的 PE/PB/ROE 是当前快照，不是历史 point-in-time
  数据库。当前快照的 announce_date 必须用真实抓取日期，不得用用户指定的
  历史 end_date 回填，不得复制到整个历史区间。无法获得可信历史基本面时
  输出只有表头的 fundamentals.csv，并在 metadata 中明确说明。
- 网络与数据源错误必须记录到 metadata，不静默吞掉。
- 路径用 pathlib，兼容 Windows，不写死绝对路径；新增代码带类型注解。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ADAPTER_VERSION = "0.1"

# 参考项目路径解析的默认候选（按优先级，run_fetch_real_data.py 会先解析环境变量与 CLI）
DEFAULT_TRADINGAGENTS_PATH = r"D:\dwzq\TradingAgents-astock-main"
REL_TRADINGAGENTS_PATH = r"..\TradingAgents-astock-main"

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
        tradingagents_path: 参考项目 TradingAgents-astock-main 根目录。
        cache_dir: TradingAgents OHLCV 缓存目录（None 则用参考项目默认）。
        snapshot_fundamentals: 是否抓取当前基本面快照（PE/PB/ROE）。
            默认 True。设为 False 时 fundamentals.csv 只输出表头。
    """

    tickers: list[str]
    start_date: str
    end_date: str
    output_dir: str | Path
    tradingagents_path: str | Path = DEFAULT_TRADINGAGENTS_PATH
    cache_dir: str | Path | None = None
    snapshot_fundamentals: bool = True
    # 内部记录每个 ticker 的错误/警告（不暴露给用户构造）
    per_ticker_errors: dict[str, str] = field(default_factory=dict)
    per_ticker_warnings: dict[str, list[str]] = field(default_factory=dict)


def resolve_tradingagents_path(
    cli_path: str | Path | None = None,
) -> Path:
    """按优先级解析参考项目路径。

    优先级：
      1. 命令行显式传入的路径（cli_path）
      2. 环境变量 TRADINGAGENTS_ASTOCK_PATH
      3. 默认路径 D:\\dwzq\\TradingAgents-astock-main
      4. 相对路径 ..\\TradingAgents-astock-main
    """
    candidates: list[Path] = []
    if cli_path:
        candidates.append(Path(cli_path))
    env_path = os.environ.get("TRADINGAGENTS_ASTOCK_PATH")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path(DEFAULT_TRADINGAGENTS_PATH))
    candidates.append(Path(REL_TRADINGAGENTS_PATH))

    for cand in candidates:
        if cand.exists() and (cand / "tradingagents" / "dataflows" / "a_stock.py").exists():
            return cand.resolve()
    # 都不存在时返回第一个候选，让后续 import 报错更可读
    return candidates[0].resolve()


def _ensure_a_stock(tradingagents_path: str | Path):
    """把参考项目加入 sys.path 并 import a_stock 模块。返回模块对象。"""
    root = Path(tradingagents_path).resolve()
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    import importlib

    mod = importlib.import_module("tradingagents.dataflows.a_stock")
    return mod


def _normalize_ticker(a_stock_mod, symbol: str) -> str:
    """复用参考项目的 _normalize_ticker，返回纯 6 位代码。"""
    return a_stock_mod._normalize_ticker(symbol)


def _fetch_ohlcv(
    a_stock_mod,
    ticker: str,
    start_date: str,
    end_date: str,
    cache_dir: str | Path | None,
) -> tuple[pd.DataFrame, str]:
    """获取单只 ticker 的 OHLCV，返回 (df, source_label)。

    优先调用参考项目的 _load_ohlcv_astock（内部可能 mootdx -> Sina HTTP fallback，
    但具体走哪条链路由参考项目内部决定，本适配器不猜测）。
    若 _load_ohlcv_astock 抛错，再直接尝试 _sina_kline_fallback。
    严格按 start_date ~ end_date 过滤。

    source_label 如实记录：成功时记参考项目函数名；无法确认实际是 mootdx 还是
    Sina 时记 unknown / internal_fallback，不猜测。
    """
    # _load_ohlcv_astock 的 curr_date 参数用于防未来函数截断，传 end_date
    used_sina_direct = False
    try:
        df = a_stock_mod._load_ohlcv_astock(ticker, end_date)
        # 参考项目内部可能走 mootdx 或 Sina fallback，本适配器无法可靠区分，
        # 如实记 internal_fallback（不猜测具体协议）。
        source = "internal_fallback"
    except Exception as exc:  # noqa: BLE001
        # 直接走 Sina HTTP fallback
        try:
            df = a_stock_mod._sina_kline_fallback(ticker, start_date, end_date)
            used_sina_direct = True
            source = "sina_http_direct"
        except Exception as exc2:  # noqa: BLE001
            raise RuntimeError(
                f"_load_ohlcv_astock failed: {exc}; "
                f"_sina_kline_fallback also failed: {exc2}"
            ) from exc2

    if df is None or df.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"]), source

    # 标准化列名（参考项目返回 Date/Open/High/Low/Close/Volume）
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["Date"])

    # 严格按日期区间过滤
    start_dt = pd.to_datetime(start_date).normalize()
    end_dt = pd.to_datetime(end_date).normalize()
    df = df[(df["Date"] >= start_dt) & (df["Date"] <= end_dt)]

    # 数值转换
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 去重（按 Date，单只 ticker 内部）
    df = df.drop_duplicates(subset=["Date"], keep="last").sort_values("Date").reset_index(drop=True)
    return df, source


def _fetch_fundamentals_snapshot(a_stock_mod, ticker: str) -> dict[str, Any]:
    """获取单只 ticker 的当前基本面快照（PE/PB）。

    参考项目的 PE/PB/ROE 更接近当前快照，不是完整历史 point-in-time 数据库。
    返回 dict: {pe, pb, roe, name, price}（缺失字段为 None）。
    """
    out: dict[str, Any] = {"pe": None, "pb": None, "roe": None, "name": None, "price": None}
    try:
        tq = a_stock_mod._tencent_quote([ticker])
    except Exception:  # noqa: BLE001
        tq = {}
    q = tq.get(ticker, {})
    if q:
        out["pe"] = q.get("pe_ttm")
        out["pb"] = q.get("pb")
        out["name"] = q.get("name")
        out["price"] = q.get("price")
    # ROE：参考项目 mootdx finance 才有，mootdx 不可用时无法获取，留 None
    return out


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
                # 参考项目 OHLCV 不含成交额；不伪造，留空
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
    a_stock_mod,
    fetch_date_str: str,
) -> tuple[pd.DataFrame, list[str]]:
    """构建 fundamentals.csv。

    严格遵守基本面时间点约束：
    - 参考项目的 PE/PB/ROE 是当前快照，不是历史 point-in-time 数据库。
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
    for ticker in config.tickers:
        norm = _normalize_ticker(a_stock_mod, ticker)
        snap = _fetch_fundamentals_snapshot(a_stock_mod, norm)
        if snap["pe"] is None and snap["pb"] is None and snap["roe"] is None:
            config.per_ticker_warnings.setdefault(norm, []).append(
                "fundamentals snapshot unavailable (tencent quote failed or empty)"
            )
            continue
        rows.append(
            {
                # 当前快照没有 report_date 概念，留空
                "report_date": "",
                # announce_date 用真实抓取日期，不用历史 end_date
                "announce_date": fetch_date_str,
                "ticker": norm,
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
    a_stock_mod,
) -> tuple[pd.DataFrame, list[str]]:
    """构建 industry.csv。

    优先获取真实行业信息；无法获取时用 unknown，并在 metadata 记录警告。
    不得因此中断 OHLCV 流程。
    """
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    got_any_real = False
    for ticker in config.tickers:
        norm = _normalize_ticker(a_stock_mod, ticker)
        industry_name: str | None = None
        # 参考项目 eastmoney push2 的 f127 是行业字段；直接复用其 get_fundamentals
        # 会拉很多无关信息且依赖网络，这里尝试用 push2 stock get 取 f127。
        try:
            market_code = 1 if norm.startswith("6") else 0
            url = "https://push2.eastmoney.com/api/qt/stock/get"
            params = {
                "fltt": "2",
                "invt": "2",
                "fields": "f127",
                "secid": f"{market_code}.{norm}",
            }
            # 复用参考项目的 _em_get（节流 + 会话复用）
            r = a_stock_mod._em_get(url, params=params, timeout=10)
            d = r.json().get("data", {})
            ind = d.get("f127")
            if ind:
                industry_name = str(ind)
                got_any_real = True
        except Exception as exc:  # noqa: BLE001
            config.per_ticker_warnings.setdefault(norm, []).append(
                f"industry fetch failed: {type(exc).__name__}: {exc}"
            )
        rows.append({"ticker": norm, "industry_name": industry_name if industry_name else "unknown"})

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
    ta_path = Path(config.tradingagents_path).resolve()
    a_stock_mod = _ensure_a_stock(ta_path)

    # 缓存写到当前项目的 outputs/cache 下（参考项目目录保持只读）。
    # 若用户显式指定 cache_dir 则用之；否则默认 outputs/cache（相对当前项目根）。
    if config.cache_dir:
        cache_dir_resolved = str(Path(config.cache_dir).resolve())
    else:
        # 当前项目根 = src 的上一级
        project_root = Path(__file__).resolve().parent.parent
        cache_dir_resolved = str((project_root / "outputs" / "cache").resolve())
    Path(cache_dir_resolved).mkdir(parents=True, exist_ok=True)
    # 通过环境变量传给参考项目（它读 config.data_cache_dir）
    os.environ["TRADINGAGENTS_CACHE_DIR"] = cache_dir_resolved

    fetch_date_str = date.today().strftime("%Y-%m-%d")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    resolved_tickers: list[str] = []
    ohlcv_by_ticker: dict[str, pd.DataFrame] = {}
    ohlcv_source_by_ticker: dict[str, str] = {}
    rows_by_ticker: dict[str, int] = {}

    for raw_ticker in config.tickers:
        try:
            norm = _normalize_ticker(a_stock_mod, raw_ticker)
        except Exception as exc:  # noqa: BLE001
            config.per_ticker_errors[raw_ticker] = f"normalize failed: {type(exc).__name__}: {exc}"
            continue
        resolved_tickers.append(norm)
        try:
            df, source = _fetch_ohlcv(
                a_stock_mod, norm, config.start_date, config.end_date, config.cache_dir
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
    fundamentals, fund_warnings = _build_fundamentals(config, a_stock_mod, fetch_date_str)
    industry, ind_warnings = _build_industry(config, a_stock_mod)

    # 落盘
    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
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
        "generated_at": generated_at,
        "fetch_date": fetch_date_str,
        "tradingagents_path": str(ta_path).replace("\\", "/"),
        "cache_dir": cache_dir_resolved.replace("\\", "/"),
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
            "date, NOT historical point-in-time fundamentals. The reference project's PE/PB/ROE "
            "are snapshots, not a complete historical fundamentals database. Do NOT backfill "
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
