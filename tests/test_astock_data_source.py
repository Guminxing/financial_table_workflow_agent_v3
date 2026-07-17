"""Tests for the project-owned standalone A-share data source."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd
import requests


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from data_sources.astock import AStockDataSource, normalize_ticker  # noqa: E402
from real_data_adapter import RealDataFetchConfig, fetch_real_data  # noqa: E402


class FakeResponse:
    def __init__(
        self,
        *,
        json_data=None,
        text: str = "",
        content: bytes | None = None,
        status_code: int = 200,
    ) -> None:
        self._json_data = json_data
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")
        self.status_code = status_code

    def json(self):
        if self._json_data is None:
            return json.loads(self.text)
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.headers: dict[str, str] = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        return self.responses.pop(0)


class TestTickerNormalisation(unittest.TestCase):
    def test_supported_formats(self):
        for raw in ("600519", "SH600519", "sh600519", "600519.SH"):
            self.assertEqual(normalize_ticker(raw), "600519")
        self.assertEqual(normalize_ticker("BJ832000"), "832000")

    def test_invalid_format_rejected(self):
        with self.assertRaises(ValueError):
            normalize_ticker("../../secret")
        with self.assertRaises(TypeError):
            normalize_ticker(600519)  # type: ignore[arg-type]


class TestAStockHttpClient(unittest.TestCase):
    def test_eastmoney_ohlcv_and_run_local_cache(self):
        response = FakeResponse(
            json_data={
                "data": {
                    "klines": [
                        "2024-01-02,10,11,12,9,100,1000,0,0,0,0",
                        "2024-01-03,11,12,13,10,120,1200,0,0,0,0",
                    ]
                }
            }
        )
        session = FakeSession([response])
        with tempfile.TemporaryDirectory(prefix="astock_cache_") as tmp:
            source = AStockDataSource(
                session=session,
                cache_dir=tmp,
                eastmoney_min_interval=0,
            )
            frame, label = source.fetch_ohlcv(
                "600519", "2024-01-01", "2024-01-03"
            )
            self.assertEqual(label, "eastmoney_http")
            self.assertEqual(list(frame.columns), [
                "Date", "Open", "High", "Low", "Close", "Volume"
            ])
            self.assertEqual(len(frame), 2)
            self.assertEqual(float(frame.iloc[0]["Close"]), 11.0)
            self.assertEqual(float(frame.iloc[0]["Volume"]), 10000.0)

            cached, cached_label = source.fetch_ohlcv(
                "600519", "2024-01-01", "2024-01-03"
            )
            self.assertEqual(cached_label, "project_cache")
            self.assertEqual(len(cached), 2)
            self.assertEqual(len(session.calls), 1)

    def test_sina_fallback(self):
        sina_payload = [
            {
                "day": "2024-01-02",
                "open": "10",
                "high": "12",
                "low": "9",
                "close": "11",
                "volume": "100",
            }
        ]
        session = FakeSession(
            [
                FakeResponse(json_data={"data": {"klines": []}}),
                FakeResponse(text=json.dumps(sina_payload)),
            ]
        )
        source = AStockDataSource(session=session, eastmoney_min_interval=0)
        frame, label = source.fetch_ohlcv(
            "000001", "2024-01-01", "2024-01-03"
        )
        self.assertEqual(label, "sina_http_fallback")
        self.assertEqual(len(frame), 1)
        self.assertEqual(float(frame.iloc[0]["Volume"]), 100.0)
        self.assertIn("sina.com.cn", session.calls[1]["url"])

    def test_tencent_snapshot_parser(self):
        values = [""] * 53
        values[1] = "贵州茅台"
        values[3] = "1500.25"
        values[39] = "22.5"
        values[46] = "8.2"
        body = f'v_sh600519="{"~".join(values)}";'.encode("gbk")
        source = AStockDataSource(
            session=FakeSession([FakeResponse(content=body)]),
            eastmoney_min_interval=0,
        )
        snapshot = source.fetch_quote_snapshot("600519")
        self.assertEqual(snapshot["name"], "贵州茅台")
        self.assertEqual(snapshot["pe"], 22.5)
        self.assertEqual(snapshot["pb"], 8.2)

    def test_industry_parser(self):
        source = AStockDataSource(
            session=FakeSession([FakeResponse(json_data={"data": {"f127": "白酒Ⅱ"}})]),
            eastmoney_min_interval=0,
        )
        self.assertEqual(source.fetch_industry("600519"), "白酒Ⅱ")


class TestStandaloneAdapter(unittest.TestCase):
    def test_default_cache_is_inside_output_dir(self):
        captured: dict[str, Path] = {}

        class FakeProvider:
            def __init__(self, *, cache_dir):
                captured["cache_dir"] = Path(cache_dir).resolve()

            def fetch_ohlcv(self, ticker, start_date, end_date):
                return pd.DataFrame(
                    [{
                        "Date": pd.Timestamp("2024-01-02"),
                        "Open": 10.0,
                        "High": 12.0,
                        "Low": 9.0,
                        "Close": 11.0,
                        "Volume": 100,
                    }]
                ), "eastmoney_http"

            def fetch_quote_snapshot(self, ticker):
                raise AssertionError("snapshot disabled")

            def fetch_industry(self, ticker):
                return "白酒Ⅱ"

        with tempfile.TemporaryDirectory(prefix="adapter_output_") as tmp:
            out = Path(tmp)
            with mock.patch("real_data_adapter.AStockDataSource", FakeProvider):
                metadata = fetch_real_data(
                    RealDataFetchConfig(
                        tickers=["600519", "../../invalid"],
                        start_date="2024-01-01",
                        end_date="2024-01-03",
                        output_dir=out,
                        snapshot_fundamentals=False,
                    )
                )
            self.assertEqual(captured["cache_dir"], (out / "cache").resolve())
            self.assertEqual(metadata["data_provider"], "project_internal_astock_http")
            self.assertEqual(metadata["resolved_tickers"], ["600519"])
            self.assertIn("../../invalid", metadata["per_ticker_errors"])
            self.assertNotIn("tradingagents_path", metadata)

    def test_runtime_modules_have_no_external_agent_dependency(self):
        paths = [
            SRC / "real_data_adapter.py",
            SRC / "run_fetch_real_data.py",
            SRC / "chat_agent.py",
            SRC / "agent_runtime" / "context.py",
            SRC / "agent_tools" / "pipeline_tools.py",
        ]
        for path in paths:
            text = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("tradingagents", text, path.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
