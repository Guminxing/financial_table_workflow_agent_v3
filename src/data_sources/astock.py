"""Standalone A-share market data provider used by this project.

This module is part of ``financial_table_workflow_agent_v3`` and has no
runtime dependency on another Agent repository.  It talks directly to public
market-data HTTP endpoints and exposes a small, stable interface for the
project's deterministic data pipeline.

Portions of the ticker normalisation, Tencent quote parsing and Sina K-line
fallback were adapted from ``TradingAgents-Astock``
(``tradingagents/dataflows/a_stock.py``), then substantially reorganised and
modified for this standalone provider.  The adapted work is licensed under
Apache-2.0; see the repository ``NOTICE`` and
``third_party/licenses/Apache-2.0.txt``.

Modifications made for this project:
- removed all TradingAgents runtime, configuration and agent dependencies;
- added a project-owned Eastmoney OHLCV primary source;
- made HTTP sessions and cache locations injectable;
- isolated cache files under the caller-selected directory;
- normalised errors and DataFrame schemas for the financial-table pipeline.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests


DATA_SOURCE_VERSION = "1.0"

OHLCV_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume"]

_EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_EASTMONEY_STOCK_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
_SINA_KLINE_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData"
)
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
)


def normalize_ticker(symbol: str) -> str:
    """Return a safe six-digit A-share/ETF code.

    Accepted examples: ``600519``, ``SH600519``, ``600519.SH``,
    ``sz000001`` and ``BJ832000``.
    """
    if not isinstance(symbol, str):
        raise TypeError(f"ticker must be a string, got {type(symbol).__name__}")
    value = symbol.strip().upper()
    for suffix in (".SH", ".SZ", ".BJ"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    for prefix in ("SH", "SZ", "BJ"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    if not re.fullmatch(r"\d{6}", value):
        raise ValueError(f"invalid A-share ticker: {symbol!r}")
    return value


def _market_prefix(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"


def _eastmoney_secid(code: str) -> str:
    market = 1 if code.startswith(("5", "6", "9")) else 0
    return f"{market}.{code}"


def _to_float(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _eastmoney_lots_to_shares(value: Any) -> int | float | None:
    """Convert Eastmoney's board-lot volume to the project's share unit."""
    lots = _to_float(value)
    if lots is None:
        return None
    shares = lots * 100
    return int(shares) if shares.is_integer() else shares


def _normalise_ohlcv(
    frame: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    missing = [column for column in OHLCV_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"OHLCV response missing columns: {missing}")
    result = frame[OHLCV_COLUMNS].copy()
    result["Date"] = pd.to_datetime(result["Date"], errors="coerce").dt.normalize()
    for column in ("Open", "High", "Low", "Close", "Volume"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["Date", "Open", "High", "Low", "Close"])
    start = pd.to_datetime(start_date).normalize()
    end = pd.to_datetime(end_date).normalize()
    result = result[(result["Date"] >= start) & (result["Date"] <= end)]
    return (
        result.drop_duplicates(subset=["Date"], keep="last")
        .sort_values("Date")
        .reset_index(drop=True)
    )


class AStockDataSource:
    """Project-owned client for A-share OHLCV and current snapshots."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        cache_dir: str | Path | None = None,
        timeout: float = 15.0,
        eastmoney_min_interval: float = 0.25,
    ) -> None:
        self.session = session if session is not None else requests.Session()
        self.session.headers.update({"User-Agent": _USER_AGENT})
        self.cache_dir = Path(cache_dir).resolve() if cache_dir is not None else None
        self.timeout = float(timeout)
        self.eastmoney_min_interval = max(0.0, float(eastmoney_min_interval))
        self._last_eastmoney_call = 0.0
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_ohlcv(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
    ) -> tuple[pd.DataFrame, str]:
        """Fetch daily OHLCV using Eastmoney, with Sina as fallback."""
        code = normalize_ticker(ticker)
        start = pd.to_datetime(start_date).normalize()
        end = pd.to_datetime(end_date).normalize()
        if start > end:
            raise ValueError(f"start_date {start_date} must be <= end_date {end_date}")

        cache_path = self._cache_path(code, start_date, end_date)
        if cache_path is not None and cache_path.exists():
            cached = pd.read_csv(cache_path, encoding="utf-8")
            cached = _normalise_ohlcv(cached, start_date, end_date)
            if not cached.empty:
                return cached, "project_cache"

        primary_error: Exception | None = None
        try:
            frame = self._fetch_eastmoney_ohlcv(code, start_date, end_date)
            source = "eastmoney_http"
        except Exception as exc:  # noqa: BLE001
            primary_error = exc
            frame = pd.DataFrame(columns=OHLCV_COLUMNS)
            source = ""

        if frame.empty:
            if primary_error is None:
                primary_error = RuntimeError("Eastmoney returned no rows")
            try:
                frame = self._fetch_sina_ohlcv(code, start_date, end_date)
                source = "sina_http_fallback"
            except Exception as fallback_error:  # noqa: BLE001
                raise RuntimeError(
                    "A-share OHLCV fetch failed; "
                    f"eastmoney={type(primary_error).__name__}: {primary_error}; "
                    f"sina={type(fallback_error).__name__}: {fallback_error}"
                ) from fallback_error

        frame = _normalise_ohlcv(frame, start_date, end_date)
        if frame.empty:
            raise RuntimeError(
                f"no OHLCV rows returned for {code} in [{start_date}, {end_date}]"
            )
        if cache_path is not None:
            frame.to_csv(cache_path, index=False, encoding="utf-8")
        return frame, source

    def fetch_quote_snapshot(self, ticker: str) -> dict[str, Any]:
        """Fetch current Tencent quote fields used for PE/PB snapshots."""
        code = normalize_ticker(ticker)
        url = _TENCENT_QUOTE_URL + f"{_market_prefix(code)}{code}"
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        raw = response.content.decode("gbk", errors="replace")
        for line in raw.split(";"):
            match = re.search(r'v_(?:sh|sz|bj)(\d{6})="([^"]*)"', line)
            if match is None or match.group(1) != code:
                continue
            values = match.group(2).split("~")
            if len(values) < 53:
                break
            return {
                "name": values[1] or None,
                "price": _to_float(values[3]),
                "pe": _to_float(values[39]),
                "pb": _to_float(values[46]),
                "roe": None,
            }
        return {"name": None, "price": None, "pe": None, "pb": None, "roe": None}

    def fetch_industry(self, ticker: str) -> str | None:
        """Fetch the current Eastmoney industry label (field f127)."""
        code = normalize_ticker(ticker)
        response = self._eastmoney_get(
            _EASTMONEY_STOCK_URL,
            params={
                "fltt": "2",
                "invt": "2",
                "fields": "f127",
                "secid": _eastmoney_secid(code),
            },
        )
        data = response.json().get("data") or {}
        industry = data.get("f127")
        return str(industry).strip() if industry else None

    def _fetch_eastmoney_ohlcv(
        self,
        code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        response = self._eastmoney_get(
            _EASTMONEY_KLINE_URL,
            params={
                "secid": _eastmoney_secid(code),
                "klt": "101",
                "fqt": "0",
                "beg": start_date.replace("-", ""),
                "end": end_date.replace("-", ""),
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            },
        )
        payload = response.json()
        klines = (payload.get("data") or {}).get("klines") or []
        rows: list[dict[str, Any]] = []
        for raw in klines:
            parts = str(raw).split(",")
            if len(parts) < 7:
                continue
            rows.append(
                {
                    "Date": parts[0],
                    "Open": parts[1],
                    "Close": parts[2],
                    "High": parts[3],
                    "Low": parts[4],
                    # Eastmoney f56 is expressed in board lots (手). A-share
                    # daily volume in this project is standardised to shares,
                    # matching the Sina fallback and the volume.csv contract.
                    "Volume": _eastmoney_lots_to_shares(parts[5]),
                }
            )
        return _normalise_ohlcv(pd.DataFrame(rows), start_date, end_date)

    def _fetch_sina_ohlcv(
        self,
        code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        start = pd.to_datetime(start_date).normalize()
        end = pd.to_datetime(end_date).normalize()
        calendar_days = max(1, int((end - start).days) + 1)
        data_length = min(5000, max(100, int(calendar_days * 1.5) + 30))
        response = self.session.get(
            _SINA_KLINE_URL,
            params={
                "symbol": f"{_market_prefix(code)}{code}",
                "scale": "240",
                "ma": "no",
                "datalen": str(data_length),
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = json.loads(response.text)
        rows = [
            {
                "Date": item.get("day"),
                "Open": item.get("open"),
                "High": item.get("high"),
                "Low": item.get("low"),
                "Close": item.get("close"),
                "Volume": item.get("volume"),
            }
            for item in data or []
        ]
        return _normalise_ohlcv(pd.DataFrame(rows), start_date, end_date)

    def _eastmoney_get(self, url: str, *, params: dict[str, Any]):
        wait = self.eastmoney_min_interval - (
            time.monotonic() - self._last_eastmoney_call
        )
        if wait > 0:
            time.sleep(wait)
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            return response
        finally:
            self._last_eastmoney_call = time.monotonic()

    def _cache_path(
        self,
        code: str,
        start_date: str,
        end_date: str,
    ) -> Path | None:
        if self.cache_dir is None:
            return None
        safe_start = start_date.replace("-", "")
        safe_end = end_date.replace("-", "")
        return self.cache_dir / f"{code}-{safe_start}-{safe_end}-daily.csv"
