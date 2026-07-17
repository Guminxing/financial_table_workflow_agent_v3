"""fetch_real_market_data 工具测试（Stage 12）。

覆盖：
1. fetch_real_market_data 已注册。
2. Registry 工具数量从 10 更新为 11。
3. ticker 格式校验（6 位数字；非法格式拒绝）。
4. 日期格式校验（YYYY-MM-DD；非法日期拒绝）。
5. start_date > end_date 拒绝。
6. ticker 数量超过上限（>20）拒绝。
7. fetch 工具默认禁用 snapshot fundamentals。
8. fetch 工具使用当前 run 的 raw_data。
9. 产物不能逃出 run_root。
10. fetch 成功后更新 context.input_dir。
11. fetch 全部失败时返回结构化错误。
12. 部分 ticker 失败时保留成功结果并返回 warning。
13. 所有抓取测试 mock 网络/adapter，不访问真实网络。
14. risk level = guarded。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
for p in (str(SRC), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent_runtime.context import AgentContext  # noqa: E402
from agent_runtime.models import RiskLevel, ToolCall  # noqa: E402
from agent_tools.pipeline_tools import (  # noqa: E402
    MAX_FETCH_TICKERS,
    _validate_fetch_date,
    _validate_fetch_tickers,
    build_default_registry,
    build_default_registry_specs,
)

FIXTURE_DIR = HERE.parent / "test_data" / "real_market_sample"


def _copy_fixture(tmp_dir: Path, subdir: str = "input") -> Path:
    dst = tmp_dir / subdir
    shutil.copytree(FIXTURE_DIR, dst)
    return dst


def _make_ctx_no_input(tmp: Path, run_id: str = "run_fetch_001") -> AgentContext:
    """模式 B：无 input_dir 启动。"""
    return AgentContext.create_without_input_dir(
        workspace_root=HERE.parent,
        output_base=tmp / "outputs",
        run_id=run_id,
    )


def _fake_metadata(out_dir: Path, tickers, *, snapshot=False,
                   price_empty=False, failed_tickers=None) -> dict:
    """构造与真实 adapter 同构的 metadata dict，并把 fixture CSV 复制到 out_dir。"""
    failed_tickers = failed_tickers or []
    out_dir.mkdir(parents=True, exist_ok=True)
    # 复制 fixture 五张 CSV
    for name in ("price.csv", "volume.csv", "fundamentals.csv",
                 "industry.csv", "calendar.csv"):
        shutil.copyfile(FIXTURE_DIR / name, out_dir / name)
    if price_empty:
        # 清空 price.csv 只留表头
        pd.DataFrame(columns=["trade_date", "ticker", "open", "high", "low", "close"]).to_csv(
            out_dir / "price.csv", index=False, encoding="utf-8-sig"
        )
    rows_by_ticker = {t: 7 for t in tickers if t not in failed_tickers}
    metadata = {
        "project": "financial_table_workflow_agent",
        "adapter_version": "0.2",
        "data_source_version": "1.0",
        "data_provider": "project_internal_astock_http",
        "generated_at": "2026-07-16 12:00:00",
        "fetch_date": "2026-07-16",
        "cache_dir": str(out_dir / "cache").replace("\\", "/"),
        "requested_tickers": list(tickers),
        "resolved_tickers": [t for t in tickers if t not in failed_tickers],
        "start_date": "2024-01-01",
        "end_date": "2024-01-10",
        "ohlcv_source_by_ticker": {t: "eastmoney_http" for t in tickers if t not in failed_tickers},
        "rows_by_ticker": rows_by_ticker,
        "per_ticker_errors": {t: "OHLCV fetch failed: RuntimeError: boom" for t in failed_tickers},
        "per_ticker_warnings": {},
        "summary_rows": {
            "price": 0 if price_empty else 7 * len([t for t in tickers if t not in failed_tickers]),
            "volume": 0 if price_empty else 7 * len([t for t in tickers if t not in failed_tickers]),
            "fundamentals": 0,
            "industry": len([t for t in tickers if t not in failed_tickers]),
            "calendar": 10,
        },
        "output_files": {
            "price": str(out_dir / "price.csv").replace("\\", "/"),
            "volume": str(out_dir / "volume.csv").replace("\\", "/"),
            "fundamentals": str(out_dir / "fundamentals.csv").replace("\\", "/"),
            "industry": str(out_dir / "industry.csv").replace("\\", "/"),
            "calendar": str(out_dir / "calendar.csv").replace("\\", "/"),
        },
        "fundamentals_limitation": "snapshot, not historical point-in-time",
        "warnings": [],
        "errors": [f"{t}: OHLCV fetch failed: RuntimeError: boom" for t in failed_tickers],
        "snapshot_fundamentals_enabled": snapshot,
    }
    with (out_dir / "fetch_metadata.json").open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    return metadata


def _patched_fetch(fake_fn):
    """patch real_data_adapter.fetch_real_data 为 fake_fn（不访问网络）。"""
    import real_data_adapter
    return mock.patch.object(real_data_adapter, "fetch_real_data", side_effect=fake_fn)


class TestFetchToolRegistration(unittest.TestCase):
    # 1. fetch_real_market_data 已注册
    def test_fetch_tool_registered(self):
        reg = build_default_registry()
        self.assertIsNotNone(reg.get("fetch_real_market_data"))

    # 2. Registry 工具数量从 10 更新为 11
    def test_registry_has_11_tools(self):
        specs = build_default_registry_specs()
        self.assertEqual(len(specs), 11)
        reg = build_default_registry()
        self.assertEqual(len(reg.names()), 11)
        self.assertIn("fetch_real_market_data", reg.names())

    # 14. risk level = guarded
    def test_fetch_tool_risk_is_guarded(self):
        reg = build_default_registry()
        spec = reg.get("fetch_real_market_data")
        self.assertEqual(spec.risk_level, RiskLevel.GUARDED)


class TestFetchTickerValidation(unittest.TestCase):
    # 3. ticker 格式校验
    def test_valid_tickers(self):
        self.assertEqual(
            _validate_fetch_tickers(["600519", "000001", "SH688017", "688017.SH"]),
            ["600519", "000001", "SH688017", "688017.SH"],
        )

    def test_invalid_ticker_format_rejected(self):
        for bad in ["60051", "6005190", "ABCDEF", "sh600519x", "600519.SH.SZ"]:
            with self.assertRaises(ValueError, msg=f"should reject {bad!r}"):
                _validate_fetch_tickers([bad])

    def test_empty_tickers_rejected(self):
        with self.assertRaises(ValueError):
            _validate_fetch_tickers([])

    def test_non_string_ticker_rejected(self):
        with self.assertRaises(ValueError):
            _validate_fetch_tickers([600519])  # int, not str

    def test_duplicates_deduped(self):
        out = _validate_fetch_tickers(["600519", "600519", "000001"])
        self.assertEqual(out, ["600519", "000001"])

    # 6. ticker 数量超过上限
    def test_too_many_tickers_rejected(self):
        many = [f"{i:06d}" for i in range(MAX_FETCH_TICKERS + 1)]
        with self.assertRaises(ValueError):
            _validate_fetch_tickers(many)

    def test_max_tickers_allowed(self):
        many = [f"{i:06d}" for i in range(MAX_FETCH_TICKERS)]
        out = _validate_fetch_tickers(many)
        self.assertEqual(len(out), MAX_FETCH_TICKERS)


class TestFetchDateValidation(unittest.TestCase):
    # 4. 日期格式校验
    def test_valid_date(self):
        self.assertEqual(_validate_fetch_date("2024-01-01", "start_date"), "2024-01-01")

    def test_invalid_format_rejected(self):
        for bad in ["2024-1-1", "2024/01/01", "20240101", "2024-13-01", "2024-02-30"]:
            with self.assertRaises(ValueError, msg=f"should reject {bad!r}"):
                _validate_fetch_date(bad, "start_date")

    def test_non_string_date_rejected(self):
        with self.assertRaises(ValueError):
            _validate_fetch_date(20240101, "start_date")


class TestFetchToolExecution(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fetch_"))
        self.ctx = _make_ctx_no_input(self.tmp, "run_fetch")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _exec(self, name, args):
        reg = build_default_registry()
        return reg.execute(ToolCall(call_id=f"c_{name}", name=name, arguments=args), self.ctx)

    # 7. fetch 工具默认禁用 snapshot fundamentals
    def test_default_snapshot_fundamentals_false(self):
        captured = {}

        def fake(config):
            captured["snapshot"] = config.snapshot_fundamentals
            captured["tickers"] = list(config.tickers)
            return _fake_metadata(Path(config.output_dir), config.tickers, snapshot=config.snapshot_fundamentals)

        with _patched_fetch(fake):
            result = self._exec("fetch_real_market_data", {
                "tickers": ["600519"],
                "start_date": "2024-01-01",
                "end_date": "2024-01-10",
            })
        self.assertTrue(result.ok, result.to_dict())
        # 默认 snapshot_fundamentals=False
        self.assertFalse(captured["snapshot"])
        self.assertEqual(captured["tickers"], ["600519"])
        self.assertFalse(result.metrics["snapshot_fundamentals_enabled"])

    def test_snapshot_fundamentals_true_when_requested(self):
        captured = {}

        def fake(config):
            captured["snapshot"] = config.snapshot_fundamentals
            return _fake_metadata(Path(config.output_dir), config.tickers, snapshot=config.snapshot_fundamentals)

        with _patched_fetch(fake):
            result = self._exec("fetch_real_market_data", {
                "tickers": ["600519"],
                "start_date": "2024-01-01",
                "end_date": "2024-01-10",
                "snapshot_fundamentals": True,
            })
        self.assertTrue(result.ok)
        self.assertTrue(captured["snapshot"])
        self.assertTrue(result.metrics["snapshot_fundamentals_enabled"])

    # 5. start_date > end_date 拒绝
    def test_start_after_end_rejected(self):
        result = self._exec("fetch_real_market_data", {
            "tickers": ["600519"],
            "start_date": "2024-02-01",
            "end_date": "2024-01-01",
        })
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "INVALID_TOOL_ARGUMENTS")
        self.assertIn("start_date", result.error.message)

    def test_invalid_ticker_rejected(self):
        result = self._exec("fetch_real_market_data", {
            "tickers": ["60051"],
            "start_date": "2024-01-01",
            "end_date": "2024-01-10",
        })
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "INVALID_TOOL_ARGUMENTS")

    def test_invalid_date_rejected(self):
        result = self._exec("fetch_real_market_data", {
            "tickers": ["600519"],
            "start_date": "2024-13-01",
            "end_date": "2024-01-10",
        })
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "INVALID_TOOL_ARGUMENTS")

    def test_too_many_tickers_rejected(self):
        many = [f"{i:06d}" for i in range(MAX_FETCH_TICKERS + 1)]
        result = self._exec("fetch_real_market_data", {
            "tickers": many,
            "start_date": "2024-01-01",
            "end_date": "2024-01-10",
        })
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "INVALID_TOOL_ARGUMENTS")

    # 8. fetch 工具使用当前 run 的 raw_data
    def test_fetch_writes_to_run_raw_data(self):
        def fake(config):
            return _fake_metadata(Path(config.output_dir), config.tickers)

        with _patched_fetch(fake):
            result = self._exec("fetch_real_market_data", {
                "tickers": ["600519"],
                "start_date": "2024-01-01",
                "end_date": "2024-01-10",
            })
        self.assertTrue(result.ok)
        raw_data = self.ctx.run_root / "raw_data"
        self.assertTrue(raw_data.exists())
        self.assertTrue((raw_data / "price.csv").exists())
        self.assertTrue((raw_data / "fetch_metadata.json").exists())

    # 9. 产物不能逃出 run_root
    def test_artifacts_within_run_root(self):
        def fake(config):
            return _fake_metadata(Path(config.output_dir), config.tickers)

        with _patched_fetch(fake):
            result = self._exec("fetch_real_market_data", {
                "tickers": ["600519"],
                "start_date": "2024-01-01",
                "end_date": "2024-01-10",
            })
        self.assertTrue(result.ok)
        for art in result.artifacts:
            p = Path(art)
            self.assertTrue(p.exists())
            # 必须在 run_root 下
            p.resolve().relative_to(self.ctx.run_root)

    # 10. fetch 成功后更新 context.input_dir
    def test_fetch_updates_input_dir(self):
        self.assertFalse(self.ctx.has_input_dir())
        def fake(config):
            return _fake_metadata(Path(config.output_dir), config.tickers)

        with _patched_fetch(fake):
            result = self._exec("fetch_real_market_data", {
                "tickers": ["600519"],
                "start_date": "2024-01-01",
                "end_date": "2024-01-10",
            })
        self.assertTrue(result.ok)
        self.assertTrue(self.ctx.has_input_dir())
        self.assertEqual(self.ctx.input_dir, (self.ctx.run_root / "raw_data").resolve())
        # input_dir 在 run_root 下
        self.ctx.input_dir.relative_to(self.ctx.run_root)

    # 11. fetch 全部失败时返回结构化错误
    def test_all_failed_returns_structured_error(self):
        def fake(config):
            # price.csv 为空 → 全部失败
            return _fake_metadata(Path(config.output_dir), config.tickers, price_empty=True)

        with _patched_fetch(fake):
            result = self._exec("fetch_real_market_data", {
                "tickers": ["600519"],
                "start_date": "2024-01-01",
                "end_date": "2024-01-10",
            })
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "FETCH_NO_USABLE_DATA")
        # input_dir 未更新
        self.assertFalse(self.ctx.has_input_dir())
        # metrics 含 requested_tickers / errors
        self.assertEqual(result.metrics["requested_tickers"], ["600519"])
        self.assertIn("errors", result.metrics)

    # 12. 部分 ticker 失败时保留成功结果并返回 warning
    def test_partial_failure_keeps_successful(self):
        def fake(config):
            return _fake_metadata(
                Path(config.output_dir), ["600519", "000001"], failed_tickers=["000001"]
            )

        with _patched_fetch(fake):
            result = self._exec("fetch_real_market_data", {
                "tickers": ["600519", "000001"],
                "start_date": "2024-01-01",
                "end_date": "2024-01-10",
            })
        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(result.metrics["successful_tickers"], ["600519"])
        self.assertEqual(result.metrics["failed_tickers"], ["000001"])
        # warnings 含 partial fetch 提示
        warn_text = " ".join(result.metrics["warnings"])
        self.assertIn("partial fetch", warn_text)
        self.assertIn("000001", warn_text)
        # input_dir 仍更新（有成功 ticker）
        self.assertTrue(self.ctx.has_input_dir())

    def test_fetch_returns_next_action_configure(self):
        def fake(config):
            return _fake_metadata(Path(config.output_dir), config.tickers)

        with _patched_fetch(fake):
            result = self._exec("fetch_real_market_data", {
                "tickers": ["600519"],
                "start_date": "2024-01-01",
                "end_date": "2024-01-10",
            })
        self.assertTrue(result.ok)
        self.assertEqual(result.next_actions, ["configure_workflow"])

    def test_fetch_metadata_path_in_artifacts(self):
        def fake(config):
            return _fake_metadata(Path(config.output_dir), config.tickers)

        with _patched_fetch(fake):
            result = self._exec("fetch_real_market_data", {
                "tickers": ["600519"],
                "start_date": "2024-01-01",
                "end_date": "2024-01-10",
            })
        self.assertTrue(result.ok)
        # fetch_metadata.json 在 artifacts 中
        meta_arts = [a for a in result.artifacts if a.endswith("fetch_metadata.json")]
        self.assertTrue(meta_arts)
        # 五张 CSV 路径都在 artifacts 中
        for name in ("price.csv", "volume.csv", "fundamentals.csv", "industry.csv", "calendar.csv"):
            self.assertTrue(any(a.endswith(name) for a in result.artifacts), f"missing {name}")

    def test_fetch_adapter_exception_returns_tool_error(self):
        def boom(config):
            raise RuntimeError("network down")

        with _patched_fetch(boom):
            result = self._exec("fetch_real_market_data", {
                "tickers": ["600519"],
                "start_date": "2024-01-01",
                "end_date": "2024-01-10",
            })
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "TOOL_EXECUTION_ERROR")
        # input_dir 未更新
        self.assertFalse(self.ctx.has_input_dir())


class TestFetchDoesNotOverwriteDataRealMarket(unittest.TestCase):
    """fetch 工具只写当前 run 的 raw_data，绝不覆盖 data/real_market。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fetchsafe_"))
        self.ctx = _make_ctx_no_input(self.tmp, "run_safe")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fetch_writes_only_run_raw_data(self):
        written_paths: list[Path] = []
        shared_price = HERE.parent / "data" / "real_market" / "price.csv"
        shared_before = (
            shared_price.read_bytes() if shared_price.exists() else None
        )

        def fake(config):
            out = Path(config.output_dir)
            _fake_metadata(out, config.tickers)
            written_paths.append(out)
            return _fake_metadata(out, config.tickers)

        with _patched_fetch(fake):
            reg = build_default_registry()
            result = reg.execute(
                ToolCall(call_id="c1", name="fetch_real_market_data",
                         arguments={"tickers": ["600519"], "start_date": "2024-01-01",
                                    "end_date": "2024-01-10"}),
                self.ctx,
            )
        self.assertTrue(result.ok)
        # 唯一写入目录在 run_root/raw_data 下
        self.assertEqual(len(written_paths), 1)
        written_paths[0].relative_to(self.ctx.run_root)
        # 已有 data/real_market 可能包含用户数据；工具不得创建或改写 price.csv。
        shared_after = shared_price.read_bytes() if shared_price.exists() else None
        self.assertEqual(shared_after, shared_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
