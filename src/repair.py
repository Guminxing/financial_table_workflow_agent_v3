"""Remediation / Repair Loop（第五阶段）。

读取第四阶段 Critic 的 validation_report.json，针对 failed 项生成
可解释的修复方案，并执行确定性修复，输出 repaired_panel.csv。

设计原则：
- 确定性 baseline，不调用任何外部 LLM API，离线可运行。
- 不删除/重写前四阶段代码，本模块独立。
- 不训练模型、不输出投资建议、不连接真实券商系统。
- 修复后支持重新运行 Critic，形成闭环。
- 路径用 pathlib，兼容 Windows，不写死绝对路径。

v2 升级（2026-07-14）：把修复动作抽象成 **strategy registry**，并支持
PipelineRunner 调度的**有界多轮自我修正**（Observe → Decide → Act → Reflect）。
- 每个策略是一个 RepairStrategy 子类，提供 name / target_check /
  can_handle / estimated_affected_rows / risk / requires_confirmation / apply。
- 未知的 failed check 不得猜测修复，必须进入 manual review。
- 不得通过填充虚假值来"修好"数据；不得伪造或回填 announce_date；
  不得修改 label_next_5d 的标签角色；label_next_5d 永远不得进入
  approved_feature_columns。
- 原始 CSV 不得被覆盖，只能生成派生产物（repaired_panel.csv）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

REPAIR_VERSION = "0.2"

# 核心行情字段：缺失时优先删除整行（保守策略）
CORE_PRICE_COLUMNS = ["close"]

# 安全门：累计删除行数占原始 panel 的比例上限（可被 PipelineRunner 覆盖）
DEFAULT_MAX_ROW_LOSS_RATIO = 0.05


# ======================================================================
# Strategy registry
# ======================================================================


class RepairStrategy(Protocol):
    """修复策略协议。所有策略必须实现这些方法。

    策略是**确定性、可审计**的修复动作。每个策略只针对一个明确的 Critic
    failed check，且必须能在 DataFrame 副本上安全验证其效果。
    """

    @property
    def name(self) -> str:
        """策略名（snake_case，写入审计记录）。"""
        ...

    @property
    def target_check(self) -> str:
        """该策略针对的 Critic check_name。"""
        ...

    def can_handle(self, failed_check: dict[str, Any], panel: pd.DataFrame) -> bool:
        """该策略是否能处理这个 failed check（看 evidence / panel 状态）。"""
        ...

    def estimated_affected_rows(
        self, failed_check: dict[str, Any], panel: pd.DataFrame
    ) -> int:
        """预估会影响（删除/修改）多少行，用于安全门预判。"""
        ...

    @property
    def risk(self) -> str:
        """风险描述（人类可读）。"""
        ...

    @property
    def requires_confirmation(self) -> bool:
        """是否需要人工确认（True 时即使能处理也走 manual review）。"""
        ...

    def apply(
        self, panel: pd.DataFrame, failed_check: dict[str, Any]
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """在 panel 副本上执行修复，返回 (new_panel, action_detail)。

        action_detail 至少包含 strategy / target_columns / rows_removed /
        status。不得修改 label_next_5d 的值或角色，不得伪造 announce_date。
        """
        ...


class DropRowsWithMissingCorePrice:
    """删除 close 缺失行（保守策略，不默认插值）。

    target_check = missing_rate_after_join（close_missing_rate > 0 时）。
    """

    @property
    def name(self) -> str:
        return "drop_rows_with_missing_core_price"

    @property
    def target_check(self) -> str:
        return "missing_rate_after_join"

    def can_handle(self, failed_check: dict[str, Any], panel: pd.DataFrame) -> bool:
        if failed_check.get("check_name") != "missing_rate_after_join":
            return False
        ev = failed_check.get("evidence", {})
        close_rate = ev.get("close_missing_rate", 0.0)
        if not isinstance(close_rate, (int, float)):
            return False
        return float(close_rate) > 0 and "close" in panel.columns

    def estimated_affected_rows(
        self, failed_check: dict[str, Any], panel: pd.DataFrame
    ) -> int:
        if "close" not in panel.columns:
            return 0
        return int(panel["close"].isna().sum())

    @property
    def risk(self) -> str:
        return "slightly reduces sample size"

    @property
    def requires_confirmation(self) -> bool:
        return False

    def apply(
        self, panel: pd.DataFrame, failed_check: dict[str, Any]
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        cols = [c for c in CORE_PRICE_COLUMNS if c in panel.columns]
        df = panel.copy()
        removed = 0
        if cols:
            mask = df[cols].isna().any(axis=1)
            removed = int(mask.sum())
            df = df[~mask].reset_index(drop=True)
        return df, {
            "strategy": self.name,
            "target_columns": cols,
            "rows_removed": removed,
            "status": "applied",
        }


class DropExactDuplicateRows:
    """删除内容完全一致的重复行（按全部列去重）。

    target_check = primary_key_uniqueness（duplicate_count > 0 时）。
    只删除**所有列完全相同**的重复行，不删除仅主键相同但其余列不同的行
    （后者需要人工判断保留哪条，走 manual review）。
    """

    @property
    def name(self) -> str:
        return "drop_exact_duplicate_rows"

    @property
    def target_check(self) -> str:
        return "primary_key_uniqueness"

    def can_handle(self, failed_check: dict[str, Any], panel: pd.DataFrame) -> bool:
        if failed_check.get("check_name") != "primary_key_uniqueness":
            return False
        ev = failed_check.get("evidence", {})
        dup = ev.get("duplicate_count", 0)
        if not isinstance(dup, (int, float)) or int(dup) <= 0:
            return False
        # 只有"完全一致的重复行"才可安全删除
        return int(panel.duplicated(keep="first").sum()) > 0

    def estimated_affected_rows(
        self, failed_check: dict[str, Any], panel: pd.DataFrame
    ) -> int:
        return int(panel.duplicated(keep="first").sum())

    @property
    def risk(self) -> str:
        return "removes only fully-identical duplicate rows; safe"

    @property
    def requires_confirmation(self) -> bool:
        return False

    def apply(
        self, panel: pd.DataFrame, failed_check: dict[str, Any]
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        df = panel.copy()
        before = len(df)
        df = df.drop_duplicates(keep="first").reset_index(drop=True)
        removed = before - len(df)
        return df, {
            "strategy": self.name,
            "target_columns": ["__all__"],
            "rows_removed": removed,
            "status": "applied" if removed > 0 else "no_op",
        }


class TrimIndustryNameWhitespace:
    """清理 industry_name 首尾空格（dormant / manual utility）。

    target_check = source_flags_consistency（industry_name 拼写异常导致 source
    flag 不一致时）。只做 strip，不改变非空格内容，不伪造行业。

    **安全约束**：只对原本非空的字符串执行 strip；None / NaN / pd.NA 必须继续
    保持缺失，不得被 astype(str) 转成 "None"/"nan"/"<NA>"。空字符串清理后
    转为 pd.NA。

    **可达性说明**：当前 Critic 中 source_flags_consistency 是 warning 而非
    failed，而 Remediation Agent 默认只处理 failed checks，因此本策略在真实
    流程中**不会被 Agent 自动调用**。它作为 registry 中的 dormant / manual
    utility 保留，供未来 warning allowlist 或手动调用使用，不宣称会被 Agent
    自动触发。
    """

    @property
    def name(self) -> str:
        return "trim_industry_name_whitespace"

    @property
    def target_check(self) -> str:
        return "source_flags_consistency"

    @staticmethod
    def _is_missing(val: Any) -> bool:
        """判断一个标量值是否为缺失（None / NaN / pd.NA）。"""
        if val is None:
            return True
        if val is pd.NA:
            return True
        try:
            # float('nan') / numpy nan
            if isinstance(val, float) and pd.isna(val):
                return True
        except (TypeError, ValueError):
            pass
        try:
            if pd.isna(val):
                return True
        except (TypeError, ValueError):
            pass
        return False

    def can_handle(self, failed_check: dict[str, Any], panel: pd.DataFrame) -> bool:
        if failed_check.get("check_name") != "source_flags_consistency":
            return False
        if "industry_name" not in panel.columns:
            return False
        # 只有当存在"原本非空但首尾有空格"的值时才处理
        return self._has_trimable(panel["industry_name"])

    def _has_trimable(self, series: pd.Series) -> bool:
        for val in series:
            if self._is_missing(val):
                continue
            if not isinstance(val, str):
                # 非字符串非缺失：不处理（避免伪造）
                continue
            if val != val.strip() and val.strip() != "":
                return True
        return False

    def estimated_affected_rows(
        self, failed_check: dict[str, Any], panel: pd.DataFrame
    ) -> int:
        if "industry_name" not in panel.columns:
            return 0
        return int(self._count_trimable(panel["industry_name"]))

    def _count_trimable(self, series: pd.Series) -> int:
        n = 0
        for val in series:
            if self._is_missing(val):
                continue
            if not isinstance(val, str):
                continue
            if val != val.strip() and val.strip() != "":
                n += 1
        return n

    @property
    def risk(self) -> str:
        return "only strips whitespace on originally non-null strings; no value fabrication"

    @property
    def requires_confirmation(self) -> bool:
        return False

    def apply(
        self, panel: pd.DataFrame, failed_check: dict[str, Any]
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        df = panel.copy()
        changed = 0
        if "industry_name" in df.columns:
            new_vals = []
            for val in df["industry_name"]:
                if self._is_missing(val):
                    # 缺失值保持缺失，绝不转成 "None"/"nan"
                    new_vals.append(pd.NA)
                    continue
                if not isinstance(val, str):
                    # 非字符串非缺失：原样保留，不伪造
                    new_vals.append(val)
                    continue
                stripped = val.strip()
                if stripped == "":
                    # 空字符串清理后转为缺失
                    new_vals.append(pd.NA)
                    continue
                if stripped != val:
                    changed += 1
                new_vals.append(stripped)
            df["industry_name"] = new_vals
        return df, {
            "strategy": self.name,
            "target_columns": ["industry_name"],
            "rows_removed": 0,
            "rows_modified": changed,
            "status": "applied" if changed > 0 else "no_op",
        }


# 策略注册表（顺序即优先级）。PipelineRunner 每轮按此顺序匹配 failed check。
DEFAULT_STRATEGIES: list[RepairStrategy] = [
    DropRowsWithMissingCorePrice(),
    DropExactDuplicateRows(),
    TrimIndustryNameWhitespace(),
]


def list_strategies() -> list[RepairStrategy]:
    """返回默认策略列表的副本。"""
    return list(DEFAULT_STRATEGIES)


# ======================================================================
# RepairLoop（单轮修复；多轮调度由 PipelineRunner 负责）
# ======================================================================


class RepairLoop:
    """修复闭环：读 Critic 报告 → 生成修复方案 → 执行修复 → 输出 repaired panel。

    v2 仍保留单轮 build_repair_plan / apply_repairs 接口（向后兼容
    run_repair.py CLI 与旧测试），内部改用 strategy registry 选择策略。
    多轮调度（Observe → Decide → Act → Reflect）由 PipelineRunner 调用
    :meth:`select_strategies` / :meth:`apply_selected` 完成。

    用法（单轮，兼容旧 CLI）::

        loop = RepairLoop()
        loop.load_inputs(...)
        plan = loop.build_repair_plan()
        result = loop.apply_repairs(plan)
        loop.save_outputs(result, "outputs/repaired")
        loop.save_report(result, "outputs/repaired")

    用法（多轮，由 PipelineRunner 调用）::

        loop = RepairLoop(strategies=..., max_row_loss_ratio=0.05)
        loop.load_inputs(...)
        decision = loop.decide_round(validation_report, panel, rows_original)
        if decision["termination_reason"] is None:
            new_panel, actions = loop.apply_selected(panel, decision["selected"])
    """

    def __init__(
        self,
        strategies: list[RepairStrategy] | None = None,
        max_row_loss_ratio: float = DEFAULT_MAX_ROW_LOSS_RATIO,
    ) -> None:
        self.strategies = list(strategies) if strategies is not None else list(DEFAULT_STRATEGIES)
        self.max_row_loss_ratio = float(max_row_loss_ratio)
        self.panel: pd.DataFrame | None = None
        self.validation_report: dict[str, Any] = {}
        self.data_dictionary: dict[str, Any] = {}
        self.approved_features: dict[str, Any] = {}
        self.input_files: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    def load_inputs(
        self,
        panel_path: str | Path,
        validation_report_path: str | Path,
        data_dictionary_path: str | Path,
        approved_features_path: str | Path,
    ) -> None:
        """读取全部输入。"""
        self.panel = pd.read_csv(panel_path)
        if "date" in self.panel.columns:
            self.panel["date"] = pd.to_datetime(self.panel["date"], errors="coerce")
        if "announce_date" in self.panel.columns:
            self.panel["announce_date"] = pd.to_datetime(
                self.panel["announce_date"], errors="coerce"
            )
        with open(validation_report_path, "r", encoding="utf-8") as f:
            self.validation_report = json.load(f)
        with open(data_dictionary_path, "r", encoding="utf-8") as f:
            self.data_dictionary = json.load(f)
        with open(approved_features_path, "r", encoding="utf-8") as f:
            self.approved_features = json.load(f)
        self.input_files = {
            "panel": str(panel_path).replace("\\", "/"),
            "validation_report": str(validation_report_path).replace("\\", "/"),
            "data_dictionary": str(data_dictionary_path).replace("\\", "/"),
            "approved_features": str(approved_features_path).replace("\\", "/"),
        }

    # ------------------------------------------------------------------
    # 多轮调度接口（PipelineRunner 使用）
    # ------------------------------------------------------------------

    @staticmethod
    def failed_checks_of(report: dict[str, Any]) -> list[dict[str, Any]]:
        """从 validation_report 提取 failed check 列表（精简字段）。"""
        return [
            {
                "check_name": c["check_name"],
                "category": c.get("category"),
                "severity": c.get("severity"),
                "status": c.get("status"),
                "description": c.get("description"),
                "evidence": c.get("evidence"),
                "recommendation": c.get("recommendation"),
            }
            for c in report.get("checks", [])
            if c.get("status") == "failed"
        ]

    @staticmethod
    def panel_fingerprint(panel: pd.DataFrame) -> str:
        """规范化 DataFrame 内容指纹：列顺序 + 行数 + 内容哈希。

        用于 no_progress 判断：若两轮的 failed check 集合与 panel 指纹
        都不变，则停止（禁止无限循环）。
        """
        import hashlib

        df = panel.copy()
        # 把日期列转成统一字符串，避免 dtype 差异导致哈希不稳
        for col in ("date", "announce_date"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")
        cols = list(df.columns)
        try:
            content = df.to_csv(index=False)
        except Exception:  # noqa: BLE001
            content = repr(df.values.tolist())
        h = hashlib.sha256()
        h.update(str(cols).encode("utf-8"))
        h.update(b"|")
        h.update(str(len(df)).encode("utf-8"))
        h.update(b"|")
        h.update(content.encode("utf-8"))
        return h.hexdigest()[:16]

    def decide_round(
        self,
        validation_report: dict[str, Any],
        panel: pd.DataFrame,
        rows_original: int,
        cumulative_removed: int = 0,
    ) -> dict[str, Any]:
        """Observe + Decide：为本轮选择可执行策略，或给出 termination_reason。

        返回 dict 含：
        - candidate_strategies: 所有匹配到的候选策略描述
        - selected_strategies: 通过安全门、可执行策略描述
        - termination_reason: 若非 None 则本轮不执行修复，直接停止
        - decision_reason: 人类可读决策理由
        - manual_review_required: bool
        - unresolved_checks: 未能自动处理的 failed check 名
        """
        failed_checks = self.failed_checks_of(validation_report)
        overall = validation_report.get("overall_status", "unknown")

        # validation_passed：没有 failed check
        if overall != "failed" and not failed_checks:
            return {
                "candidate_strategies": [],
                "selected_strategies": [],
                "termination_reason": "validation_passed",
                "decision_reason": (
                    f"overall_status={overall}; no failed checks; no repair needed"
                ),
                "manual_review_required": False,
                "unresolved_checks": [],
            }

        # 候选策略：对每个 failed check 找到能 handle 的策略
        candidates: list[dict[str, Any]] = []
        selected: list[dict[str, Any]] = []
        unresolved: list[str] = []
        blocked_by_safety: list[str] = []

        for fc in failed_checks:
            handled = False
            for strat in self.strategies:
                if not strat.can_handle(fc, panel):
                    continue
                est = strat.estimated_affected_rows(fc, panel)
                cand = {
                    "strategy": strat.name,
                    "target_check": strat.target_check,
                    "failed_check": fc["check_name"],
                    "estimated_affected_rows": est,
                    "risk": strat.risk,
                    "requires_confirmation": strat.requires_confirmation,
                }
                candidates.append(cand)
                handled = True

                # 安全门：预估删除行数 + 累计删除行数 不得超过阈值
                if strat.requires_confirmation:
                    # 策略本身要求人工确认 → 不自动执行
                    blocked_by_safety.append(
                        f"{strat.name} requires_confirmation=True"
                    )
                    continue
                projected_removed = cumulative_removed + est
                rows_after = rows_original - projected_removed
                if rows_original > 0:
                    projected_ratio = projected_removed / rows_original
                else:
                    projected_ratio = 0.0
                if projected_ratio > self.max_row_loss_ratio + 1e-9:
                    blocked_by_safety.append(
                        f"{strat.name} projected cumulative row loss "
                        f"{projected_ratio:.4f} > {self.max_row_loss_ratio:.4f}"
                    )
                    continue
                selected.append(cand)
            if not handled:
                # 没有任何策略能处理这个 failed check → manual review
                unresolved.append(fc["check_name"])

        # 决策
        if not candidates and unresolved:
            return {
                "candidate_strategies": [],
                "selected_strategies": [],
                "termination_reason": "no_actionable_strategy",
                "decision_reason": (
                    f"no strategy can handle failed checks: {unresolved}; "
                    "manual review required"
                ),
                "manual_review_required": True,
                "unresolved_checks": unresolved,
            }

        if candidates and not selected:
            # 有候选但全部被安全门挡下 → manual_review_required
            return {
                "candidate_strategies": candidates,
                "selected_strategies": [],
                "termination_reason": "manual_review_required",
                "decision_reason": (
                    "candidate strategies exist but all blocked by safety gate: "
                    f"{blocked_by_safety}"
                ),
                "manual_review_required": True,
                "unresolved_checks": unresolved,
            }

        return {
            "candidate_strategies": candidates,
            "selected_strategies": selected,
            "termination_reason": None,
            "decision_reason": (
                f"selected {len(selected)} strategy(ies) to apply this round"
            ),
            "manual_review_required": bool(unresolved),
            "unresolved_checks": unresolved,
        }

    def apply_selected(
        self,
        panel: pd.DataFrame,
        selected: list[dict[str, Any]],
    ) -> tuple[pd.DataFrame, list[dict[str, Any]], int]:
        """Act：在 panel 副本上依次执行 selected 策略，返回 (new_panel, actions, rows_removed)。

        执行后再次在副本上复核实际删除行数；若实际累计超过安全门阈值，
        **不保存**该结果（返回的 new_panel 仍为修复后副本，但调用方应据
        rows_removed 判定是否回滚——PipelineRunner 会在外层用实际行数
        复核并决定 termination_reason）。
        """
        df = panel.copy()
        actions: list[dict[str, Any]] = []
        rows_removed = 0
        for sel in selected:
            strat = next(
                (s for s in self.strategies if s.name == sel["strategy"]), None
            )
            if strat is None:
                actions.append({
                    "strategy": sel["strategy"],
                    "status": "skipped",
                    "reason": "strategy not found in registry",
                })
                continue
            before = len(df)
            df, detail = strat.apply(df, {"check_name": sel["target_check"]})
            removed = int(detail.get("rows_removed", 0))
            rows_removed += removed
            actions.append({
                "strategy": strat.name,
                "target_check": strat.target_check,
                "target_columns": detail.get("target_columns", []),
                "rows_removed": removed,
                "rows_modified": detail.get("rows_modified", 0),
                "status": detail.get("status", "applied"),
            })
        return df, actions, rows_removed

    # ------------------------------------------------------------------
    # 单轮接口（向后兼容 run_repair.py CLI 与旧测试）
    # ------------------------------------------------------------------

    def build_repair_plan(self) -> dict[str, Any]:
        """根据 Critic 的 failed/warning 项生成修复方案（单轮，兼容旧接口）。"""
        if self.panel is None:
            raise RuntimeError("call load_inputs() first")

        failed_checks = self.failed_checks_of(self.validation_report)
        warning_checks = [
            {
                "check_name": c["check_name"],
                "status": c["status"],
                "evidence": c.get("evidence"),
            }
            for c in self.validation_report.get("checks", [])
            if c.get("status") == "warning"
        ]

        repair_actions: list[dict[str, Any]] = []
        not_repaired: list[dict[str, Any]] = []
        action_id = 1

        # 用 strategy registry 为每个 failed check 选策略
        for fc in failed_checks:
            matched = None
            for strat in self.strategies:
                if strat.can_handle(fc, self.panel):
                    matched = strat
                    break
            if matched is not None:
                est = matched.estimated_affected_rows(fc, self.panel)
                repair_actions.append(
                    {
                        "action_id": action_id,
                        "target_check": matched.target_check,
                        "target_columns": (
                            ["close"] if matched.name == "drop_rows_with_missing_core_price"
                            else (["__all__"] if matched.name == "drop_exact_duplicate_rows"
                                  else ["industry_name"])
                        ),
                        "issue": fc.get("description", ""),
                        "strategy": matched.name,
                        "reason": self._strategy_reason(matched.name, fc),
                        "affected_rows_before": est,
                        "expected_effect": (
                            "remove rows failing the check; deterministic, no imputation"
                        ),
                        "risk": matched.risk,
                        "requires_manual_confirmation": matched.requires_confirmation,
                    }
                )
                action_id += 1
            else:
                not_repaired.append(
                    {
                        "item": fc["check_name"],
                        "reason": (
                            "no automatic repair strategy implemented for this check; "
                            "requires manual review"
                        ),
                        "evidence": fc.get("evidence"),
                    }
                )

        # warning 检查：记录但不强制修复
        for c in warning_checks:
            not_repaired.append(
                {
                    "item": c["check_name"],
                    "reason": "warning-level; recorded but not auto-repaired in baseline",
                    "evidence": c.get("evidence"),
                }
            )

        return {
            "project": "financial_table_workflow_agent",
            "repair_version": REPAIR_VERSION,
            "input_validation_status": self.validation_report.get("overall_status", "unknown"),
            "failed_checks": failed_checks,
            "warning_checks": warning_checks,
            "repair_actions": repair_actions,
            "not_repaired_items": not_repaired,
            "next_validation_required": True,
        }

    @staticmethod
    def _strategy_reason(name: str, fc: dict[str, Any]) -> str:
        if name == "drop_rows_with_missing_core_price":
            return (
                "close is required for return features (return_1d/return_5d/"
                "volatility_20d) and label (label_next_5d); rows with missing "
                "close cannot be safely used for supervised modeling"
            )
        if name == "drop_exact_duplicate_rows":
            return (
                "fully-identical duplicate rows carry no information and break "
                "primary key uniqueness; safe to drop (keep first)"
            )
        if name == "trim_industry_name_whitespace":
            return (
                "industry_name with leading/trailing whitespace causes source flag "
                "mismatch; stripping whitespace is safe and does not fabricate values"
            )
        return ""

    def apply_repairs(self, plan: dict[str, Any]) -> dict[str, Any]:
        """执行修复方案，返回结果 dict（含 repaired panel 与 log）。"""
        if self.panel is None:
            raise RuntimeError("call load_inputs() first")

        df = self.panel.copy()
        rows_before = len(df)
        actions_applied: list[dict[str, Any]] = []
        warnings: list[str] = []

        for action in plan["repair_actions"]:
            strat = next(
                (s for s in self.strategies if s.name == action["strategy"]), None
            )
            if strat is None:
                warnings.append(
                    f"action {action['action_id']}: unknown strategy "
                    f"{action['strategy']}, skipped"
                )
                continue
            before = len(df)
            df, detail = strat.apply(df, {"check_name": action["target_check"]})
            actions_applied.append({
                "action_id": action["action_id"],
                "strategy": action["strategy"],
                "target_columns": detail.get("target_columns", action.get("target_columns", [])),
                "rows_removed": int(detail.get("rows_removed", 0)),
                "rows_modified": int(detail.get("rows_modified", 0)),
                "status": detail.get("status", "applied"),
            })

        rows_after = len(df)

        # 修复后自检
        checks_after = self._post_repair_checks(df)

        log = {
            "project": "financial_table_workflow_agent",
            "repair_version": REPAIR_VERSION,
            "input_panel_path": self.input_files["panel"],
            "input_validation_report_path": self.input_files["validation_report"],
            "rows_before": rows_before,
            "rows_after": rows_after,
            "rows_removed": rows_before - rows_after,
            "actions_applied": actions_applied,
            "checks_after_repair": checks_after,
            "warnings": warnings,
            "next_step": "rerun_validity_critic_on_repaired_panel",
        }

        return {
            "repair_plan": plan,
            "repaired_panel": df,
            "repair_log": log,
        }

    def save_outputs(self, result: dict[str, Any], output_dir: str | Path) -> dict[str, Path]:
        """保存 repair_plan.json / repaired_panel.csv / repair_log.json。"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        plan_path = out / "repair_plan.json"
        with plan_path.open("w", encoding="utf-8") as f:
            json.dump(result["repair_plan"], f, ensure_ascii=False, indent=2)

        panel: pd.DataFrame = result["repaired_panel"]
        csv_path = out / "repaired_panel.csv"
        panel_to_write = panel.copy()
        if "date" in panel_to_write.columns:
            panel_to_write["date"] = pd.to_datetime(
                panel_to_write["date"]
            ).dt.strftime("%Y-%m-%d")
        if "announce_date" in panel_to_write.columns:
            # announce_date 可能含 NaT
            ad = pd.to_datetime(panel_to_write["announce_date"], errors="coerce")
            panel_to_write["announce_date"] = ad.dt.strftime("%Y-%m-%d")
        panel_to_write.to_csv(csv_path, index=False, encoding="utf-8-sig")

        log_path = out / "repair_log.json"
        with log_path.open("w", encoding="utf-8") as f:
            json.dump(result["repair_log"], f, ensure_ascii=False, indent=2)

        return {"plan": plan_path, "panel": csv_path, "log": log_path}

    def save_report(self, result: dict[str, Any], output_dir: str | Path) -> Path:
        """生成并保存 repair_report.md。"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        md_path = out / "repair_report.md"
        md_path.write_text(self._render_report(result), encoding="utf-8")
        return md_path

    # ------------------------------------------------------------------
    # 修复后自检
    # ------------------------------------------------------------------

    def _post_repair_checks(self, df: pd.DataFrame) -> dict[str, Any]:
        close_missing = int(df["close"].isna().sum()) if "close" in df.columns else -1
        pk_dup = int(df.duplicated(subset=["date", "ticker"]).sum()) if all(
            c in df.columns for c in ["date", "ticker"]
        ) else -1
        label_preserved = "label_next_5d" in df.columns
        # approved features 仍不含 label
        approved = self.approved_features.get("approved_feature_columns", [])
        label_not_in_features = "label_next_5d" not in approved
        return {
            "close_missing_count": close_missing,
            "primary_key_unique": pk_dup == 0,
            "primary_key_duplicate_count": pk_dup,
            "label_column_preserved": label_preserved,
            "label_not_in_approved_features": label_not_in_features,
            "approved_feature_columns_unchanged": approved,
        }

    # ------------------------------------------------------------------
    # Markdown 报告
    # ------------------------------------------------------------------

    def _render_report(self, result: dict[str, Any]) -> str:
        plan = result["repair_plan"]
        log = result["repair_log"]
        lines: list[str] = []
        lines.append("# Repair Loop Report")
        lines.append("")
        lines.append(
            f"- project: `{plan['project']}`  |  repair_version: `{plan['repair_version']}`"
        )
        lines.append("")

        # 1. Why Repair Was Needed
        lines.append("## 1. Why Repair Was Needed")
        lines.append("")
        lines.append(
            f"The Validity Critic reported `overall_status = {plan['input_validation_status']}`. "
            "Failing checks were addressed by deterministic strategies from the repair "
            "strategy registry. No values are fabricated; rows may be dropped conservatively."
        )
        lines.append("")

        # 2. Failed Checks From Critic
        lines.append("## 2. Failed Checks From Critic")
        lines.append("")
        if plan["failed_checks"]:
            lines.append("| check_name | category | description | evidence |")
            lines.append("|---|---|---|---|")
            for c in plan["failed_checks"]:
                ev = c.get("evidence", {})
                ev_str = ", ".join(f"{k}={v}" for k, v in ev.items())
                lines.append(
                    f"| {c['check_name']} | {c.get('category')} | {c.get('description')} | {ev_str} |"
                )
        else:
            lines.append("(none)")
        lines.append("")

        # 3. Repair Strategy
        lines.append("## 3. Repair Strategy")
        lines.append("")
        lines.append("Strategies are selected from a registry; each targets one Critic check.")
        lines.append("")
        if plan["repair_actions"]:
            lines.append("| action_id | strategy | target_check | target_columns | risk |")
            lines.append("|---|---|---|---|---|")
            for a in plan["repair_actions"]:
                lines.append(
                    f"| {a['action_id']} | {a['strategy']} | {a['target_check']} | "
                    f"{a.get('target_columns')} | {a.get('risk')} |"
                )
        else:
            lines.append("(no automatic repair actions; all failed checks need manual review)")
        lines.append("")
        if plan["not_repaired_items"]:
            lines.append("Items not repaired (by design):")
            lines.append("")
            for it in plan["not_repaired_items"]:
                lines.append(f"- `{it['item']}` — {it['reason']}")
            lines.append("")

        # 4. Repair Result
        lines.append("## 4. Repair Result")
        lines.append("")
        lines.append(f"- rows before: {log['rows_before']}")
        lines.append(f"- rows after: {log['rows_after']}")
        lines.append(f"- rows removed: {log['rows_removed']}")
        chk = log["checks_after_repair"]
        lines.append(f"- close missing count after repair: {chk['close_missing_count']}")
        lines.append(f"- primary key unique after repair: {chk['primary_key_unique']}")
        lines.append(f"- label column preserved: {chk['label_column_preserved']}")
        lines.append(f"- label not in approved features: {chk['label_not_in_approved_features']}")
        lines.append("")
        lines.append("Actions applied:")
        lines.append("")
        lines.append("| strategy | target_columns | rows_removed | status |")
        lines.append("|---|---|---|---|")
        for a in log["actions_applied"]:
            lines.append(
                f"| {a['strategy']} | {a.get('target_columns')} | "
                f"{a.get('rows_removed')} | {a.get('status')} |"
            )
        lines.append("")

        # 5. Limitations
        lines.append("## 5. Limitations")
        lines.append("")
        lines.append("- This is a **deterministic baseline repair**; no LLM is called.")
        lines.append("- Unknown failed checks are routed to manual review, never guessed.")
        lines.append("- No announce_date is fabricated or backfilled; label role is never changed.")
        lines.append("- No model is trained in this stage; no investment advice is produced.")
        lines.append("")

        # 6. Next Step
        lines.append("## 6. Next Step")
        lines.append("")
        lines.append("Re-run the Validity Critic on the repaired panel to confirm the failure is resolved:")
        lines.append("")
        lines.append("```bash")
        lines.append(
            "python src/run_critic.py --panel_path outputs/repaired/repaired_panel.csv "
            "--data_dictionary_path outputs/prepared/data_dictionary.json "
            "--execution_log_path outputs/prepared/execution_log.json "
            "--plan_path outputs/plans/workflow_plan.json "
            "--executor_source_path src/executor.py "
            "--calendar_path data/sample/calendar.csv "
            "--output_dir outputs/validation_repaired"
        )
        lines.append("```")
        lines.append("")

        return "\n".join(lines)
