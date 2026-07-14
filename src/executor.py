"""Code Executor（第三阶段）。

读取第二阶段产出的 workflow_plan.json 与原始 CSV，
按 plan 的步骤用确定性 pandas 代码执行数据处理，
真正生成 analysis-ready financial panel table。

设计原则：
- 确定性 baseline，不调用任何外部 LLM API，离线可运行。
- 不训练预测模型，不输出投资建议，不连接真实券商系统。
- 严格防未来函数：所有 rolling/pct_change 按 ticker 分组、只用历史窗口；
  财务数据按 announce_date 做 as-of 对齐；label_next_5d 隔离于特征。
- 路径用 pathlib，兼容 Windows，不写死绝对路径。
- 不删除/重写第一、二阶段代码，本模块独立。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

# ---- 表名常量（五张 CSV 固定文件名） -----------------------

T_PRICE = "price.csv"
T_VOLUME = "volume.csv"
T_FUND = "fundamentals.csv"
T_INDUSTRY = "industry.csv"
T_CALENDAR = "calendar.csv"

EXECUTOR_VERSION = "0.1"


class CodeExecutor:
    """金融表格数据准备 Code Executor。

    用法::

        ex = CodeExecutor()
        plan = ex.load_workflow_plan("outputs_real/plans/workflow_plan.json")
        result = ex.execute(plan, "data/real_market")
        ex.save_outputs(result, "outputs_real/prepared")
        ex.save_execution_report(result, "outputs_real/prepared")
    """

    def __init__(self) -> None:
        # 执行日志（贯穿整个 execute）
        self._log: dict[str, Any] = self._fresh_log()

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    def load_workflow_plan(self, plan_path: str | Path) -> dict[str, Any]:
        """读取 workflow_plan.json。"""
        p = Path(plan_path)
        if not p.exists():
            raise FileNotFoundError(
                f"workflow plan not found: {p}. Run run_planner.py first."
            )
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)

    def load_raw_tables(self, input_dir: str | Path) -> dict[str, pd.DataFrame]:
        """读取 5 张原始 CSV（dtype=str，保留原始状态）。"""
        d = Path(input_dir)
        tables: dict[str, pd.DataFrame] = {}
        for name in [T_PRICE, T_VOLUME, T_FUND, T_INDUSTRY, T_CALENDAR]:
            p = d / name
            if not p.exists():
                raise FileNotFoundError(f"missing raw table: {p}")
            tables[name] = pd.read_csv(p, dtype=str)
        return tables

    def execute(
        self, plan: dict[str, Any], input_dir: str | Path
    ) -> dict[str, Any]:
        """按 plan 执行全部数据处理步骤，返回结果 dict。

        返回结构包含 panel(DataFrame)、data_dictionary、execution_log。
        """
        self._log = self._fresh_log()
        self._log["input_plan_path"] = str(input_dir).replace("\\", "/")
        self._log["input_dir"] = str(input_dir).replace("\\", "/")

        raw = self.load_raw_tables(input_dir)
        self._log["steps_executed"].append(
            {"step": "load_raw_tables", "status": "ok",
             "tables": {k: list(v.columns) for k, v in raw.items()}}
        )

        # step 2: 字段标准化
        std = self._standardize_column_names(raw)

        # step 3: 日期解析
        typed = self._parse_dates(std)

        # step 4: 主键去重
        dedup = self._dedup_primary_keys(typed)

        # step 5: 交易日对齐
        aligned = self._align_trading_calendar(dedup)

        # step 6: 合并 price + volume
        panel = self._merge_price_volume(aligned)

        # step 7: 行情与成交量特征
        panel = self._compute_price_volume_features(panel)

        # step 8: 财务 announce_date 对齐
        panel = self._align_fundamentals(panel, aligned)

        # step 9: 合并 industry
        panel = self._merge_industry(panel, aligned)

        # step 10: 未来 5 日收益率标签
        panel = self._create_future_return_label(panel)

        # step 11: 最终质量检查
        panel = self._final_quality_checks(panel)

        # 列顺序整理
        panel = self._order_columns(panel)

        # data dictionary
        data_dict = self._build_data_dictionary()

        # 填充 log 的最终摘要
        self._finalize_log(panel)

        return {
            "panel": panel,
            "data_dictionary": data_dict,
            "execution_log": self._log,
        }

    def save_outputs(self, result: dict[str, Any], output_dir: str | Path) -> dict[str, Path]:
        """保存 prepared_panel.csv / data_dictionary.json / execution_log.json。"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        panel: pd.DataFrame = result["panel"]
        csv_path = out / "prepared_panel.csv"
        # 用 utf-8-sig 便于 Excel 打开，日期格式化为字符串
        panel_to_write = panel.copy()
        if "date" in panel_to_write.columns:
            panel_to_write["date"] = pd.to_datetime(panel_to_write["date"]).dt.strftime("%Y-%m-%d")
        panel_to_write.to_csv(csv_path, index=False, encoding="utf-8-sig")

        dict_path = out / "data_dictionary.json"
        with dict_path.open("w", encoding="utf-8") as f:
            json.dump(result["data_dictionary"], f, ensure_ascii=False, indent=2)

        log_path = out / "execution_log.json"
        # 回填 output_files
        result["execution_log"]["output_files"] = [
            str(csv_path).replace("\\", "/"),
            str(dict_path).replace("\\", "/"),
            str(log_path).replace("\\", "/"),
        ]
        with log_path.open("w", encoding="utf-8") as f:
            json.dump(result["execution_log"], f, ensure_ascii=False, indent=2)

        return {"panel": csv_path, "dictionary": dict_path, "log": log_path}

    def save_execution_report(
        self, result: dict[str, Any], output_dir: str | Path
    ) -> Path:
        """生成并保存 execution_report.md。"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        md_path = out / "execution_report.md"
        md_path.write_text(
            self._render_report(result), encoding="utf-8"
        )
        return md_path

    # ------------------------------------------------------------------
    # step 2: 字段标准化
    # ------------------------------------------------------------------

    def _standardize_column_names(
        self, raw: dict[str, pd.DataFrame]
    ) -> dict[str, pd.DataFrame]:
        """统一字段名：日期→date，证券代码→ticker。"""
        std: dict[str, pd.DataFrame] = {}
        rename_map = {
            "trade_date": "date",
            "stock_code": "ticker",
        }
        for name, df in raw.items():
            d = df.rename(columns={c: rename_map.get(c, c) for c in df.columns})
            std[name] = d
        self._log["steps_executed"].append(
            {"step": "standardize_column_names", "status": "ok",
             "rename_map": rename_map}
        )
        return std

    # ------------------------------------------------------------------
    # step 3: 日期解析
    # ------------------------------------------------------------------

    def _parse_dates(
        self, std: dict[str, pd.DataFrame]
    ) -> dict[str, pd.DataFrame]:
        """把日期列解析为 datetime，记录无法解析的行。"""
        date_cols = {
            T_PRICE: ["date"],
            T_VOLUME: ["date"],
            T_FUND: ["report_date", "announce_date"],
            T_CALENDAR: ["date"],
        }
        typed: dict[str, pd.DataFrame] = {}
        for name, df in std.items():
            d = df.copy()
            for col in date_cols.get(name, []):
                if col not in d.columns:
                    continue
                before_na = d[col].isna().sum()
                parsed = pd.to_datetime(d[col], errors="coerce", format="mixed")
                new_na = parsed.isna().sum()
                d[col] = parsed
                if new_na > before_na:
                    self._log["warnings"].append(
                        f"{name}.{col}: {new_na - before_na} value(s) could not be parsed as date"
                    )
            typed[name] = d
        self._log["steps_executed"].append(
            {"step": "parse_and_validate_dates", "status": "ok",
             "date_columns_parsed": date_cols}
        )
        return typed

    # ------------------------------------------------------------------
    # step 4: 主键去重
    # ------------------------------------------------------------------

    def _dedup_primary_keys(
        self, typed: dict[str, pd.DataFrame]
    ) -> dict[str, pd.DataFrame]:
        """对 price / volume 按 (date, ticker) 去重，默认保留最后一条。"""
        dedup: dict[str, pd.DataFrame] = {}
        for name in [T_PRICE, T_VOLUME]:
            df = typed[name].copy()
            if "date" in df.columns and "ticker" in df.columns:
                dup_count = int(df.duplicated(subset=["date", "ticker"]).sum())
                if dup_count > 0:
                    self._log["warnings"].append(
                        f"{name}: {dup_count} duplicate (date, ticker) rows; "
                        f"strategy=keep_last (needs manual confirmation)"
                    )
                    self._log["quality_checks"].append(
                        {
                            "check": "duplicate_row_handling",
                            "table": name,
                            "duplicate_count": dup_count,
                            "strategy": "keep_last",
                            "needs_manual_confirmation": True,
                        }
                    )
                    df = df.drop_duplicates(
                        subset=["date", "ticker"], keep="last"
                    ).reset_index(drop=True)
            dedup[name] = df
        # 其余表原样保留
        for name in [T_FUND, T_INDUSTRY, T_CALENDAR]:
            dedup[name] = typed[name]
        self._log["steps_executed"].append(
            {"step": "validate_primary_keys", "status": "ok"}
        )
        return dedup

    # ------------------------------------------------------------------
    # step 5: 交易日对齐
    # ------------------------------------------------------------------

    def _align_trading_calendar(
        self, dedup: dict[str, pd.DataFrame]
    ) -> dict[str, pd.DataFrame]:
        """用 calendar 的 is_trading_day 对齐 price/volume。"""
        cal = dedup[T_CALENDAR].copy()
        # is_trading_day 可能是字符串 '0'/'1'
        cal["is_trading_day"] = (
            pd.to_numeric(cal["is_trading_day"], errors="coerce")
            .fillna(0)
            .astype(int)
        )
        trading_days = set(
            cal.loc[cal["is_trading_day"] == 1, "date"].dropna().tolist()
        )

        aligned: dict[str, pd.DataFrame] = {}
        for name in [T_PRICE, T_VOLUME]:
            df = dedup[name].copy()
            if "date" in df.columns:
                before = len(df)
                df = df[df["date"].isin(trading_days)].reset_index(drop=True)
                dropped = before - len(df)
                if dropped > 0:
                    self._log["warnings"].append(
                        f"{name}: {dropped} row(s) dropped (non-trading-day)"
                    )
            aligned[name] = df
        aligned[T_FUND] = dedup[T_FUND]
        aligned[T_INDUSTRY] = dedup[T_INDUSTRY]
        aligned[T_CALENDAR] = cal
        self._log["steps_executed"].append(
            {"step": "align_with_trading_calendar", "status": "ok",
             "n_trading_days": len(trading_days)}
        )
        return aligned

    # ------------------------------------------------------------------
    # step 6: 合并 price + volume
    # ------------------------------------------------------------------

    def _merge_price_volume(
        self, aligned: dict[str, pd.DataFrame]
    ) -> pd.DataFrame:
        """以 price 为主表，left join volume on (date, ticker)。"""
        price = aligned[T_PRICE].copy()
        volume = aligned[T_VOLUME].copy()

        # 数值列转 numeric
        for col in ["open", "high", "low", "close"]:
            if col in price.columns:
                price[col] = pd.to_numeric(price[col], errors="coerce")
        for col in ["volume", "turnover"]:
            if col in volume.columns:
                volume[col] = pd.to_numeric(volume[col], errors="coerce")

        panel = price.merge(
            volume[["date", "ticker", "volume", "turnover"]],
            on=["date", "ticker"],
            how="left",
        )
        panel["source_price_available"] = True
        panel["source_volume_available"] = panel["volume"].notna() | panel["turnover"].notna()

        # 记录覆盖不一致
        miss_vol = int(panel["source_volume_available"].sum() == 0)  # 占位
        n_missing_vol = int((~panel["source_volume_available"]).sum())
        if n_missing_vol > 0:
            self._log["warnings"].append(
                f"merge_price_and_volume: {n_missing_vol} panel row(s) have no volume data (left join)"
            )
        self._log["steps_executed"].append(
            {"step": "merge_price_and_volume", "status": "ok",
             "n_rows": len(panel),
             "n_missing_volume": n_missing_vol}
        )
        return panel

    # ------------------------------------------------------------------
    # step 7: 行情与成交量特征
    # ------------------------------------------------------------------

    def _compute_price_volume_features(self, panel: pd.DataFrame) -> pd.DataFrame:
        """按 ticker 分组生成 return/volatility/turnover 特征（只用历史窗口）。"""
        df = panel.sort_values(["ticker", "date"]).copy()

        # return_1d / return_5d：pct_change 只用当前及过去价格
        # fill_method=None 显式禁止 ffill，避免缺失值被前向填充造成潜在泄漏
        grp = df.groupby("ticker", group_keys=False)["close"]
        df["return_1d"] = grp.pct_change(1, fill_method=None)
        df["return_5d"] = grp.pct_change(5, fill_method=None)

        # volatility_20d：return_1d 的历史 20 日滚动标准差
        df["volatility_20d"] = (
            df.groupby("ticker", group_keys=False)["return_1d"]
            .rolling(20, min_periods=1)
            .std()
            .reset_index(level=0, drop=True)
        )

        # turnover_20d：turnover 的历史 20 日滚动均值
        df["turnover_20d"] = (
            df.groupby("ticker", group_keys=False)["turnover"]
            .rolling(20, min_periods=1)
            .mean()
            .reset_index(level=0, drop=True)
        )

        self._log["steps_executed"].append(
            {"step": "compute_price_volume_features", "status": "ok",
             "features": ["return_1d", "return_5d", "volatility_20d", "turnover_20d"],
             "leakage_safe": True,
             "note": "all rolling/pct_change grouped by ticker, historical window only"}
        )
        return df

    # ------------------------------------------------------------------
    # step 8: 财务 announce_date 对齐
    # ------------------------------------------------------------------

    def _align_fundamentals(
        self, panel: pd.DataFrame, aligned: dict[str, pd.DataFrame]
    ) -> pd.DataFrame:
        """把 pe/pb/roe 按 announce_date as-of merge 到日频 panel。

        关键：禁止用 report_date 对齐；对每个 ticker，某日只能用
        announce_date <= date 的最近一条财务数据。
        """
        df = panel.sort_values(["ticker", "date"]).copy()
        fund = aligned[T_FUND].copy()

        # 数值列
        for col in ["pe", "pb", "roe"]:
            if col in fund.columns:
                fund[col] = pd.to_numeric(fund[col], errors="coerce")

        # 按 ticker 分组做 merge_asof，避免不同 ticker 财务数据串用
        merged_parts = []
        for ticker, sub in df.groupby("ticker", sort=False):
            fund_t = fund[fund["ticker"] == ticker].sort_values("announce_date")
            if fund_t.empty:
                # 该 ticker 无财务数据：补 announce_date(NaT) + pe/pb/roe(NA)，
                # 保证 panel 始终含 announce_date 列，避免 Critic 因列缺失而 failed。
                sub = sub.assign(
                    announce_date=pd.NaT, pe=pd.NA, pb=pd.NA, roe=pd.NA
                )
                merged_parts.append(sub)
                continue
            sub_sorted = sub.sort_values("date")
            asof = pd.merge_asof(
                sub_sorted,
                fund_t[["announce_date", "pe", "pb", "roe"]],
                left_on="date",
                right_on="announce_date",
                direction="backward",
            )
            merged_parts.append(asof)

        df = pd.concat(merged_parts, ignore_index=True)

        # source flag
        df["source_fundamental_available"] = df[["pe", "pb", "roe"]].notna().any(axis=1)

        self._log["steps_executed"].append(
            {"step": "align_fundamentals_by_announce_date", "status": "ok",
             "method": "merge_asof direction=backward on announce_date, grouped by ticker",
             "look_ahead_bias_avoided": True,
             "n_rows_with_fundamental": int(df["source_fundamental_available"].sum())}
        )
        self._log["quality_checks"].append(
            {
                "check": "fundamentals_aligned_by_announce_date",
                "status": "implemented",
                "method": "merge_asof backward on announce_date per ticker",
                "report_date_not_used": True,
            }
        )
        return df

    # ------------------------------------------------------------------
    # step 9: 合并 industry
    # ------------------------------------------------------------------

    def _merge_industry(
        self, panel: pd.DataFrame, aligned: dict[str, pd.DataFrame]
    ) -> pd.DataFrame:
        """按 ticker 合并 industry_name，标记缺失/异常。"""
        df = panel.copy()
        industry = aligned[T_INDUSTRY].copy()

        # 清洗 industry_name：去首尾空格，空字符串视为缺失
        industry["industry_name"] = industry["industry_name"].astype(str).str.strip()
        industry.loc[industry["industry_name"].isin(["", "nan"]), "industry_name"] = pd.NA

        df = df.merge(
            industry[["ticker", "industry_name"]],
            on="ticker",
            how="left",
        )
        df["source_industry_available"] = df["industry_name"].notna()

        n_missing_ind = int((~df["source_industry_available"]).sum())
        if n_missing_ind > 0:
            self._log["warnings"].append(
                f"merge_industry: {n_missing_ind} panel row(s) have missing/abnormal industry_name"
            )
        self._log["steps_executed"].append(
            {"step": "merge_industry", "status": "ok",
             "n_missing_industry": n_missing_ind}
        )
        return df

    # ------------------------------------------------------------------
    # step 10: 未来 5 日收益率标签
    # ------------------------------------------------------------------

    def _create_future_return_label(self, panel: pd.DataFrame) -> pd.DataFrame:
        """生成 label_next_5d = close.shift(-5)/close - 1，按 ticker 分组。

        标签本质是未来信息，仅作标签用途，不进入特征列。
        """
        df = panel.sort_values(["ticker", "date"]).copy()
        df["label_next_5d"] = (
            df.groupby("ticker", group_keys=False)["close"]
            .shift(-5) / df["close"] - 1
        )
        self._log["steps_executed"].append(
            {"step": "create_future_return_label", "status": "ok",
             "label": "label_next_5d",
             "role": "label_only",
             "must_exclude_from_features": True}
        )
        return df

    # ------------------------------------------------------------------
    # step 11: 最终质量检查
    # ------------------------------------------------------------------

    def _final_quality_checks(self, panel: pd.DataFrame) -> pd.DataFrame:
        """最终质量检查并记录到 log。"""
        df = panel.copy()
        n_rows = len(df)
        n_cols = df.shape[1]

        # 主键唯一性
        pk_dup = int(df.duplicated(subset=["date", "ticker"]).sum())
        pk_unique = pk_dup == 0

        # 列缺失率
        missing = {
            col: round(float(df[col].isna().mean()), 4) for col in df.columns
        }

        # 异常值计数
        def _neg_count(col: str, strict: bool) -> int:
            if col not in df.columns:
                return 0
            s = pd.to_numeric(df[col], errors="coerce")
            return int((s < 0 if strict else s <= 0).sum()) if strict else int((s <= 0).sum())

        price_le_zero = 0
        for c in ["open", "high", "low", "close"]:
            if c in df.columns:
                s = pd.to_numeric(df[c], errors="coerce")
                price_le_zero += int((s <= 0).sum())
        vol_lt_zero = _neg_count("volume", True)
        turn_lt_zero = _neg_count("turnover", True)

        checks = [
            {"check": "primary_key_uniqueness", "value": pk_unique,
             "duplicate_count": pk_dup, "severity": "error" if not pk_unique else "info"},
            {"check": "missing_rate_after_join", "column_missing_summary": missing,
             "severity": "info"},
            {"check": "negative_or_zero_price_check", "count": price_le_zero,
             "severity": "error" if price_le_zero > 0 else "info"},
            {"check": "negative_volume_or_turnover_check",
             "negative_volume": vol_lt_zero, "negative_turnover": turn_lt_zero,
             "severity": "error" if (vol_lt_zero + turn_lt_zero) > 0 else "info"},
            {"check": "label_next_5d_missing_rate",
             "value": missing.get("label_next_5d", None),
             "severity": "info"},
            {"check": "pe_pb_roe_missing_rates",
             "pe": missing.get("pe"), "pb": missing.get("pb"), "roe": missing.get("roe"),
             "severity": "info"},
            {"check": "industry_name_missing_rate",
             "value": missing.get("industry_name"),
             "severity": "warning" if (missing.get("industry_name") or 0) > 0 else "info"},
        ]
        self._log["quality_checks"].extend(checks)

        self._log["steps_executed"].append(
            {"step": "final_missing_and_quality_checks", "status": "ok",
             "n_rows": n_rows, "n_columns": n_cols,
             "primary_key_unique": pk_unique}
        )
        return df

    # ------------------------------------------------------------------
    # 列顺序整理
    # ------------------------------------------------------------------

    def _order_columns(self, panel: pd.DataFrame) -> pd.DataFrame:
        """按主键→行情→成交量→特征→标签→source flag 顺序整理列。"""
        preferred = [
            "date", "ticker",
            "open", "high", "low", "close",
            "volume", "turnover",
            "return_1d", "return_5d", "volatility_20d", "turnover_20d",
            "pe", "pb", "roe", "industry_name",
            "label_next_5d",
            "source_price_available", "source_volume_available",
            "source_fundamental_available", "source_industry_available",
            "announce_date",
        ]
        cols = [c for c in preferred if c in panel.columns]
        # 兜底：把未列出的列追加到末尾
        cols += [c for c in panel.columns if c not in cols]
        return panel[cols]

    # ------------------------------------------------------------------
    # data dictionary
    # ------------------------------------------------------------------

    def _build_data_dictionary(self) -> dict[str, dict[str, str]]:
        return {
            "date": {"role": "primary_key", "description": "Trading date (trading-day aligned)"},
            "ticker": {"role": "primary_key", "description": "Security identifier"},
            "open": {"role": "raw_input", "description": "Daily open price"},
            "high": {"role": "raw_input", "description": "Daily high price"},
            "low": {"role": "raw_input", "description": "Daily low price"},
            "close": {"role": "raw_input", "description": "Daily close price"},
            "volume": {"role": "raw_input", "description": "Daily trading volume"},
            "turnover": {"role": "raw_input", "description": "Daily turnover (notional)"},
            "return_1d": {"role": "feature",
                          "description": "One-day historical return from close price (pct_change(1), grouped by ticker)"},
            "return_5d": {"role": "feature",
                          "description": "Five-day historical return from close price (pct_change(5), grouped by ticker)"},
            "volatility_20d": {"role": "feature",
                               "description": "Historical 20-day rolling std of return_1d (past window only, grouped by ticker)"},
            "turnover_20d": {"role": "feature",
                             "description": "Historical 20-day rolling mean of turnover (past window only, grouped by ticker)"},
            "pe": {"role": "feature",
                   "description": "P/E ratio, as-of aligned by announce_date (NOT report_date) to avoid look-ahead bias"},
            "pb": {"role": "feature",
                   "description": "P/B ratio, as-of aligned by announce_date (NOT report_date) to avoid look-ahead bias"},
            "roe": {"role": "feature",
                    "description": "ROE, as-of aligned by announce_date (NOT report_date) to avoid look-ahead bias"},
            "industry_name": {"role": "feature",
                              "description": "Industry classification (static, joined by ticker)"},
            "label_next_5d": {"role": "label",
                              "description": "Future 5-day return = close.shift(-5)/close-1; MUST be excluded from feature columns"},
            "source_price_available": {"role": "source_flag", "description": "Whether price source exists for the row"},
            "source_volume_available": {"role": "source_flag", "description": "Whether volume/turnover is non-null"},
            "source_fundamental_available": {"role": "source_flag", "description": "Whether pe/pb/roe at least one is non-null"},
            "source_industry_available": {"role": "source_flag", "description": "Whether industry_name is non-null"},
            "announce_date": {"role": "auxiliary",
                              "description": "Announce date of the as-of matched fundamental record (for audit)"},
        }

    # ------------------------------------------------------------------
    # log 初始化与收尾
    # ------------------------------------------------------------------

    def _fresh_log(self) -> dict[str, Any]:
        return {
            "project": "financial_table_workflow_agent",
            "executor_version": EXECUTOR_VERSION,
            "input_plan_path": "",
            "input_dir": "",
            "steps_executed": [],
            "warnings": [],
            "errors": [],
            "output_files": [],
            "final_table_summary": {},
            "column_missing_summary": {},
            "quality_checks": [],
        }

    def _finalize_log(self, panel: pd.DataFrame) -> None:
        n_rows = len(panel)
        n_cols = panel.shape[1]
        pk_dup = int(panel.duplicated(subset=["date", "ticker"]).sum())
        date_min = panel["date"].min() if "date" in panel.columns else None
        date_max = panel["date"].max() if "date" in panel.columns else None
        n_tickers = int(panel["ticker"].nunique()) if "ticker" in panel.columns else 0
        self._log["final_table_summary"] = {
            "n_rows": n_rows,
            "n_columns": n_cols,
            "n_tickers": n_tickers,
            "date_min": str(date_min)[:10] if date_min is not None else None,
            "date_max": str(date_max)[:10] if date_max is not None else None,
            "primary_key_unique": pk_dup == 0,
        }
        self._log["column_missing_summary"] = {
            col: round(float(panel[col].isna().mean()), 4) for col in panel.columns
        }

    # ------------------------------------------------------------------
    # Markdown 报告
    # ------------------------------------------------------------------

    def _render_report(self, result: dict[str, Any]) -> str:
        panel: pd.DataFrame = result["panel"]
        log = result["execution_log"]
        lines: list[str] = []
        lines.append("# Code Execution Report")
        lines.append("")
        lines.append(f"- project: `{log['project']}`  |  executor_version: `{log['executor_version']}`")
        lines.append("")

        # 1. Input Files
        lines.append("## 1. Input Files")
        lines.append("")
        lines.append(f"- input_dir: `{log['input_dir']}`")
        lines.append("- raw tables: price.csv, volume.csv, fundamentals.csv, industry.csv, calendar.csv")
        lines.append("")

        # 2. Workflow Plan Used
        lines.append("## 2. Workflow Plan Used")
        lines.append("")
        lines.append(f"- plan: `{log['input_plan_path']}`")
        lines.append("- planner steps followed: load_raw_tables → standardize_column_names → parse_and_validate_dates → validate_primary_keys → align_with_trading_calendar → merge_price_and_volume → compute_price_volume_features → align_fundamentals_by_announce_date → merge_industry → create_future_return_label → final_missing_and_quality_checks")
        lines.append("")

        # 3. Executed Steps
        lines.append("## 3. Executed Steps")
        lines.append("")
        lines.append("| # | step | status |")
        lines.append("|---|---|---|")
        for i, s in enumerate(log["steps_executed"], 1):
            lines.append(f"| {i} | {s['step']} | {s.get('status', '-')} |")
        lines.append("")

        # 4. Output Table Summary
        summ = log["final_table_summary"]
        lines.append("## 4. Output Table Summary")
        lines.append("")
        lines.append(f"- rows: {summ['n_rows']}")
        lines.append(f"- columns: {summ['n_columns']}")
        lines.append(f"- tickers: {summ['n_tickers']}")
        lines.append(f"- date range: {summ['date_min']} ~ {summ['date_max']}")
        lines.append(f"- primary key unique: {summ['primary_key_unique']}")
        lines.append("")
        lines.append("### column missing summary")
        lines.append("")
        lines.append("| column | missing_rate |")
        lines.append("|---|---|")
        for col, rate in log["column_missing_summary"].items():
            lines.append(f"| {col} | {rate:.2%} |")
        lines.append("")

        # 5. Generated Features
        lines.append("## 5. Generated Features")
        lines.append("")
        feats = ["return_1d", "return_5d", "volatility_20d", "turnover_20d", "pe", "pb", "roe", "industry_name"]
        for f in feats:
            lines.append(f"- `{f}`")
        lines.append("")
        lines.append("All rolling/pct_change features are grouped by ticker and use historical windows only (no future data).")
        lines.append("")

        # 6. Label Definition
        lines.append("## 6. Label Definition")
        lines.append("")
        lines.append("`label_next_5d` = future 5-day return = `close.shift(-5)/close - 1`, grouped by ticker.")
        lines.append("")
        lines.append("- It is a **future-looking** value and can only be used as a **supervised-learning label**.")
        lines.append("- It **must NOT** be used as a feature. Exclude it from the feature matrix before training.")
        lines.append("")

        # 7. Fundamental Data Alignment
        lines.append("## 7. Fundamental Data Alignment")
        lines.append("")
        lines.append("pe/pb/roe are aligned to the daily panel via `pd.merge_asof(direction='backward')` on **announce_date**, grouped by ticker.")
        lines.append("")
        lines.append("- For each row date `t`, only the most recent fundamental record with `announce_date <= t` is used.")
        lines.append("- `report_date` is **NOT** used as the available-as-of date, which avoids **look-ahead bias**.")
        lines.append("")

        # 8. Warnings and Limitations
        lines.append("## 8. Warnings and Limitations")
        lines.append("")
        lines.append("- This is a **deterministic baseline executor**; no LLM is called.")
        lines.append("- Dedup strategy defaults to **keep last**; this needs **manual confirmation**.")
        if log["warnings"]:
            lines.append("- Warnings:")
            for w in log["warnings"]:
                lines.append(f"  - {w}")
        lines.append("- No model is trained in this stage.")
        lines.append("- No full Validity Critic is run in this stage (planned for the next stage).")
        lines.append("- No investment advice is produced.")
        lines.append("")

        return "\n".join(lines)
