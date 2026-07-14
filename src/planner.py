"""Workflow Planner Agent（第二阶段）。

读取第一阶段产出的 profile.json，结合下游分析目标 analysis_goal，
生成一份结构化的 workflow plan（workflow_plan.json + workflow_plan_report.md）。

设计原则：
- 确定性规则实现，不调用任何外部 LLM API，可离线运行。
- 不执行任何数据清洗代码，不生成 prepared_panel.csv；只"规划"。
- 根据 profile.json 的实际检测结果动态生成步骤与风险项，
  而非写死一份静态 JSON，为后续接入 LLM Planner 留出接口。
- 路径用 pathlib，兼容 Windows，不写死绝对路径。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ---- 默认分析目标 -------------------------------------------------------

DEFAULT_ANALYSIS_GOAL = (
    "构建一个用于 5 日收益率预测或因子分析的股票/ETF 日频建模宽表。"
    "要求每一行是 ticker-date，特征只能使用当前日期及之前可获得的信息，"
    "生成 return_1d、return_5d、volatility_20d、turnover_20d、pe、pb、roe、industry 等字段，"
    "标签为未来 5 日收益率 label_next_5d，并检查是否存在未来函数或数据泄漏。"
)

# ---- 表名常量（五张 CSV 固定文件名） -----------------------

T_PRICE = "price.csv"
T_VOLUME = "volume.csv"
T_FUND = "fundamentals.csv"
T_INDUSTRY = "industry.csv"
T_CALENDAR = "calendar.csv"

# 标准化后的统一字段名
STD_DATE = "date"
STD_TICKER = "ticker"


class WorkflowPlanner:
    """金融表格数据准备 Workflow Planner。

    用法::

        planner = WorkflowPlanner()
        profile = planner.load_profile("outputs_real/profiles/profile.json")
        plan = planner.build_plan(profile, analysis_goal)
        planner.save_plan(plan, "outputs_real/plans/workflow_plan.json")
        planner.save_markdown_report(plan, "outputs_real/plans/workflow_plan_report.md")
    """

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    def load_profile(self, profile_path: str | Path) -> dict[str, Any]:
        """读取 profile.json。"""
        p = Path(profile_path)
        if not p.exists():
            raise FileNotFoundError(
                f"profile not found: {p}. Run run_profile.py first."
            )
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)

    def build_plan(
        self, profile: dict[str, Any], analysis_goal: str = DEFAULT_ANALYSIS_GOAL
    ) -> dict[str, Any]:
        """根据 profile 与 analysis_goal 构建完整 workflow plan。"""
        tables = profile.get("tables", [])
        cross = profile.get("cross_table_findings", {})

        # 1. 检测到的上下文
        detected_context = self._build_detected_context(tables, cross)

        # 2. 规划假设（动态，基于 profile 发现）
        assumptions = self._build_planning_assumptions(tables, cross)

        # 3. workflow steps（动态生成）
        workflow_steps = self._build_workflow_steps(tables, cross)

        # 4. feature / label 计划
        feature_plan = self._build_feature_plan(tables)

        # 5. validation plan
        validation_plan = self._build_validation_plan(tables, cross)

        # 6. 给 Code Executor 的执行说明
        exec_notes = self._build_execution_notes(workflow_steps)

        # 7. 局限性
        limitations = self._build_limitations()

        return {
            "project": "financial_table_workflow_agent",
            "planner_version": "0.1",
            "analysis_goal": analysis_goal,
            "input_profile_path": "",  # 由 save 时回填
            "detected_context": detected_context,
            "planning_assumptions": assumptions,
            "workflow_steps": workflow_steps,
            "feature_plan": feature_plan,
            "validation_plan": validation_plan,
            "execution_notes_for_code_executor": exec_notes,
            "limitations": limitations,
            "next_stage_recommendation": (
                "下一阶段由 Code Executor Agent 读取本 workflow_plan.json，"
                "生成并执行 pandas 数据处理代码，产出 prepared_panel.csv，"
                "再交由 Validity Critic 基于 validation_plan 做泄漏与有效性校验。"
            ),
        }

    def save_plan(self, plan: dict[str, Any], output_json_path: str | Path) -> Path:
        """保存 workflow_plan.json。"""
        p = Path(output_json_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        return p

    def save_markdown_report(
        self, plan: dict[str, Any], output_md_path: str | Path
    ) -> Path:
        """生成并保存 workflow_plan_report.md。"""
        p = Path(output_md_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self._render_markdown(plan), encoding="utf-8")
        return p

    # ------------------------------------------------------------------
    # detected_context
    # ------------------------------------------------------------------

    def _build_detected_context(
        self, tables: list[dict], cross: dict
    ) -> dict[str, Any]:
        table_names = [t["table_name"] for t in tables]
        date_cols = [
            {"table": d["table"], "column": d["column"]}
            for d in cross.get("possible_date_columns", [])
        ]
        id_cols = [
            {"table": d["table"], "column": d["column"]}
            for d in cross.get("possible_security_id_columns", [])
        ]
        return {
            "tables": table_names,
            "main_entity": "ticker-date panel",
            "target_table_type": "analysis-ready financial panel table",
            "downstream_task_type": "factor_analysis_or_5d_return_prediction",
            "date_fields": date_cols,
            "security_id_fields": id_cols,
        }

    # ------------------------------------------------------------------
    # planning_assumptions（动态）
    # ------------------------------------------------------------------

    def _build_planning_assumptions(
        self, tables: list[dict], cross: dict
    ) -> list[str]:
        assumptions: list[str] = []

        # 通用假设
        assumptions.append(
            "主表为日频 ticker-date panel，主键为 (date, ticker)。"
        )
        assumptions.append(
            "所有特征必须只使用预测时点 t 及之前可获得的信息，禁止使用未来数据。"
        )

        # 字段口径不一致
        schema_inconsist = cross.get("schema_inconsistencies", [])
        if any(s.get("type") == "date_column_name_mismatch" for s in schema_inconsist):
            assumptions.append(
                "price 与 volume 的日期字段命名不一致（trade_date vs date），"
                "需先统一为 date 再合并。"
            )
        if any(
            s.get("type") == "security_id_column_name_mismatch" for s in schema_inconsist
        ):
            assumptions.append(
                "price 与 volume 的证券代码字段命名不一致（ticker vs stock_code），"
                "需先统一为 ticker 再合并。"
            )

        # fundamentals 公告滞后 → look-ahead bias
        fund = next((t for t in tables if t["table_name"] == T_FUND), None)
        if fund and "announce_date" in fund.get("columns", []) and "report_date" in fund.get("columns", []):
            assumptions.append(
                "fundamentals 同时存在 report_date 与 announce_date，存在公告滞后；"
                "财务字段（pe/pb/roe）只能基于 announce_date 对齐到日频 panel，"
                "严禁直接用 report_date 作为可用日期，否则会引入 look-ahead bias。"
            )

        # calendar 交易日对齐
        cal = next((t for t in tables if t["table_name"] == T_CALENDAR), None)
        if cal:
            assumptions.append(
                "存在 calendar.csv，可作为交易日对齐依据（is_trading_day 标志），"
                "用于剔除非交易日记录并补齐交易日序列。"
            )

        # price/volume 覆盖不一致
        global_issues = cross.get("global_potential_issues", [])
        if any("non-overlapping" in g for g in global_issues):
            assumptions.append(
                "price 与 volume 的 (date, ticker) 覆盖可能不一致，"
                "合并后需检查缺失并决定 left/inner join 策略。"
            )

        # 标签假设
        assumptions.append(
            "label_next_5d 为未来 5 日收益率，仅作标签用途，"
            "不得进入特征列；训练时必须从 feature columns 中排除。"
        )
        assumptions.append(
            "时间序列样本不得随机打乱，必须按时间做 train/test 切分。"
        )
        return assumptions

    # ------------------------------------------------------------------
    # workflow_steps（动态生成 13 步）
    # ------------------------------------------------------------------

    def _build_workflow_steps(
        self, tables: list[dict], cross: dict
    ) -> list[dict[str, Any]]:
        # 工具：按表名取表画像
        by_name = {t["table_name"]: t for t in tables}
        price = by_name.get(T_PRICE)
        volume = by_name.get(T_VOLUME)
        fund = by_name.get(T_FUND)
        industry = by_name.get(T_INDUSTRY)
        calendar = by_name.get(T_CALENDAR)

        steps: list[dict[str, Any]] = []

        # ---- 1. load_raw_tables ----
        steps.append(
            self._step(
                step_id=1,
                name="load_raw_tables",
                category="ingestion",
                priority="high",
                input_tables=[],
                output_tables=[
                    "raw_price",
                    "raw_volume",
                    "raw_fundamentals",
                    "raw_industry",
                    "raw_calendar",
                ],
                actions=[
                    "读取 price.csv、volume.csv、fundamentals.csv、industry.csv、calendar.csv",
                    "保留原始字段名与原始类型，不做任何清洗",
                    "记录每张表的行数与列数，与 profile.json 对账",
                ],
                reason="统一加载原始表，作为后续清洗与对齐的输入基线。",
                depends_on=[],
                risks_addressed=["原始数据未加载"],
                expected_output="5 个原始 DataFrame，字段与 profile.json 一致",
            )
        )

        # ---- 2. standardize_column_names ----
        actions_2 = []
        risks_2 = []
        if price and "trade_date" in price["columns"]:
            actions_2.append("price.trade_date → date")
        if volume and "date" in volume["columns"]:
            actions_2.append("volume.date → date（已是 date，保持）")
        if volume and "stock_code" in volume["columns"]:
            actions_2.append("volume.stock_code → ticker")
        if fund and "ticker" in fund["columns"]:
            actions_2.append("fundamentals.ticker → ticker（已是 ticker，保持）")
        if industry and "ticker" in industry["columns"]:
            actions_2.append("industry.ticker → ticker（已是 ticker，保持）")
        # 引用 cross_table schema 不一致
        for s in cross.get("schema_inconsistencies", []):
            if s.get("type") in (
                "date_column_name_mismatch",
                "security_id_column_name_mismatch",
            ):
                risks_2.append(
                    f"profiler 检出 {s['type']}: {s['columns']} — {s['note']}"
                )
        steps.append(
            self._step(
                step_id=2,
                name="standardize_column_names",
                category="schema_standardization",
                priority="high",
                input_tables=["raw_price", "raw_volume", "raw_fundamentals", "raw_industry"],
                output_tables=["std_price", "std_volume", "std_fundamentals", "std_industry"],
                actions=actions_2 or ["统一日期字段为 date，证券代码字段为 ticker"],
                reason=(
                    "profiler 发现 price 与 volume 的日期/代码字段命名不一致，"
                    "必须先统一字段名才能按 (date, ticker) 合并。"
                ),
                depends_on=[1],
                risks_addressed=risks_2 or ["字段口径不一致导致 join 失败"],
                expected_output="所有表日期列统一为 date，证券代码列统一为 ticker",
            )
        )

        # ---- 3. parse_and_validate_dates ----
        date_cols_to_parse = []
        if price:
            date_cols_to_parse += [c for c in price["date_columns"]]
        if volume:
            date_cols_to_parse += [c for c in volume["date_columns"]]
        if fund:
            date_cols_to_parse += [c for c in fund["date_columns"]]
        if calendar:
            date_cols_to_parse += [c for c in calendar["date_columns"]]
        steps.append(
            self._step(
                step_id=3,
                name="parse_and_validate_dates",
                category="type_normalization",
                priority="high",
                input_tables=["std_price", "std_volume", "std_fundamentals", "std_calendar"],
                output_tables=["typed_price", "typed_volume", "typed_fundamentals", "typed_calendar"],
                actions=[
                    f"将日期列解析为 datetime: {sorted(set(date_cols_to_parse))}",
                    "检查空日期、无法解析日期，记录异常行数",
                    "检查各表日期范围是否合理（与 profile.date_range 对账）",
                ],
                reason="日期是 panel 主键与对齐依据，必须先转成 datetime 才能做滚动与对齐。",
                depends_on=[2],
                risks_addressed=["字符串日期无法参与时间运算"],
                expected_output="日期列为 datetime 类型，异常日期被记录",
            )
        )

        # ---- 4. validate_primary_keys ----
        actions_4 = [
            "检查每张表的主键候选唯一性，重点 (date, ticker)",
        ]
        risks_4 = []
        if price:
            for cand in price.get("duplicate_key_candidates", []):
                if cand.get("duplicate_count", 0) > 0:
                    actions_4.append(
                        f"price.csv 检出重复主键 {cand['key']}: "
                        f"{cand['duplicate_count']} 条，需去重"
                    )
                    risks_4.append(
                        f"price 主键重复 ({cand['duplicate_count']} 条) — "
                        "去重策略需人工确认（保留最后一条 / 聚合）"
                    )
        actions_4.append(
            "去重策略默认：保留最后一条（按加载顺序），但需在 plan 中标记为需人工确认"
        )
        steps.append(
            self._step(
                step_id=4,
                name="validate_primary_keys",
                category="data_quality",
                priority="high",
                input_tables=["typed_price", "typed_volume", "typed_fundamentals"],
                output_tables=["pk_checked_price", "pk_checked_volume", "pk_checked_fundamentals"],
                actions=actions_4,
                reason=(
                    "profiler 发现 price.csv 存在重复 (trade_date, ticker) 主键，"
                    "若不去重会导致 join 后行数膨胀与标签错位。"
                ),
                depends_on=[3],
                risks_addressed=risks_4 or ["主键不唯一"],
                expected_output="主键唯一的中间表；去重动作被记录待人工确认",
            )
        )

        # ---- 5. align_with_trading_calendar ----
        actions_5 = []
        risks_5 = []
        if calendar:
            actions_5.append("使用 calendar.csv 的 is_trading_day 标志筛选交易日")
            actions_5.append("检查 price/volume 是否存在非交易日记录并记录")
            risks_5.append("profiler 提示 calendar 可作交易日对齐依据")
        else:
            actions_5.append("未发现 calendar.csv，跳过交易日对齐（记录为风险）")
            risks_5.append("缺少交易日历，无法对齐交易日")
        steps.append(
            self._step(
                step_id=5,
                name="align_with_trading_calendar",
                category="time_alignment",
                priority="medium",
                input_tables=["pk_checked_price", "pk_checked_volume", "typed_calendar"],
                output_tables=["aligned_price", "aligned_volume"],
                actions=actions_5,
                reason="用交易日历对齐，剔除非交易日记录，保证 panel 时间轴一致。",
                depends_on=[4],
                risks_addressed=risks_5,
                expected_output="仅含交易日的 price/volume 中间表",
            )
        )

        # ---- 6. merge_price_and_volume ----
        actions_6 = [
            "基于 date + ticker 合并 price 与 volume（字段已在 step2 统一）",
        ]
        risks_6 = []
        for g in cross.get("global_potential_issues", []):
            if "non-overlapping" in g:
                actions_6.append("price 与 volume 覆盖不一致，合并后检查缺失率")
                risks_6.append("price/volume 覆盖不一致 → 合并后部分 key 缺失")
        actions_6.append("默认 left join 以 price 为基准，缺失 volume 字段标记为待处理")
        steps.append(
            self._step(
                step_id=6,
                name="merge_price_and_volume",
                category="join",
                priority="high",
                input_tables=["aligned_price", "aligned_volume"],
                output_tables=["price_volume_panel"],
                actions=actions_6,
                reason=(
                    "行情与成交是日频 panel 的主体，需合并成一张 (date, ticker) 宽表。"
                    "profiler 已给出 join key 建议。"
                ),
                depends_on=[5],
                risks_addressed=risks_6 or ["行情与成交未合并"],
                expected_output="price_volume_panel，主键 (date, ticker)",
            )
        )

        # ---- 7. compute_price_volume_features ----
        steps.append(
            self._step(
                step_id=7,
                name="compute_price_volume_features",
                category="feature_engineering",
                priority="high",
                input_tables=["price_volume_panel"],
                output_tables=["feature_panel"],
                actions=[
                    "return_1d = close.pct_change(1)，按 ticker 分组，仅用当前及过去价格",
                    "return_5d = close.pct_change(5)，按 ticker 分组，仅用历史窗口",
                    "volatility_20d = close.pct_change().rolling(20).std()，仅用历史 20 日",
                    "turnover_20d = turnover.rolling(20).mean()，仅用历史 20 日",
                    "所有 rolling/pct_change 必须按 ticker 分组，禁止跨标的泄漏",
                    "禁止使用未来价格作为特征",
                ],
                reason=(
                    "生成收益与波动特征。rolling 窗口必须只使用历史数据，"
                    "否则构成 look-ahead bias。"
                ),
                depends_on=[6],
                risks_addressed=[
                    "rolling 窗口使用未来数据",
                    "跨标的泄漏（未按 ticker 分组）",
                ],
                expected_output="feature_panel 含 return_1d/return_5d/volatility_20d/turnover_20d",
            )
        )

        # ---- 8. align_fundamentals_by_announce_date ----
        actions_8 = []
        risks_8 = []
        if fund and "announce_date" in fund["columns"]:
            actions_8.append(
                "把 fundamentals 的 pe/pb/roe 基于 announce_date 对齐到日频 panel"
            )
            actions_8.append(
                "使用 as-of join / forward fill：每个交易日使用最近一次已公告的财务数据"
            )
            actions_8.append(
                "严禁直接用 report_date 作为可用日期（会引入未来函数）"
            )
            risks_8.append(
                "profiler 检出 fundamentals_lag: report_date 不是可用日期，"
                "必须用 announce_date 滞后对齐以避免 look-ahead bias"
            )
        else:
            actions_8.append("未发现 announce_date，跳过财务对齐（记录为风险）")
            risks_8.append("缺少 announce_date，无法安全对齐财务数据")
        steps.append(
            self._step(
                step_id=8,
                name="align_fundamentals_by_announce_date",
                category="time_alignment",
                priority="high",
                input_tables=["feature_panel", "pk_checked_fundamentals"],
                output_tables=["panel_with_fundamentals"],
                actions=actions_8,
                reason=(
                    "财务数据有公告滞后，必须基于 announce_date 对齐，"
                    "确保某日只能用到该日及之前已公告的财务数据。"
                ),
                depends_on=[7],
                risks_addressed=risks_8,
                expected_output="panel 含 pe/pb/roe，且均为已公告可得数据",
            )
        )

        # ---- 9. merge_industry ----
        actions_9 = ["按 ticker 合并 industry.csv 的 industry_name"]
        risks_9 = []
        if industry:
            for col, info in industry.get("missing_summary", {}).items():
                if info.get("missing_rate", 0) > 0:
                    actions_9.append(
                        f"industry.{col} 缺失率 {info['missing_rate']:.2%}，合并后标记 warning"
                    )
                    risks_9.append(f"industry.{col} 存在缺失或拼写异常")
        steps.append(
            self._step(
                step_id=9,
                name="merge_industry",
                category="join",
                priority="medium",
                input_tables=["panel_with_fundamentals", "std_industry"],
                output_tables=["panel_with_industry"],
                actions=actions_9,
                reason="补充行业字段作为分类特征；profiler 发现行业存在缺失/拼写异常需标记。",
                depends_on=[8],
                risks_addressed=risks_9 or ["行业字段缺失"],
                expected_output="panel 含 industry_name，异常值被标记",
            )
        )

        # ---- 10. create_future_return_label ----
        steps.append(
            self._step(
                step_id=10,
                name="create_future_return_label",
                category="label_engineering",
                priority="high",
                input_tables=["panel_with_industry"],
                output_tables=["labeled_panel"],
                actions=[
                    "label_next_5d = 未来 5 日收益率（close.shift(-5)/close - 1），按 ticker 分组",
                    "label_next_5d 只能作为标签，不得作为特征",
                    "在后续训练时必须从 feature columns 中排除 label_next_5d",
                    "生成 label 的行因含未来信息，训练特征矩阵中不得包含该列",
                ],
                reason=(
                    "生成预测标签。标签本质是未来信息，"
                    "必须严格隔离于特征之外，否则构成 label leakage。"
                ),
                depends_on=[9],
                risks_addressed=["label leakage", "未来收益混入特征"],
                expected_output="labeled_panel 含 label_next_5d（仅标签用途）",
            )
        )

        # ---- 11. final_missing_and_quality_checks ----
        actions_11 = []
        risks_11 = []
        # 汇总各表高缺失列
        for t in tables:
            for col, info in t.get("missing_summary", {}).items():
                if info.get("missing_rate", 0) > 0.2:
                    actions_11.append(
                        f"{t['table_name']}.{col} 缺失率 {info['missing_rate']:.2%} (>20%)，重点检查"
                    )
                    risks_11.append(f"{t['table_name']}.{col} 高缺失")
        actions_11.append("检查 join 后整体缺失率、重复 key、异常值、样本覆盖范围")
        steps.append(
            self._step(
                step_id=11,
                name="final_missing_and_quality_checks",
                category="data_quality",
                priority="medium",
                input_tables=["labeled_panel"],
                output_tables=["quality_checked_panel"],
                actions=actions_11,
                reason="join 与特征工程后需复查缺失/重复/异常，确保 panel 可用。",
                depends_on=[10],
                risks_addressed=risks_11 or ["join 后质量未知"],
                expected_output="quality_checked_panel + 缺失/异常清单",
            )
        )

        # ---- 12. leakage_and_validity_checks ----
        steps.append(
            self._step(
                step_id=12,
                name="leakage_and_validity_checks",
                category="validation_planning",
                priority="high",
                input_tables=["quality_checked_panel"],
                output_tables=["validation_findings"],
                actions=[
                    "规划 Validity Critic 需检查项（详见 validation_plan）",
                    "label leakage: label_next_5d 不得出现在特征列",
                    "look-ahead bias: rolling/pct_change 不得使用未来数据",
                    "fundamentals 是否使用 announce_date 滞后对齐",
                    "ticker-date 主键唯一性",
                    "时间序列不得随机打乱，需按时间 train/test 切分",
                    "幸存者偏差风险：检查是否存在仅含存续标的的样本",
                ],
                reason="在交付建模前，必须由 Validity Critic 做泄漏与有效性审查。",
                depends_on=[11],
                risks_addressed=[
                    "label leakage",
                    "look-ahead bias",
                    "时间序列打乱",
                    "幸存者偏差",
                ],
                expected_output="validation_findings，供 Critic 执行",
            )
        )

        # ---- 13. export_analysis_ready_outputs ----
        steps.append(
            self._step(
                step_id=13,
                name="export_analysis_ready_outputs",
                category="export",
                priority="medium",
                input_tables=["quality_checked_panel", "validation_findings"],
                output_tables=[
                    "prepared_panel.csv",
                    "data_dictionary.json",
                    "validation_report.json",
                    "data_quality_report.md",
                ],
                actions=[
                    "导出 prepared_panel.csv（analysis-ready 宽表）",
                    "导出 data_dictionary.json（字段口径说明）",
                    "导出 validation_report.json（校验结果）",
                    "导出 data_quality_report.md（质量报告）",
                    "注意：当前阶段只规划，不实际生成这些文件",
                ],
                reason="产出最终 analysis-ready 产物与配套文档，供下游建模使用。",
                depends_on=[12],
                risks_addressed=["产物未导出"],
                expected_output="4 个产物文件路径（规划层面，本阶段不生成）",
            )
        )

        return steps

    # ------------------------------------------------------------------
    # feature_plan
    # ------------------------------------------------------------------

    def _build_feature_plan(self, tables: list[dict]) -> dict[str, Any]:
        features = [
            {"name": "return_1d", "source": "price.close", "window": "1d lag", "leakage_safe": True},
            {"name": "return_5d", "source": "price.close", "window": "5d lag", "leakage_safe": True},
            {"name": "volatility_20d", "source": "price.close", "window": "20d rolling std", "leakage_safe": True},
            {"name": "turnover_20d", "source": "volume.turnover", "window": "20d rolling mean", "leakage_safe": True},
            {"name": "pe", "source": "fundamentals.pe", "window": "as-of announce_date", "leakage_safe": True},
            {"name": "pb", "source": "fundamentals.pb", "window": "as-of announce_date", "leakage_safe": True},
            {"name": "roe", "source": "fundamentals.roe", "window": "as-of announce_date", "leakage_safe": True},
            {"name": "industry_name", "source": "industry.industry_name", "window": "static", "leakage_safe": True},
        ]
        label = {
            "name": "label_next_5d",
            "definition": "未来 5 日收益率 = close.shift(-5)/close - 1，按 ticker 分组",
            "usage": "label only; must be excluded from feature columns",
        }
        excluded = [
            "label_next_5d",
            "any_future_return_columns",
            "raw_future_price_columns",
            "columns_available_only_after_prediction_date",
        ]
        return {
            "features": features,
            "label": label,
            "excluded_columns": excluded,
        }

    # ------------------------------------------------------------------
    # validation_plan
    # ------------------------------------------------------------------

    def _build_validation_plan(
        self, tables: list[dict], cross: dict
    ) -> dict[str, Any]:
        checks = [
            {
                "check_name": "primary_key_uniqueness",
                "severity": "error",
                "description": "(date, ticker) 主键必须唯一",
                "suggested_rule": "assert panel.groupby(['date','ticker']).size().max() == 1",
            },
            {
                "check_name": "missing_rate_after_join",
                "severity": "warning",
                "description": "join 后各特征列缺失率应在可接受范围",
                "suggested_rule": "for c in feature_cols: assert panel[c].isna().mean() < 0.2",
            },
            {
                "check_name": "label_not_in_features",
                "severity": "error",
                "description": "label_next_5d 不得出现在特征列",
                "suggested_rule": "assert 'label_next_5d' not in feature_cols",
            },
            {
                "check_name": "no_future_return_in_features",
                "severity": "error",
                "description": "特征中不得包含任何未来收益列",
                "suggested_rule": "assert not any('future' in c or c.startswith('return_') and 'next' in c for c in feature_cols)",
            },
            {
                "check_name": "rolling_window_uses_past_only",
                "severity": "error",
                "description": "rolling/pct_change 只能使用历史窗口",
                "suggested_rule": "verify rolling(20).std() and pct_change(1) use no shift(-k) with k>0",
            },
            {
                "check_name": "fundamentals_aligned_by_announce_date",
                "severity": "error",
                "description": "财务字段必须基于 announce_date 滞后对齐",
                "suggested_rule": "for each row, fundamentals effective date <= row date, based on announce_date",
            },
            {
                "check_name": "trading_calendar_alignment",
                "severity": "warning",
                "description": "panel 日期应与交易日历对齐，无非交易日记录",
                "suggested_rule": "assert set(panel['date']).issubset(set(calendar[is_trading_day==1]['date']))",
            },
            {
                "check_name": "time_based_train_test_split_required",
                "severity": "error",
                "description": "时间序列必须按时间切分，不得随机打乱",
                "suggested_rule": "train.max(date) < test.min(date)",
            },
            {
                "check_name": "duplicate_row_handling",
                "severity": "warning",
                "description": "重复行/重复主键必须已处理",
                "suggested_rule": "assert panel.duplicated(['date','ticker']).sum() == 0",
            },
            {
                "check_name": "suspicious_industry_values",
                "severity": "warning",
                "description": "行业字段缺失或拼写异常需标记",
                "suggested_rule": "flag industry_name with missing or trailing/leading whitespace",
            },
            {
                "check_name": "negative_or_zero_price_check",
                "severity": "error",
                "description": "价格不得 <= 0",
                "suggested_rule": "assert (panel[['open','high','low','close']] > 0).all().all()",
            },
            {
                "check_name": "negative_volume_or_turnover_check",
                "severity": "error",
                "description": "成交量/成交额不得 < 0",
                "suggested_rule": "assert (panel[['volume','turnover']] >= 0).all().all()",
            },
        ]
        return {"checks": checks}

    # ------------------------------------------------------------------
    # execution_notes
    # ------------------------------------------------------------------

    def _build_execution_notes(self, steps: list[dict]) -> list[str]:
        notes = [
            "Code Executor 应按 workflow_steps 的 step_id 顺序执行，尊重 depends_on 依赖。",
            "每步执行后应输出中间表名与行数，便于追溯。",
            "所有 rolling/pct_change 必须按 ticker 分组（groupby('ticker')），禁止跨标的泄漏。",
            "财务对齐必须用 announce_date 做 as-of join，禁止用 report_date。",
            "label_next_5d 生成后立即从特征矩阵中排除，单独保存为标签向量。",
            "本阶段只规划，Code Executor 在下一阶段实现；当前不生成 prepared_panel.csv。",
        ]
        return notes

    # ------------------------------------------------------------------
    # limitations
    # ------------------------------------------------------------------

    def _build_limitations(self) -> list[str]:
        return [
            "当前 Planner 为确定性规则版本，不调用 LLM，规划逻辑固定。",
            "本阶段只输出 plan，不执行任何数据处理代码。",
            "不保证 prepared_panel.csv 已生成；该文件由下一阶段 Code Executor 产出。",
            "去重策略（保留最后一条 vs 聚合）需人工确认，Planner 仅给出默认建议。",
            "未做收益预测、未做投资建议、未连接真实券商系统。",
        ]

    # ------------------------------------------------------------------
    # 工具：构造单个 step
    # ------------------------------------------------------------------

    @staticmethod
    def _step(
        step_id: int,
        name: str,
        category: str,
        priority: str,
        input_tables: list[str],
        output_tables: list[str],
        actions: list[str],
        reason: str,
        depends_on: list[int],
        risks_addressed: list[str],
        expected_output: str,
    ) -> dict[str, Any]:
        return {
            "step_id": step_id,
            "name": name,
            "category": category,
            "priority": priority,
            "input_tables": input_tables,
            "output_tables": output_tables,
            "actions": actions,
            "reason": reason,
            "depends_on": depends_on,
            "risks_addressed": risks_addressed,
            "expected_output": expected_output,
        }

    # ------------------------------------------------------------------
    # Markdown 渲染
    # ------------------------------------------------------------------

    def _render_markdown(self, plan: dict[str, Any]) -> str:
        lines: list[str] = []
        lines.append("# Workflow Plan Report")
        lines.append("")
        lines.append(f"- project: `{plan['project']}`  |  planner_version: `{plan['planner_version']}`")
        lines.append("")

        # 1. Analysis Goal
        lines.append("## 1. Analysis Goal")
        lines.append("")
        lines.append(plan["analysis_goal"])
        lines.append("")

        # 2. Detected Data Context
        ctx = plan["detected_context"]
        lines.append("## 2. Detected Data Context")
        lines.append("")
        lines.append(f"- tables: `{ctx['tables']}`")
        lines.append(f"- main_entity: {ctx['main_entity']}")
        lines.append(f"- target_table_type: {ctx['target_table_type']}")
        lines.append(f"- downstream_task_type: {ctx['downstream_task_type']}")
        lines.append("")
        lines.append("### date_fields")
        lines.append("")
        lines.append("| table | column |")
        lines.append("|---|---|")
        for d in ctx.get("date_fields", []):
            lines.append(f"| {d['table']} | {d['column']} |")
        lines.append("")
        lines.append("### security_id_fields")
        lines.append("")
        lines.append("| table | column |")
        lines.append("|---|---|")
        for d in ctx.get("security_id_fields", []):
            lines.append(f"| {d['table']} | {d['column']} |")
        lines.append("")

        # 3. Key Issues From Profiler
        lines.append("## 3. Key Issues From Profiler")
        lines.append("")
        assumptions = plan["planning_assumptions"]
        for a in assumptions:
            lines.append(f"- {a}")
        lines.append("")

        # 4. Planned Workflow Steps
        lines.append("## 4. Planned Workflow Steps")
        lines.append("")
        lines.append("| step_id | name | category | priority | reason |")
        lines.append("|---|---|---|---|---|")
        for s in plan["workflow_steps"]:
            reason_short = s["reason"].replace("\n", " ")
            if len(reason_short) > 80:
                reason_short = reason_short[:77] + "..."
            lines.append(
                f"| {s['step_id']} | {s['name']} | {s['category']} | {s['priority']} | {reason_short} |"
            )
        lines.append("")
        # 每个 step 的详情
        for s in plan["workflow_steps"]:
            lines.append(f"### Step {s['step_id']}: {s['name']}")
            lines.append("")
            lines.append(f"- category: {s['category']}  |  priority: {s['priority']}  |  depends_on: {s['depends_on']}")
            lines.append(f"- input_tables: `{s['input_tables']}`")
            lines.append(f"- output_tables: `{s['output_tables']}`")
            lines.append(f"- reason: {s['reason']}")
            lines.append("- actions:")
            for a in s["actions"]:
                lines.append(f"  - {a}")
            lines.append("- risks_addressed:")
            for r in s["risks_addressed"]:
                lines.append(f"  - {r}")
            lines.append(f"- expected_output: {s['expected_output']}")
            lines.append("")

        # 5. Feature and Label Plan
        fp = plan["feature_plan"]
        lines.append("## 5. Feature and Label Plan")
        lines.append("")
        lines.append("### features")
        lines.append("")
        lines.append("| name | source | window | leakage_safe |")
        lines.append("|---|---|---|---|")
        for f in fp["features"]:
            lines.append(f"| {f['name']} | {f['source']} | {f['window']} | {f['leakage_safe']} |")
        lines.append("")
        lines.append("### label")
        lines.append("")
        lbl = fp["label"]
        lines.append(f"- name: `{lbl['name']}`")
        lines.append(f"- definition: {lbl['definition']}")
        lines.append(f"- usage: {lbl['usage']}")
        lines.append("")
        lines.append("### excluded_columns")
        lines.append("")
        for c in fp["excluded_columns"]:
            lines.append(f"- `{c}`")
        lines.append("")

        # 6. Validation Plan
        lines.append("## 6. Validation Plan")
        lines.append("")
        lines.append("| check_name | severity | description | suggested_rule |")
        lines.append("|---|---|---|---|")
        for c in plan["validation_plan"]["checks"]:
            lines.append(
                f"| {c['check_name']} | {c['severity']} | {c['description']} | `{c['suggested_rule']}` |"
            )
        lines.append("")

        # 7. Limitations
        lines.append("## 7. Limitations")
        lines.append("")
        for l in plan["limitations"]:
            lines.append(f"- {l}")
        lines.append("")

        # 8. Next Stage
        lines.append("## 8. Next Stage")
        lines.append("")
        lines.append(plan["next_stage_recommendation"])
        lines.append("")
        lines.append("Code Executor Agent 的职责：")
        lines.append("")
        lines.append("- 读取 `workflow_plan.json`")
        lines.append("- 生成 pandas 数据处理代码")
        lines.append("- 执行数据处理")
        lines.append("- 输出 `prepared_panel.csv`")
        lines.append("- 交给 Validity Critic 检查")
        lines.append("")

        return "\n".join(lines)
