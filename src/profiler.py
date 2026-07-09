"""Data Profiler：金融表格数据剖析器。

第一阶段核心模块。确定性实现，不调用任何 LLM API。
输入一个包含 CSV 的目录，输出结构化 profile dict，并可落盘为
profile.json 与 profile_report.md。

设计目标：为后续 Planner Agent 提供一份"机器可读 + 人类可读"的数据画像，
让它能据此规划清洗/校验/对齐步骤。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

# ---- 常量：列名识别规则 -------------------------------------------------

# 日期列候选：列名包含这些子串，或大多数值可被 to_datetime 解析
DATE_NAME_HINTS = ("date", "day", "time", "report", "announce")

# 证券代码列候选：列名包含这些子串
ID_NAME_HINTS = ("ticker", "stock_code", "symbol", "code", "sec_id")

# 数值列异常检测规则：(列名子串, 下界, 上界, 异常描述)
# 下界/上界为 None 表示不检查该侧
NUMERIC_ANOMALY_RULES: list[tuple[str, float | None, float | None, str]] = [
    ("price", 0.0, None, "price <= 0"),
    ("open", 0.0, None, "open <= 0"),
    ("high", 0.0, None, "high <= 0"),
    ("low", 0.0, None, "low <= 0"),
    ("close", 0.0, None, "close <= 0"),
    ("volume", 0.0, None, "volume < 0"),
    ("turnover", 0.0, None, "turnover < 0"),
    ("pe", None, None, "pe < 0"),  # pe 允许为负（亏损），但仍提示
    ("pb", 0.0, None, "pb <= 0"),
]

# 缺失率告警阈值
MISSING_RATE_WARN = 0.2


class FinancialTableProfiler:
    """金融表格 Data Profiler。

    用法::

        profiler = FinancialTableProfiler(input_dir="data/sample")
        profile = profiler.run()
        profiler.save_json(profile, "outputs/profiles/profile.json")
        profiler.save_markdown(profile, "outputs/profiles/profile_report.md")
    """

    def __init__(self, input_dir: str | Path) -> None:
        self.input_dir = Path(input_dir)

    # ---- 公共入口 -------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """剖析目录下所有 CSV，返回完整 profile dict。"""
        csv_files = sorted(self.input_dir.glob("*.csv"))
        if not csv_files:
            raise FileNotFoundError(
                f"No CSV files found in {self.input_dir}. "
                "Run generate_sample_data.py first."
            )

        tables: list[dict[str, Any]] = []
        # 缓存每张表的 (date_columns, id_columns)，供跨表分析使用
        per_table_keys: dict[str, dict[str, list[str]]] = {}

        for csv_path in csv_files:
            table_profile = self._profile_one_table(csv_path)
            tables.append(table_profile)
            per_table_keys[csv_path.name] = {
                "date_columns": table_profile["date_columns"],
                "id_columns": table_profile["id_columns"],
            }

        cross = self._cross_table_findings(tables, per_table_keys)

        return {
            "project": "financial_table_workflow_agent",
            "profile_version": "0.1",
            "tables": tables,
            "cross_table_findings": cross,
        }

    # ---- 单表剖析 -------------------------------------------------------

    def _profile_one_table(self, csv_path: Path) -> dict[str, Any]:
        """剖析单张表。"""
        # 用 dtype=str 读入，避免 pandas 自动把日期/代码列转成奇怪类型，
        # 这样我们能自己控制类型推断，也更贴近"原始落库"状态。
        df = pd.read_csv(csv_path, dtype=str)
        n_rows, n_cols = df.shape

        # 数值列推断：尝试把每列转 numeric，成功率高的视为数值列（初步候选）
        numeric_candidates = self._detect_numeric_columns(df)
        # 日期列识别
        date_cols = self._detect_date_columns(df)
        # 证券代码列识别（排除已识别为日期列的列）
        id_cols = self._detect_id_columns(df, date_cols, numeric_candidates)
        # 数值列最终归类：从候选中剔除已被识别为日期/代码的列
        numeric_cols = [
            c for c in numeric_candidates if c not in set(date_cols) | set(id_cols)
        ]

        # 缺失值统计
        missing_summary = self._missing_summary(df)

        # 日期范围
        date_range = self._date_range(df, date_cols)

        # 重复行
        dup_rows = int(df.duplicated().sum())

        # 主键候选重复检测：第一个日期列 + 第一个证券代码列
        dup_key_candidates = self._duplicate_key_candidates(df, date_cols, id_cols)

        # 异常值检测
        numeric_stats = self._numeric_stats(df, numeric_cols)

        # 汇总 potential_issues
        issues: list[str] = []
        issues.extend(self._missing_issues(missing_summary))
        issues.extend(self._duplicate_issues(dup_rows, dup_key_candidates))
        issues.extend(self._anomaly_issues(numeric_stats))

        return {
            "table_name": csv_path.name,
            "file_path": str(csv_path).replace("\\", "/"),
            "n_rows": int(n_rows),
            "n_columns": int(n_cols),
            "columns": list(df.columns),
            "dtypes": {c: str(df[c].dtype) for c in df.columns},
            "missing_summary": missing_summary,
            "date_columns": date_cols,
            "id_columns": id_cols,
            "numeric_columns": numeric_cols,
            "date_range": date_range,
            "duplicate_rows_count": dup_rows,
            "duplicate_key_candidates": dup_key_candidates,
            "numeric_stats": numeric_stats,
            "potential_issues": issues,
        }

    # ---- 列类型识别 -----------------------------------------------------

    def _detect_numeric_columns(self, df: pd.DataFrame) -> list[str]:
        """识别数值列：非空值中可被解析为数字的比例 > 80% 即视为数值列。

        纯数字字符串（如 '000001'）也会被 to_numeric 解析成功，因此这里只做
        初步候选；调用方会在识别日期列/代码列后做最终归类。
        """
        numeric_cols = []
        for col in df.columns:
            s = df[col].dropna()
            if s.empty:
                continue
            parsed = pd.to_numeric(s, errors="coerce")
            rate = parsed.notna().mean()
            if rate > 0.8:
                numeric_cols.append(col)
        return numeric_cols

    def _detect_date_columns(self, df: pd.DataFrame) -> list[str]:
        """识别日期列：列名命中提示词，或大多数值可被 to_datetime 解析。

        注意：
        - 纯数字列（如 is_trading_day=0/1）语义上不是日期，需排除。
        - 列名命中提示词（如含 'day'）只是"候选"，还需值确实能解析成日期，
          且解析出的日期不全是同一基准日，才最终认定为日期列。
        """
        date_cols = []
        for col in df.columns:
            name_hit = any(h in col.lower() for h in DATE_NAME_HINTS)
            s = df[col].dropna()
            if s.empty:
                # 空列但名字像日期，仍记录
                if name_hit:
                    date_cols.append(col)
                continue
            parsed = pd.to_datetime(s, errors="coerce", format="mixed")
            rate = parsed.notna().mean()
            # 必须能解析成日期（rate > 0.8）；仅列名像日期但值不是日期的不算
            if rate <= 0.8:
                continue
            # 防御：解析出的日期若几乎全是同一个值（如 0/1 → 1970-01-01），
            # 判定为非日期列（避免把布尔/整数标志位误判成日期）
            if parsed.notna().sum() > 0:
                nunique_dates = parsed.dropna().dt.normalize().nunique()
                if nunique_dates <= 1:
                    continue
            date_cols.append(col)
        return date_cols

    def _detect_id_columns(
        self,
        df: pd.DataFrame,
        date_cols: list[str],
        numeric_candidates: list[str],
    ) -> list[str]:
        """识别证券代码列：列名命中提示词，或值形态像代码（短字符串、低基数）。

        已识别为日期列的列会被排除，避免把日期误判成代码。
        纯数字代码（如 '000001'）虽可被 to_numeric 解析，但列名命中提示词
        或形态像代码时优先归为代码列；同时排除明显是连续数值的列（如 OHLC、pe）。
        布尔/标志位列（如 is_trading_day=0/1）不应被当作代码列。
        """
        exclude = set(date_cols)
        id_cols = []
        for col in df.columns:
            if col in exclude:
                continue
            name_hit = any(h in col.lower() for h in ID_NAME_HINTS)
            if name_hit:
                id_cols.append(col)
                continue
            # 形态启发：非空值多为短字符串且唯一值数量较少（像代码而非自由文本）
            s = df[col].dropna().astype(str)
            if s.empty:
                continue
            short_rate = (s.str.len() <= 12).mean()
            cardinality = s.nunique()
            if not (short_rate > 0.9 and 0 < cardinality <= 50):
                continue
            # 排除布尔/标志位列：唯一值只有 2 个且取值为 0/1 / true/false / yes/no
            if cardinality <= 2:
                vals = set(v.lower() for v in s.unique())
                bool_like = {"0", "1", "true", "false", "yes", "no", "y", "n"}
                if vals.issubset(bool_like):
                    continue
            # 若该列可被解析为数值，则只有"像代码"（整数为主、低基数）才归为代码列，
            # 否则视为连续数值列（如 pe/pb/roe/价格），不当作 id
            if col in numeric_candidates and not self._looks_like_code(df, col):
                continue
            id_cols.append(col)
        return id_cols

    @staticmethod
    def _looks_like_code(df: pd.DataFrame, col: str) -> bool:
        """判断一个纯数字列是否更像"代码"而非"连续数值"。

        判据：唯一值数量少（<= 50）且小数比例低（多为整数/定长串）。
        典型如 ticker='000001'：5 个唯一值、全是整数；
        而 pe/pb/roe 多为小数、唯一值多，不会被误判为代码。
        """
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if s.empty:
            return False
        cardinality = s.nunique()
        frac_rate = (s % 1 != 0).mean()
        return cardinality <= 50 and frac_rate <= 0.3

    # ---- 缺失值 ---------------------------------------------------------

    def _missing_summary(self, df: pd.DataFrame) -> dict[str, dict[str, float]]:
        summary = {}
        n = len(df)
        for col in df.columns:
            miss = int(df[col].isna().sum())
            summary[col] = {
                "missing_count": miss,
                "missing_rate": round(miss / n, 4) if n else 0.0,
            }
        return summary

    def _missing_issues(self, missing_summary: dict) -> list[str]:
        issues = []
        for col, info in missing_summary.items():
            if info["missing_rate"] > MISSING_RATE_WARN:
                issues.append(
                    f"warning: column '{col}' missing_rate={info['missing_rate']:.2%} "
                    f">(>{MISSING_RATE_WARN:.0%})"
                )
        return issues

    # ---- 日期范围 -------------------------------------------------------

    def _date_range(self, df: pd.DataFrame, date_cols: list[str]) -> dict[str, dict[str, str]]:
        rng = {}
        for col in date_cols:
            s = df[col].dropna()
            if s.empty:
                rng[col] = {"min": None, "max": None}
                continue
            parsed = pd.to_datetime(s, errors="coerce", format="mixed").dropna()
            if parsed.empty:
                rng[col] = {"min": None, "max": None}
                continue
            rng[col] = {
                "min": parsed.min().strftime("%Y-%m-%d"),
                "max": parsed.max().strftime("%Y-%m-%d"),
            }
        return rng

    # ---- 重复检测 -------------------------------------------------------

    def _duplicate_key_candidates(
        self, df: pd.DataFrame, date_cols: list[str], id_cols: list[str]
    ) -> list[dict[str, Any]]:
        """用 第一个日期列 + 第一个证券代码列 作为主键候选，检测重复 key。

        若日期列与代码列指向同一列（理论上不会，但防御性处理），跳过。
        """
        if not (date_cols and id_cols):
            return []
        d, i = date_cols[0], id_cols[0]
        if d == i:
            return []
        sub = df[[d, i]].dropna()
        dup_count = int(sub.duplicated().sum())
        return [
            {
                "key": [d, i],
                "duplicate_count": dup_count,
            }
        ]

    def _duplicate_issues(self, dup_rows: int, dup_key_candidates: list[dict]) -> list[str]:
        issues = []
        if dup_rows > 0:
            issues.append(f"warning: {dup_rows} fully duplicated rows found")
        for cand in dup_key_candidates:
            if cand["duplicate_count"] > 0:
                issues.append(
                    f"warning: duplicate key on {cand['key']}: "
                    f"{cand['duplicate_count']} duplicates"
                )
        return issues

    # ---- 数值统计与异常 -------------------------------------------------

    def _numeric_stats(self, df: pd.DataFrame, numeric_cols: list[str]) -> dict[str, dict[str, float]]:
        stats = {}
        for col in numeric_cols:
            s = pd.to_numeric(df[col], errors="coerce").dropna()
            if s.empty:
                stats[col] = {"min": None, "max": None, "mean": None, "std": None}
                continue
            stats[col] = {
                "min": round(float(s.min()), 4),
                "max": round(float(s.max()), 4),
                "mean": round(float(s.mean()), 4),
                "std": round(float(s.std()), 4) if len(s) > 1 else 0.0,
            }
        return stats

    def _anomaly_issues(self, numeric_stats: dict[str, dict]) -> list[str]:
        issues = []
        for col, st in numeric_stats.items():
            if st["min"] is None:
                continue
            for hint, lo, hi, desc in NUMERIC_ANOMALY_RULES:
                if hint not in col.lower():
                    continue
                # pe 单独处理：允许负但提示
                if hint == "pe":
                    if st["min"] < 0:
                        issues.append(
                            f"warning: column '{col}' has negative value (min={st['min']}); "
                            f"possible loss-making company, verify if intended"
                        )
                    continue
                if lo is not None and st["min"] < lo:
                    issues.append(
                        f"warning: column '{col}' has value below {lo} (min={st['min']}); "
                        f"suspect {desc}"
                    )
                if hi is not None and st["max"] > hi:
                    issues.append(
                        f"warning: column '{col}' has value above {hi} (max={st['max']})"
                    )
        return issues

    # ---- 跨表分析 -------------------------------------------------------

    def _cross_table_findings(
        self,
        tables: list[dict[str, Any]],
        per_table_keys: dict[str, dict[str, list[str]]],
    ) -> dict[str, Any]:
        """跨表字段不一致、join key 建议等。"""
        possible_date: list[dict[str, str]] = []
        possible_sec: list[dict[str, str]] = []
        schema_inconsist: list[dict[str, Any]] = []
        join_suggestions: list[dict[str, Any]] = []
        global_issues: list[str] = []

        # 汇总每张表的日期列/代码列
        for t in tables:
            name = t["table_name"]
            for c in t["date_columns"]:
                possible_date.append({"table": name, "column": c})
            for c in t["id_columns"]:
                possible_sec.append({"table": name, "column": c})

        # ---- schema 不一致：同名语义不同列名 ----
        # price.trade_date vs volume.date
        price = next((t for t in tables if t["table_name"] == "price.csv"), None)
        volume = next((t for t in tables if t["table_name"] == "volume.csv"), None)
        fund = next((t for t in tables if t["table_name"] == "fundamentals.csv"), None)
        calendar = next((t for t in tables if t["table_name"] == "calendar.csv"), None)

        if price and volume:
            p_date = price["date_columns"][0] if price["date_columns"] else None
            v_date = volume["date_columns"][0] if volume["date_columns"] else None
            p_id = price["id_columns"][0] if price["id_columns"] else None
            v_id = volume["id_columns"][0] if volume["id_columns"] else None
            if p_date and v_date and p_date != v_date:
                schema_inconsist.append(
                    {
                        "type": "date_column_name_mismatch",
                        "tables": ["price.csv", "volume.csv"],
                        "columns": [p_date, v_date],
                        "note": "date fields have different names but likely same semantics",
                    }
                )
            if p_id and v_id and p_id != v_id:
                schema_inconsist.append(
                    {
                        "type": "security_id_column_name_mismatch",
                        "tables": ["price.csv", "volume.csv"],
                        "columns": [p_id, v_id],
                        "note": "security id fields have different names but likely same semantics",
                    }
                )
            # join key 建议
            if p_date and v_date and p_id and v_id:
                join_suggestions.append(
                    {
                        "left_table": "price.csv",
                        "right_table": "volume.csv",
                        "left_keys": [p_date, p_id],
                        "right_keys": [v_date, v_id],
                        "reason": "date/security id fields have different names but likely represent the same keys",
                    }
                )

        # ---- fundamentals 公告滞后提示 ----
        if fund:
            has_report = any("report" in c.lower() for c in fund["columns"])
            has_announce = any("announce" in c.lower() for c in fund["columns"])
            if has_report and has_announce:
                global_issues.append(
                    "fundamentals.csv has both report_date and announce_date; "
                    "use announce_date (not report_date) as the available-as-of date "
                    "to avoid look-ahead bias"
                )
                schema_inconsist.append(
                    {
                        "type": "fundamentals_lag",
                        "tables": ["fundamentals.csv"],
                        "columns": ["report_date", "announce_date"],
                        "note": "financial data has announcement lag; report_date is NOT the available-as-of date",
                    }
                )

        # ---- calendar 可作为交易日对齐依据 ----
        if calendar:
            global_issues.append(
                "calendar.csv can be used as the trading-day alignment reference "
                "(is_trading_day flag)"
            )

        # ---- price 与 volume 覆盖不一致提示 ----
        if price and volume:
            global_issues.append(
                "price.csv and volume.csv may have non-overlapping (date, ticker) keys; "
                "verify coverage before joining"
            )

        return {
            "possible_date_columns": possible_date,
            "possible_security_id_columns": possible_sec,
            "schema_inconsistencies": schema_inconsist,
            "join_key_suggestions": join_suggestions,
            "global_potential_issues": global_issues,
        }

    # ---- 落盘 -----------------------------------------------------------

    def save_json(self, profile: dict[str, Any], path: str | Path) -> Path:
        """保存 profile.json。"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        return p

    def save_markdown(self, profile: dict[str, Any], path: str | Path) -> Path:
        """生成并保存 profile_report.md。"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        md = self._render_markdown(profile)
        p.write_text(md, encoding="utf-8")
        return p

    # ---- Markdown 渲染 --------------------------------------------------

    def _render_markdown(self, profile: dict[str, Any]) -> str:
        lines: list[str] = []
        lines.append("# Financial Table Data Profile Report")
        lines.append("")
        lines.append(
            f"- project: `{profile['project']}`  |  profile_version: `{profile['profile_version']}`"
        )
        lines.append(f"- tables: **{len(profile['tables'])}**")
        total_issues = sum(len(t["potential_issues"]) for t in profile["tables"])
        total_issues += len(profile["cross_table_findings"]["global_potential_issues"])
        lines.append(f"- total issues found: **{total_issues}**")
        lines.append("")

        # 每张表
        for t in profile["tables"]:
            lines.append(f"## {t['table_name']}")
            lines.append("")
            lines.append(f"- file: `{t['file_path']}`")
            lines.append(f"- shape: {t['n_rows']} rows × {t['n_columns']} cols")
            lines.append(f"- date_columns: `{t['date_columns']}`")
            lines.append(f"- id_columns: `{t['id_columns']}`")
            lines.append(f"- numeric_columns: `{t['numeric_columns']}`")
            lines.append(f"- duplicate_rows_count: {t['duplicate_rows_count']}")
            if t["duplicate_key_candidates"]:
                for c in t["duplicate_key_candidates"]:
                    lines.append(
                        f"- duplicate_key_candidate: `{c['key']}` → {c['duplicate_count']} dups"
                    )
            lines.append("")

            # 日期范围
            if t["date_range"]:
                lines.append("### date_range")
                lines.append("")
                lines.append("| column | min | max |")
                lines.append("|---|---|---|")
                for col, rng in t["date_range"].items():
                    lines.append(f"| {col} | {rng['min']} | {rng['max']} |")
                lines.append("")

            # 缺失值
            lines.append("### missing_summary")
            lines.append("")
            lines.append("| column | missing_count | missing_rate |")
            lines.append("|---|---|---|")
            for col, info in t["missing_summary"].items():
                lines.append(f"| {col} | {info['missing_count']} | {info['missing_rate']:.2%} |")
            lines.append("")

            # 数值统计
            if t.get("numeric_stats"):
                lines.append("### numeric_stats")
                lines.append("")
                lines.append("| column | min | max | mean | std |")
                lines.append("|---|---|---|---|---|")
                for col, st in t["numeric_stats"].items():
                    lines.append(
                        f"| {col} | {st['min']} | {st['max']} | {st['mean']} | {st['std']} |"
                    )
                lines.append("")

            # 问题
            lines.append("### potential_issues")
            lines.append("")
            if t["potential_issues"]:
                for iss in t["potential_issues"]:
                    lines.append(f"- {iss}")
            else:
                lines.append("- (none)")
            lines.append("")

        # 跨表
        cross = profile["cross_table_findings"]
        lines.append("## Cross-Table Findings")
        lines.append("")

        lines.append("### possible_date_columns")
        lines.append("")
        for d in cross["possible_date_columns"]:
            lines.append(f"- `{d['table']}` → `{d['column']}`")
        lines.append("")

        lines.append("### possible_security_id_columns")
        lines.append("")
        for d in cross["possible_security_id_columns"]:
            lines.append(f"- `{d['table']}` → `{d['column']}`")
        lines.append("")

        lines.append("### schema_inconsistencies")
        lines.append("")
        if cross["schema_inconsistencies"]:
            for s in cross["schema_inconsistencies"]:
                lines.append(
                    f"- **{s['type']}** ({', '.join(s['tables'])}): "
                    f"columns `{s['columns']}` — {s['note']}"
                )
        else:
            lines.append("- (none)")
        lines.append("")

        lines.append("### join_key_suggestions")
        lines.append("")
        if cross["join_key_suggestions"]:
            for j in cross["join_key_suggestions"]:
                lines.append(
                    f"- `{j['left_table']}` [{', '.join(j['left_keys'])}] "
                    f"⟷ `{j['right_table']}` [{', '.join(j['right_keys'])}]"
                )
                lines.append(f"  - reason: {j['reason']}")
        else:
            lines.append("- (none)")
        lines.append("")

        lines.append("### global_potential_issues")
        lines.append("")
        if cross["global_potential_issues"]:
            for g in cross["global_potential_issues"]:
                lines.append(f"- {g}")
        else:
            lines.append("- (none)")
        lines.append("")

        return "\n".join(lines)
