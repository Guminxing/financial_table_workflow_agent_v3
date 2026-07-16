"""PipelineRunner（第七阶段）：统一调度器。

把原来需要手动执行的一长串脚本（run_profile / run_planner / run_executor /
run_critic / run_repair / run_critic / run_report_generator）封装成一个可编程的
调度器，供 run_all.py（一键运行）与 agent_shell.py（交互式 shell）复用。

设计原则：
- 不删除/重写前六阶段代码，本模块只**复用**它们的内部类（FinancialTableProfiler /
  WorkflowPlanner / CodeExecutor / ValidityCritic / RepairLoop / ReportGenerator）。
- 不调用任何外部 LLM API，离线可运行。
- 不连接真实券商系统，不获取真实市场数据，不训练模型，不输出投资建议。
- 路径用 pathlib，兼容 Windows，不写死绝对路径。
- 每个阶段运行后记录 status / start_time / end_time / duration / output_files /
  summary / error_message；失败不静默吞掉。
- 生成 outputs/sessions/latest_session.json 与 session_YYYYMMDD_HHMMSS.json。
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

# 让脚本无论从哪里调用都能 import 同级模块
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from critic import ValidityCritic  # noqa: E402
from executor import CodeExecutor  # noqa: E402
from planner import DEFAULT_ANALYSIS_GOAL, WorkflowPlanner  # noqa: E402
from profiler import FinancialTableProfiler  # noqa: E402
from repair import REPAIR_VERSION, RepairLoop  # noqa: E402
from report_generator import ReportGenerator  # noqa: E402

RUNNER_VERSION = "0.2"

# 阶段顺序与展示名
STAGE_ORDER = [
    "profile",
    "planner",
    "executor",
    "initial_critic",
    "repair",
    "repaired_critic",
    "final_report",
]

STAGE_DISPLAY = {
    "profile": "Stage 1 Data Profiler",
    "planner": "Stage 2 Workflow Planner",
    "executor": "Stage 3 Code Executor",
    "initial_critic": "Stage 4 Validity Critic",
    "repair": "Stage 5 Repair Loop",
    "repaired_critic": "Stage 6 Re-run Critic",
    "final_report": "Stage 7 Final Report",
}

# Remediation Agent 默认参数
DEFAULT_MAX_REPAIR_ROUNDS = 3
DEFAULT_MAX_ROW_LOSS_RATIO = 0.05


# 可注入的 Critic 工厂类型（测试用）；签名 () -> ValidityCritic-like 对象
CriticFactory = Any


class PipelineRunner:
    """统一调度器：复用前六阶段内部类，按顺序运行并记录状态。

    用法::

        runner = PipelineRunner(
            input_dir="data/real_market",
            output_root="outputs_real",
            analysis_goal=None,
            auto_repair=True,
        )
        runner.run_full_pipeline()
        status = runner.get_status()
        runner.save_session_log()
    """

    def __init__(
        self,
        input_dir: str | Path = "data/real_market",
        output_root: str | Path = "outputs_real",
        analysis_goal: str | None = None,
        auto_repair: bool = True,
        skip_report: bool = False,
        verbose: bool = False,
        max_repair_rounds: int = DEFAULT_MAX_REPAIR_ROUNDS,
        max_row_loss_ratio: float = DEFAULT_MAX_ROW_LOSS_RATIO,
    ) -> None:
        self.input_dir = Path(input_dir)
        self.output_root = Path(output_root)
        self.analysis_goal = analysis_goal or DEFAULT_ANALYSIS_GOAL
        self.auto_repair = auto_repair
        self.skip_report = skip_report
        self.verbose = verbose
        self.max_repair_rounds = int(max_repair_rounds)
        self.max_row_loss_ratio = float(max_row_loss_ratio)

        # Remediation Agent 运行结果（多轮闭环）。
        # 内存字段标记"本次运行是否已产生状态"；get_status 时若内存无状态且
        # repair_history.json 存在，则从磁盘恢复历史状态（见 _load_repair_state）。
        self.repair_history: list[dict[str, Any]] = []
        self.repair_rounds_run: int | None = None
        self.termination_reason: str | None = None
        self.manual_review_required: bool | None = None
        self.unresolved_checks: list[str] | None = None
        self._has_run_remediation: bool = False
        # 可注入的 Critic 工厂（测试用）；为 None 时用真实 ValidityCritic。
        self._critic_factory: CriticFactory | None = None

        # 各阶段输出目录（与前六阶段默认保持一致）
        self.profiles_dir = self.output_root / "profiles"
        self.plans_dir = self.output_root / "plans"
        self.prepared_dir = self.output_root / "prepared"
        self.validation_dir = self.output_root / "validation"
        self.repaired_dir = self.output_root / "repaired"
        self.validation_repaired_dir = self.output_root / "validation_repaired"
        self.final_report_dir = self.output_root / "final_report"
        self.sessions_dir = self.output_root / "sessions"

        # 关键产物路径（供后续阶段与 shell 读取）
        self.profile_json = self.profiles_dir / "profile.json"
        self.profile_md = self.profiles_dir / "profile_report.md"
        self.plan_json = self.plans_dir / "workflow_plan.json"
        self.plan_md = self.plans_dir / "workflow_plan_report.md"
        self.prepared_panel = self.prepared_dir / "prepared_panel.csv"
        self.data_dictionary = self.prepared_dir / "data_dictionary.json"
        self.execution_log = self.prepared_dir / "execution_log.json"
        self.execution_report = self.prepared_dir / "execution_report.md"
        self.initial_validation_json = self.validation_dir / "validation_report.json"
        self.initial_validation_md = self.validation_dir / "validation_report.md"
        self.initial_approved = self.validation_dir / "approved_feature_columns.json"
        self.repair_plan = self.repaired_dir / "repair_plan.json"
        self.repaired_panel = self.repaired_dir / "repaired_panel.csv"
        self.repair_log = self.repaired_dir / "repair_log.json"
        self.repair_report = self.repaired_dir / "repair_report.md"
        self.repair_history_json = self.repaired_dir / "repair_history.json"
        self.final_validation_json = (
            self.validation_repaired_dir / "validation_report.json"
        )
        self.final_validation_md = (
            self.validation_repaired_dir / "validation_report.md"
        )
        self.final_approved = (
            self.validation_repaired_dir / "approved_feature_columns.json"
        )
        self.summary_json = self.final_report_dir / "final_workflow_summary.json"
        self.full_report_md = self.final_report_dir / "final_workflow_report.md"
        self.one_page_md = self.final_report_dir / "final_workflow_one_page.md"
        self.artifacts_index = (
            self.final_report_dir / "pipeline_artifacts_index.json"
        )

        # executor.py 源码路径（Critic 静态检查需要）
        self.executor_source = HERE / "executor.py"
        self.calendar_csv = self.input_dir / "calendar.csv"

        # 阶段状态记录
        self.stages: dict[str, dict[str, Any]] = {
            s: self._fresh_stage_record(s) for s in STAGE_ORDER
        }
        self._final_summary_cache: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # 公共：单阶段运行
    # ------------------------------------------------------------------

    def run_profile(self) -> dict[str, Any]:
        """Stage 1: Data Profiler。"""
        return self._run_stage("profile", self._profile_impl)

    def run_planner(self) -> dict[str, Any]:
        """Stage 2: Workflow Planner。"""
        return self._run_stage("planner", self._planner_impl)

    def run_executor(self) -> dict[str, Any]:
        """Stage 3: Code Executor。"""
        return self._run_stage("executor", self._executor_impl)

    def run_initial_critic(self) -> dict[str, Any]:
        """Stage 4: Validity Critic（初始）。"""
        return self._run_stage("initial_critic", self._initial_critic_impl)

    def run_repair(self) -> dict[str, Any]:
        """Stage 5: Repair Loop。"""
        return self._run_stage("repair", self._repair_impl)

    def run_remediation_agent(self) -> dict[str, Any]:
        """Stage 5（多轮）：有界 Remediation Agent 的薄公开入口。

        本方法是 Stage 9 Agent Runtime 引入的**薄包装**，只负责：
        - 检查前置条件（initial critic 已运行且产物存在）；
        - 委托现有私有实现 :meth:`_run_remediation_agent`；
        - 返回标准 ``repair`` 阶段记录。

        **不**复制多轮修复逻辑；所有 Observe/Decide/Act/Reflect/Stop 仍在
        :meth:`_remediation_agent_loop` 中。Agent 工具通过本方法触发有界修复闭环，
        复用既有安全门（max_row_loss_ratio / no_progress / manual_review_required /
        unresolved_checks / label 泄漏保护）。

        前置条件：
        - ``initial_critic`` 阶段已运行（否则 initial validation 不存在）；
        - prepared_panel / initial_validation_json / data_dictionary /
          initial_approved 存在（_remediation_agent_loop 内部也会校验）。

        返回 ``self.stages["repair"]`` 记录（与 run_repair 一致的阶段记录结构）。
        """
        # 前置条件：initial critic 必须已运行
        init_rec = self.stages.get("initial_critic", {})
        if init_rec.get("status") in (None, "pending"):
            raise RuntimeError(
                "run_remediation_agent: initial_critic has not run yet; "
                "run validate_financial_panel / run_initial_critic first."
            )
        # 前置产物存在性（_remediation_agent_loop 内部也会校验，这里提前给出清晰错误）
        for label, path in [
            ("panel", self.prepared_panel),
            ("validation_report", self.initial_validation_json),
            ("data_dictionary", self.data_dictionary),
            ("approved_features", self.initial_approved),
        ]:
            if not Path(path).exists():
                raise FileNotFoundError(
                    f"run_remediation_agent: {label} not found: {path}"
                )
        # 委托现有私有实现（含异常捕获、repair_history.json 写入）
        self._run_remediation_agent()
        return self.stages["repair"]

    def run_noop_repair(
        self,
        initial_status: str | None = None,
        no_op_kind: str = "no_repair_needed",
    ) -> dict[str, Any]:
        """Stage 5（no-op）：无需实际 Repair 时生成统一 no-op 产物。

        本方法是 Stage 10 引入的**薄公开入口**，封装 :meth:`run_full_pipeline`
        中"initial critic 未 failed / auto_repair=False"分支的 no-op 逻辑，
        让 Agent 工具（``run_safe_remediation`` 的 not_needed 分支）只走公开 API，
        不再触碰 ``_write_noop_repair_artifacts`` / ``_write_repair_history`` /
        ``_mark_skipped`` 等私有方法。

        前置条件：``initial_critic`` 阶段已运行（否则 initial validation 不存在）。

        参数：
        - ``initial_status``: initial critic 的 overall_status；为 None 时从
          ``initial_validation.json`` 读取。
        - ``no_op_kind``: ``no_repair_needed``（initial 未 failed）或
          ``repair_disabled``（initial failed 但 --no_repair）。

        行为（与 run_full_pipeline 的 else 分支完全一致）：
        - mark_skipped repair / repaired_critic；
        - 写 no-op 产物（repaired_panel / repair_plan / repair_log / 复审 validation）；
        - 按 no_op_kind 设置 termination_reason / manual_review_required /
          unresolved_checks / repair_rounds_run=0 / _has_run_remediation=True；
        - 写 repair_history.json（0 轮）。

        返回 ``self.stages["repair"]`` 记录。
        """
        # 前置条件：initial critic 必须已运行
        init_rec = self.stages.get("initial_critic", {})
        if init_rec.get("status") in (None, "pending"):
            raise RuntimeError(
                "run_noop_repair: initial_critic has not run yet; "
                "run validate_financial_panel / run_initial_critic first."
            )
        if initial_status is None:
            initial_status = init_rec.get("summary", {}).get(
                "overall_status", "unknown"
            )

        self._mark_skipped("repair", reason=self._skip_repair_reason(initial_status))
        self._mark_skipped(
            "repaired_critic", reason="repair skipped; no re-critic needed"
        )
        self._write_noop_repair_artifacts(initial_status, no_op_kind)
        # no-op 场景也写 repair_history.json（0 轮），保证审计文件始终存在
        self.repair_rounds_run = 0
        if no_op_kind == "repair_disabled":
            # initial critic failed 但 --no_repair：最终仍 failed，
            # unresolved_checks 记录初始 failed check 名，manual_review_required=True
            self.termination_reason = "repair_disabled"
            init_failed = self._read_failed_check_names(self.initial_validation_json)
            self.unresolved_checks = init_failed
            self.manual_review_required = bool(init_failed)
        else:
            self.termination_reason = "validation_passed"
            self.manual_review_required = False
            self.unresolved_checks = []
        self._has_run_remediation = True
        self._write_repair_history(
            rounds=[],
            termination_reason=self.termination_reason,
            manual_review_required=bool(self.manual_review_required),
            unresolved_checks=list(self.unresolved_checks),
        )
        return self.stages["repair"]

    def run_repaired_critic(self) -> dict[str, Any]:
        """Stage 6: 对 repaired panel 重新运行 Critic。"""
        return self._run_stage("repaired_critic", self._repaired_critic_impl)

    def run_final_report(self) -> dict[str, Any]:
        """Stage 7: Final Report Generator。"""
        return self._run_stage("final_report", self._final_report_impl)

    # ------------------------------------------------------------------
    # 公共：完整 pipeline
    # ------------------------------------------------------------------

    def run_full_pipeline(self) -> dict[str, Any]:
        """一键运行完整 workflow，含 auto_repair 与 skip_report 逻辑。"""
        self._log("Financial Table Workflow Agent — full pipeline start")
        self._log(f"Input dir: {self.input_dir}")
        self._log(f"Output root: {self.output_root}")
        self._log(f"Analysis goal: {self.analysis_goal}")
        self._log(f"Auto repair: {self.auto_repair}")
        self._log(f"Skip report: {self.skip_report}")

        # 1. profile
        self.run_profile()
        if self.stages["profile"]["status"] == "failed":
            self._fail_fast("profile")
            return self.get_status()

        # 2. planner
        self.run_planner()
        if self.stages["planner"]["status"] == "failed":
            self._fail_fast("planner")
            return self.get_status()

        # 3. executor
        self.run_executor()
        if self.stages["executor"]["status"] == "failed":
            self._fail_fast("executor")
            return self.get_status()

        # 4. initial critic
        self.run_initial_critic()
        if self.stages["initial_critic"]["status"] == "failed":
            self._fail_fast("initial_critic")
            return self.get_status()

        initial_status = self.stages["initial_critic"]["summary"].get(
            "overall_status", "unknown"
        )

        # 5. repair（仅当 initial critic failed 且 auto_repair=True）
        if initial_status == "failed" and self.auto_repair:
            # v2：有界多轮 Remediation Agent（Observe → Decide → Act → Reflect）
            self._run_remediation_agent()
            if self.stages["repair"]["status"] == "failed":
                self._fail_fast("repair")
                return self.get_status()
            # 6. repaired critic（最后一轮已在内层跑过，这里补一次正式阶段记录）
            self.run_repaired_critic()
            if self.stages["repaired_critic"]["status"] == "failed":
                self._fail_fast("repaired_critic")
                return self.get_status()
        else:
            # 跳过实际 repair 与 repaired critic，但生成统一 no-op 产物，
            # 让 final_report 阶段的输入全部存在。区分两种 no-op：
            #   - no_repair_needed: initial critic 未失败（passed/passed_with_warnings）
            #   - repair_disabled:  initial critic failed 但 --no_repair（最终仍 failed）
            if initial_status == "failed" and not self.auto_repair:
                no_op_kind = "repair_disabled"
            else:
                no_op_kind = "no_repair_needed"
            self.run_noop_repair(initial_status, no_op_kind)

        # 7. final report
        if not self.skip_report:
            self.run_final_report()
            if self.stages["final_report"]["status"] == "failed":
                self._fail_fast("final_report")
                return self.get_status()
        else:
            self._mark_skipped("final_report", reason="--skip_report set")

        self._log("Full pipeline finished.")
        return self.get_status()

    # ------------------------------------------------------------------
    # 公共：状态与 session log
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """返回当前 pipeline 状态快照。"""
        final_status = self._read_final_validation_status()
        initial_status = self._read_initial_validation_status()
        rows_prepared = self._count_rows(self.prepared_panel)
        rows_repaired = self._count_rows(self.repaired_panel)
        rows_removed = self._read_rows_removed()

        approved, label_col, label_in_features = self._read_approved_features()

        # Remediation Agent 状态：本次运行内存状态优先；若本次未运行且
        # repair_history.json 存在，则从磁盘恢复历史状态。
        rounds, term, manual, unresolved = self._resolve_repair_state()

        return {
            "project": "financial_table_workflow_agent",
            "runner_version": RUNNER_VERSION,
            "input_dir": str(self.input_dir).replace("\\", "/"),
            "output_root": str(self.output_root).replace("\\", "/"),
            "analysis_goal": self.analysis_goal,
            "auto_repair": self.auto_repair,
            "skip_report": self.skip_report,
            "stages": {s: dict(self.stages[s]) for s in STAGE_ORDER},
            "initial_validation_status": initial_status,
            "final_validation_status": final_status,
            "prepared_panel_rows": rows_prepared,
            "repaired_panel_rows": rows_repaired,
            "rows_removed_by_repair": rows_removed,
            "failed_checks_initial": self._read_failed_count(
                self.initial_validation_json
            ),
            "failed_checks_final": self._read_failed_count(
                self.final_validation_json
            ),
            "approved_feature_columns": approved,
            "label_column": label_col,
            "label_in_approved_features": label_in_features,
            "repair_rounds": rounds,
            "termination_reason": term,
            "manual_review_required": manual,
            "unresolved_checks": unresolved,
            "final_report_path": (
                str(self.full_report_md).replace("\\", "/")
                if self.full_report_md.exists()
                else None
            ),
            "one_page_path": (
                str(self.one_page_md).replace("\\", "/")
                if self.one_page_md.exists()
                else None
            ),
            "session_log_path": (
                str(self.sessions_dir / "latest_session.json").replace("\\", "/")
            ),
        }

    def _resolve_repair_state(
        self,
    ) -> tuple[int | None, str | None, bool | None, list[str]]:
        """返回 (repair_rounds, termination_reason, manual_review_required, unresolved_checks)。

        优先级：本次运行内存状态 > repair_history.json 磁盘历史 > 默认空值。
        """
        if self._has_run_remediation:
            return (
                self.repair_rounds_run,
                self.termination_reason,
                self.manual_review_required,
                list(self.unresolved_checks or []),
            )
        # 本次未运行：尝试从磁盘恢复
        disk = self._load_repair_state_from_disk()
        if disk is not None:
            return disk
        return (0, None, False, [])

    def _load_repair_state_from_disk(
        self,
    ) -> tuple[int, str, bool, list[str]] | None:
        """从 repair_history.json 读取历史 Remediation Agent 状态。"""
        if not self.repair_history_json.exists():
            return None
        try:
            with self.repair_history_json.open("r", encoding="utf-8") as f:
                rh = json.load(f)
            return (
                int(rh.get("repair_rounds", 0)),
                rh.get("termination_reason"),
                bool(rh.get("manual_review_required", False)),
                list(rh.get("unresolved_checks", [])),
            )
        except Exception:  # noqa: BLE001
            return None

    def save_session_log(self) -> Path:
        """保存 session log：latest_session.json + session_YYYYMMDD_HHMMSS.json。"""
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        status = self.get_status()
        # 用固定时间戳，避免同一秒覆盖；run_all/shell 传入的时间戳由调用方控制
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        payload = {
            "project": "financial_table_workflow_agent",
            "runner_version": RUNNER_VERSION,
            "generated_at": ts,
            "status": status,
        }

        latest_path = self.sessions_dir / "latest_session.json"
        with latest_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        stamped_path = self.sessions_dir / f"session_{ts}.json"
        with stamped_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        return latest_path

    # ------------------------------------------------------------------
    # dashboard 打印（供 run_all / shell 复用）
    # ------------------------------------------------------------------

    def print_dashboard(self) -> None:
        """打印 summary dashboard。"""
        status = self.get_status()
        print()
        print("[run_all] Financial Table Workflow Agent")
        print()
        print(f"Input dir: {status['input_dir']}")
        print(f"Output root: {status['output_root']}")
        goal = status["analysis_goal"]
        if len(goal) > 80:
            goal = goal[:77] + "..."
        print(f"Analysis goal: {goal}")
        print()
        for s in STAGE_ORDER:
            display = STAGE_DISPLAY[s]
            st = status["stages"][s]["status"]
            dots = "." * max(2, 38 - len(display))
            print(f"{display} {dots} {st}")
        print()
        print(f"Final status: {status['final_validation_status'] or 'n/a'}")
        if status["prepared_panel_rows"] is not None and (
            status["repaired_panel_rows"] is not None
        ):
            print(
                f"Rows: {status['prepared_panel_rows']} -> "
                f"{status['repaired_panel_rows']}"
            )
        elif status["prepared_panel_rows"] is not None:
            print(f"Rows: {status['prepared_panel_rows']}")
        print(f"Rows removed by repair: {status['rows_removed_by_repair']}")
        label_in = status["label_in_approved_features"]
        label_msg = (
            "FAILED (label in features!)" if label_in else "passed"
        )
        print(f"Label leakage: {label_msg}")
        print(f"Approved features: {len(status['approved_feature_columns'])}")
        if status["final_report_path"]:
            print(f"Final report: {status['final_report_path']}")
        if status["one_page_path"]:
            print(f"One-page summary: {status['one_page_path']}")
        print(f"Session log: {status['session_log_path']}")

    # ------------------------------------------------------------------
    # 内部：各阶段实现
    # ------------------------------------------------------------------

    def _profile_impl(self) -> dict[str, Any]:
        """Stage 1 实现：剖析真实市场数据目录。

        v3：不再自动生成合成样例数据。若 input_dir 不存在、为空或缺少 CSV，
        直接抛错并给出可操作错误信息，绝不静默回退到合成数据。
        """
        if not self.input_dir.exists():
            raise FileNotFoundError(
                f"input_dir does not exist: {self.input_dir}. "
                "Download real market data first, e.g.:\n"
                "  python -B src/run_fetch_real_data.py --tickers 600519 "
                "--start_date 2024-01-01 --end_date 2024-01-10 "
                "--output_dir data/real_market "
                "--tradingagents_path D:\\dwzq\\TradingAgents-astock-main "
                "--no_snapshot_fundamentals"
            )
        csv_files = sorted(self.input_dir.glob("*.csv"))
        if not csv_files:
            raise FileNotFoundError(
                f"no CSV files in {self.input_dir}. "
                "Download real market data first (see run_fetch_real_data.py); "
                "synthetic sample data generation has been removed in v3."
            )

        profiler = FinancialTableProfiler(self.input_dir)
        profile = profiler.run()
        profiler.save_json(profile, self.profile_json)
        profiler.save_markdown(profile, self.profile_md)

        n_tables = len(profile.get("tables", []))
        total_issues = sum(
            len(t.get("potential_issues", [])) for t in profile.get("tables", [])
        )
        total_issues += len(
            profile.get("cross_table_findings", {}).get(
                "global_potential_issues", []
            )
        )
        return {
            "output_files": [
                str(self.profile_json).replace("\\", "/"),
                str(self.profile_md).replace("\\", "/"),
            ],
            "summary": {
                "n_tables": n_tables,
                "total_issues": total_issues,
            },
        }

    def _planner_impl(self) -> dict[str, Any]:
        """Stage 2 实现。"""
        if not self.profile_json.exists():
            raise FileNotFoundError(
                f"profile not found: {self.profile_json}. Run profile first."
            )
        planner = WorkflowPlanner()
        profile = planner.load_profile(self.profile_json)
        plan = planner.build_plan(profile, self.analysis_goal)
        plan["input_profile_path"] = str(self.profile_json).replace("\\", "/")
        planner.save_plan(plan, self.plan_json)
        planner.save_markdown_report(plan, self.plan_md)

        return {
            "output_files": [
                str(self.plan_json).replace("\\", "/"),
                str(self.plan_md).replace("\\", "/"),
            ],
            "summary": {
                "n_workflow_steps": len(plan.get("workflow_steps", [])),
                "n_validation_checks": len(
                    plan.get("validation_plan", {}).get("checks", [])
                ),
                "analysis_goal": self.analysis_goal,
            },
        }

    def _executor_impl(self) -> dict[str, Any]:
        """Stage 3 实现。"""
        if not self.plan_json.exists():
            raise FileNotFoundError(
                f"plan not found: {self.plan_json}. Run planner first."
            )
        ex = CodeExecutor()
        plan = ex.load_workflow_plan(self.plan_json)
        result = ex.execute(plan, self.input_dir)
        paths = ex.save_outputs(result, self.prepared_dir)
        ex.save_execution_report(result, self.prepared_dir)

        panel: pd.DataFrame = result["panel"]
        pk_unique = not panel.duplicated(subset=["date", "ticker"]).any()
        fts = result["execution_log"].get("final_table_summary", {})
        return {
            "output_files": [str(p).replace("\\", "/") for p in paths.values()],
            "summary": {
                "n_rows": int(len(panel)),
                "n_columns": int(panel.shape[1]),
                "primary_key_unique": bool(pk_unique),
                "date_min": fts.get("date_min"),
                "date_max": fts.get("date_max"),
            },
        }

    def _initial_critic_impl(self) -> dict[str, Any]:
        """Stage 4 实现：对 prepared_panel 运行 Critic。"""
        self._check_critic_inputs(self.prepared_panel)
        report = self._run_critic(
            panel_path=self.prepared_panel,
            output_dir=self.validation_dir,
        )
        s = report.get("summary", {})
        return {
            "output_files": [
                str(self.initial_validation_json).replace("\\", "/"),
                str(self.initial_validation_md).replace("\\", "/"),
                str(self.initial_approved).replace("\\", "/"),
            ],
            "summary": {
                "overall_status": report.get("overall_status", "unknown"),
                "total_checks": s.get("total_checks"),
                "passed": s.get("passed"),
                "warnings": s.get("warnings"),
                "failed": s.get("failed"),
            },
        }

    def _repair_impl(self) -> dict[str, Any]:
        """Stage 5 实现：Repair Loop（单轮，向后兼容 run_repair CLI 与旧测试）。

        v2 的多轮调度走 :meth:`_run_remediation_agent`；本方法保留单轮入口，
        供 ``run repair`` shell 命令与 ``run_repair.py`` CLI 复用。
        """
        for label, path in [
            ("panel", self.prepared_panel),
            ("validation_report", self.initial_validation_json),
            ("data_dictionary", self.data_dictionary),
            ("approved_features", self.initial_approved),
        ]:
            if not Path(path).exists():
                raise FileNotFoundError(f"{label} not found: {path}")

        loop = RepairLoop(max_row_loss_ratio=self.max_row_loss_ratio)
        loop.load_inputs(
            panel_path=self.prepared_panel,
            validation_report_path=self.initial_validation_json,
            data_dictionary_path=self.data_dictionary,
            approved_features_path=self.initial_approved,
        )
        plan = loop.build_repair_plan()
        result = loop.apply_repairs(plan)
        paths = loop.save_outputs(result, self.repaired_dir)
        loop.save_report(result, self.repaired_dir)

        log = result["repair_log"]
        return {
            "output_files": [str(p).replace("\\", "/") for p in paths.values()]
            + [str(self.repair_report).replace("\\", "/")],
            "summary": {
                "rows_before": log.get("rows_before"),
                "rows_after": log.get("rows_after"),
                "rows_removed": log.get("rows_removed"),
                "input_validation_status": log.get("input_validation_status"),
            },
        }

    # ------------------------------------------------------------------
    # v2：有界多轮 Remediation Agent（Observe → Decide → Act → Reflect）
    # ------------------------------------------------------------------

    def _run_remediation_agent(self) -> None:
        """有界多轮自我修正闭环。

        每一轮：
          Observe  读取最新 validation_report（首轮用 initial，后续用上一轮复审）
          Decide   用 RepairLoop.decide_round 选可执行策略或给出 termination_reason
          Act      在 panel 副本上 apply_selected；安全门在实际行数上复核
          Reflect  重新运行 Critic；记录 panel 指纹与 failed check 集合
          Decide whether to continue

        停止条件（termination_reason）：
          validation_passed / no_actionable_strategy / no_progress /
          max_rounds_reached / manual_review_required / stage_failed

        下一轮基于上一轮 repaired panel 与最新 Critic 结果，绝不重用最初输入。
        """
        rec = self.stages["repair"]
        rec["status"] = "running"
        rec["start_time"] = _now_iso()
        start_dt = datetime.now()
        self._log(
            f"Remediation Agent start (max_rounds={self.max_repair_rounds}, "
            f"max_row_loss_ratio={self.max_row_loss_ratio})"
        )

        try:
            self._remediation_agent_loop()
            self._has_run_remediation = True
            end_dt = datetime.now()
            rec["end_time"] = _now_iso()
            rec["duration_seconds"] = round((end_dt - start_dt).total_seconds(), 3)
            rec["status"] = "completed"
            rec["summary"] = {
                "repair_rounds": self.repair_rounds_run,
                "termination_reason": self.termination_reason,
                "manual_review_required": self.manual_review_required,
                "unresolved_checks": list(self.unresolved_checks or []),
            }
            rec["error_message"] = None
            self._log(
                f"Remediation Agent finished: rounds={self.repair_rounds_run}, "
                f"termination_reason={self.termination_reason}"
            )
        except Exception as exc:  # noqa: BLE001
            end_dt = datetime.now()
            rec["end_time"] = _now_iso()
            rec["duration_seconds"] = round((end_dt - start_dt).total_seconds(), 3)
            rec["status"] = "failed"
            rec["error_message"] = f"{type(exc).__name__}: {exc}"
            rec["traceback"] = traceback.format_exc()
            self.termination_reason = "stage_failed"
            self.manual_review_required = True
            self._has_run_remediation = True
            # 即使失败也写 repair_history.json，保证审计文件存在
            self._write_repair_history(
                rounds=self.repair_history,
                termination_reason=self.termination_reason,
                manual_review_required=True,
                unresolved_checks=list(self.unresolved_checks or []),
            )
            print(
                f"[pipeline] ERROR in Remediation Agent: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            if self.verbose:
                traceback.print_exc()

    def _remediation_agent_loop(self) -> None:
        """Remediation Agent 主循环（不含异常捕获，由外层 _run_remediation_agent 包裹）。"""
        # 参数边界校验（防御性；run_all 已校验，这里再兜底）
        if self.max_repair_rounds < 1:
            raise ValueError(
                f"max_repair_rounds must be >= 1, got {self.max_repair_rounds}"
            )
        if not (0.0 <= self.max_row_loss_ratio <= 1.0):
            raise ValueError(
                f"max_row_loss_ratio must be in [0, 1], got {self.max_row_loss_ratio}"
            )

        # 输入校验
        for label, path in [
            ("panel", self.prepared_panel),
            ("validation_report", self.initial_validation_json),
            ("data_dictionary", self.data_dictionary),
            ("approved_features", self.initial_approved),
        ]:
            if not Path(path).exists():
                raise FileNotFoundError(f"{label} not found: {path}")

        loop = RepairLoop(max_row_loss_ratio=self.max_row_loss_ratio)
        loop.load_inputs(
            panel_path=self.prepared_panel,
            validation_report_path=self.initial_validation_json,
            data_dictionary_path=self.data_dictionary,
            approved_features_path=self.initial_approved,
        )

        # 原始 panel 行数（安全门基准，全程不变）
        rows_original = len(loop.panel) if loop.panel is not None else 0
        if rows_original == 0:
            # 空 panel：无法修复，直接 manual review
            self.termination_reason = "manual_review_required"
            self.manual_review_required = True
            self.unresolved_checks = ["empty_panel"]
            self._write_noop_repair_artifacts("failed", "no_repair_needed")
            self._write_repair_history(
                rounds=[],
                termination_reason=self.termination_reason,
                manual_review_required=True,
                unresolved_checks=["empty_panel"],
            )
            return

        # 当前 panel（每轮更新；首轮为 prepared_panel）
        current_panel = loop.panel.copy()
        # 当前 validation report（每轮更新；首轮为 initial）
        with self.initial_validation_json.open("r", encoding="utf-8") as f:
            current_report = json.load(f)

        cumulative_removed = 0
        prev_failed_set: set[str] | None = None
        prev_fingerprint: str | None = None
        rounds_log: list[dict[str, Any]] = []

        for round_idx in range(1, self.max_repair_rounds + 1):
            # ---- Observe ----
            failed_before = loop.failed_checks_of(current_report)
            failed_names_before = sorted(c["check_name"] for c in failed_before)
            status_before = current_report.get("overall_status", "unknown")
            rows_before = len(current_panel)
            fingerprint_before = loop.panel_fingerprint(current_panel)

            # ---- Decide ----
            decision = loop.decide_round(
                current_report,
                current_panel,
                rows_original,
                cumulative_removed=cumulative_removed,
            )
            term = decision["termination_reason"]

            # 若 Decide 已给出终止原因（validation_passed / no_actionable_strategy /
            # manual_review_required），记录本轮并退出
            if term is not None:
                round_rec = self._make_round_record(
                    round_idx,
                    status_before,
                    failed_names_before,
                    decision["candidate_strategies"],
                    decision["selected_strategies"],
                    decision["decision_reason"],
                    rows_before,
                    rows_before,
                    cumulative_removed,
                    rows_original,
                    status_before,
                    failed_names_before,
                    fingerprint_before,
                    term,
                )
                rounds_log.append(round_rec)
                self.termination_reason = term
                self.manual_review_required = bool(
                    decision.get("manual_review_required", False)
                )
                self.unresolved_checks = list(decision.get("unresolved_checks", []))
                # validation_passed 时把当前 panel 落盘为 repaired_panel
                if term == "validation_passed":
                    self._save_repaired_panel(current_panel, loop)
                    self._copy_current_validation_as_repaired(current_report)
                else:
                    # no_actionable_strategy / manual_review_required：
                    # 仍把当前 panel 落盘，便于人工查看
                    self._save_repaired_panel(current_panel, loop)
                    # 复审报告用当前 report（可能仍 failed）
                    self._copy_current_validation_as_repaired(current_report)
                break

            # ---- Act ----
            new_panel, actions, round_removed = loop.apply_selected(
                current_panel, decision["selected_strategies"]
            )
            rows_after = len(new_panel)

            # 安全门：在实际执行结果上复核累计删除比例
            actual_cumulative = cumulative_removed + round_removed
            if rows_original > 0:
                actual_ratio = actual_cumulative / rows_original
            else:
                actual_ratio = 0.0
            safety_violation = actual_ratio > self.max_row_loss_ratio + 1e-9

            if safety_violation:
                # 超过阈值：不保存修复后 panel，回退到本轮输入，转人工
                self._save_repaired_panel(current_panel, loop)
                self._copy_current_validation_as_repaired(current_report)
                round_rec = self._make_round_record(
                    round_idx,
                    status_before,
                    failed_names_before,
                    decision["candidate_strategies"],
                    decision["selected_strategies"],
                    (
                        f"safety gate violated after apply: actual cumulative row loss "
                        f"{actual_ratio:.4f} > {self.max_row_loss_ratio:.4f}; "
                        "panel reverted to pre-round state; manual review required"
                    ),
                    rows_before,
                    rows_before,
                    cumulative_removed,
                    rows_original,
                    status_before,
                    failed_names_before,
                    fingerprint_before,
                    "manual_review_required",
                )
                rounds_log.append(round_rec)
                self.termination_reason = "manual_review_required"
                self.manual_review_required = True
                self.unresolved_checks = list(failed_names_before)
                break

            # ---- Reflect：对修复后 panel 重新运行 Critic ----
            current_panel = new_panel
            cumulative_removed = actual_cumulative
            # 先把修复后 panel 落盘，Critic 从磁盘读
            self._save_repaired_panel(current_panel, loop)
            reflect_report = self._run_critic(
                panel_path=self.repaired_panel,
                output_dir=self.validation_repaired_dir,
                critic_factory=self._critic_factory,
            )
            current_report = reflect_report
            status_after = reflect_report.get("overall_status", "unknown")
            failed_after = loop.failed_checks_of(reflect_report)
            failed_names_after = sorted(c["check_name"] for c in failed_after)
            fingerprint_after = loop.panel_fingerprint(current_panel)

            round_rec = self._make_round_record(
                round_idx,
                status_before,
                failed_names_before,
                decision["candidate_strategies"],
                decision["selected_strategies"],
                decision["decision_reason"],
                rows_before,
                rows_after,
                cumulative_removed,
                rows_original,
                status_after,
                failed_names_after,
                fingerprint_after,
                None,
            )
            round_rec["actions_applied"] = actions
            rounds_log.append(round_rec)

            # ---- Decide whether to continue ----
            if status_after != "failed" and not failed_names_after:
                self.termination_reason = "validation_passed"
                self.manual_review_required = False
                self.unresolved_checks = []
                break

            # no_progress：failed check 集合 + panel 指纹连续不变 → 停止。
            # 注意：必须同时满足"failed 集合不变"且"指纹不变"才停；只要其中
            # 一个变化就视为有进展，继续下一轮（直到 max_rounds）。
            if (
                prev_failed_set is not None
                and prev_failed_set == set(failed_names_after)
                and prev_fingerprint == fingerprint_after
            ):
                self.termination_reason = "no_progress"
                self.manual_review_required = True
                self.unresolved_checks = list(failed_names_after)
                break

            prev_failed_set = set(failed_names_after)
            prev_fingerprint = fingerprint_after

            # 达到最大轮数
            if round_idx >= self.max_repair_rounds:
                self.termination_reason = "max_rounds_reached"
                self.manual_review_required = bool(failed_names_after)
                self.unresolved_checks = list(failed_names_after)
                break

        self.repair_rounds_run = len(rounds_log)
        self.repair_history = rounds_log
        # 写 repair_plan / repair_log / repair_report（兼容旧产物）
        self._write_remediation_legacy_artifacts(loop, current_panel, current_report)
        # 写 repair_history.json（v2 审计记录）
        self._write_repair_history(
            rounds=rounds_log,
            termination_reason=self.termination_reason or "max_rounds_reached",
            manual_review_required=bool(self.manual_review_required),
            unresolved_checks=list(self.unresolved_checks or []),
        )

    # ------------------------------------------------------------------
    # Remediation Agent 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _make_round_record(
        round_idx: int,
        status_before: str,
        failed_before: list[str],
        candidates: list[dict[str, Any]],
        selected: list[dict[str, Any]],
        decision_reason: str,
        rows_before: int,
        rows_after: int,
        cumulative_removed: int,
        rows_original: int,
        status_after: str,
        failed_after: list[str],
        fingerprint: str,
        termination_reason: str | None,
    ) -> dict[str, Any]:
        cum_ratio = (
            round(cumulative_removed / rows_original, 6) if rows_original > 0 else 0.0
        )
        return {
            "round": round_idx,
            "validation_status_before": status_before,
            "failed_checks_before": failed_before,
            "candidate_strategies": candidates,
            "selected_strategies": selected,
            "decision_reason": decision_reason,
            "rows_before": rows_before,
            "rows_after": rows_after,
            "cumulative_row_loss_ratio": cum_ratio,
            "validation_status_after": status_after,
            "failed_checks_after": failed_after,
            "panel_fingerprint": fingerprint,
            "termination_reason": termination_reason,
        }

    def _save_repaired_panel(self, panel: pd.DataFrame, loop: RepairLoop) -> None:
        """把当前 panel 落盘为 repaired_panel.csv（不覆盖原始 prepared_panel.csv）。"""
        self.repaired_dir.mkdir(parents=True, exist_ok=True)
        panel_to_write = panel.copy()
        if "date" in panel_to_write.columns:
            panel_to_write["date"] = pd.to_datetime(
                panel_to_write["date"]
            ).dt.strftime("%Y-%m-%d")
        if "announce_date" in panel_to_write.columns:
            ad = pd.to_datetime(panel_to_write["announce_date"], errors="coerce")
            panel_to_write["announce_date"] = ad.dt.strftime("%Y-%m-%d")
        panel_to_write.to_csv(
            self.repaired_panel, index=False, encoding="utf-8-sig"
        )

    def _copy_current_validation_as_repaired(self, report: dict[str, Any]) -> None:
        """把当前 validation report 写为复审报告（validation_repaired/）。"""
        self.validation_repaired_dir.mkdir(parents=True, exist_ok=True)
        critic = ValidityCritic()
        critic.save_json_report(report, self.final_validation_json)
        critic.save_markdown_report(report, self.final_validation_md)
        critic.save_approved_feature_columns(report, self.final_approved)

    def _write_remediation_legacy_artifacts(
        self,
        loop: RepairLoop,
        final_panel: pd.DataFrame,
        final_report: dict[str, Any],
    ) -> None:
        """写 repair_plan.json / repair_log.json / repair_report.md（兼容旧产物）。"""
        self.repaired_dir.mkdir(parents=True, exist_ok=True)
        rows_before = len(loop.panel) if loop.panel is not None else 0
        rows_after = len(final_panel)
        failed_checks = loop.failed_checks_of(final_report)
        # repair_plan：用最后一轮的 failed check 与已应用策略汇总
        applied_strategies = []
        for r in self.repair_history:
            for a in r.get("actions_applied", []):
                applied_strategies.append(a)
        repair_plan = {
            "project": "financial_table_workflow_agent",
            "repair_version": REPAIR_VERSION,
            "input_validation_status": loop.validation_report.get(
                "overall_status", "unknown"
            ),
            "failed_checks": failed_checks,
            "warning_checks": [
                {
                    "check_name": c["check_name"],
                    "status": c.get("status"),
                    "evidence": c.get("evidence"),
                }
                for c in final_report.get("checks", [])
                if c.get("status") == "warning"
            ],
            "repair_actions": applied_strategies,
            "not_repaired_items": [
                {"item": name, "reason": "unresolved after bounded remediation; manual review required"}
                for name in (self.unresolved_checks or [])
            ],
            "next_validation_required": self.termination_reason != "validation_passed",
            "remediation_summary": {
                "repair_rounds": self.repair_rounds_run,
                "termination_reason": self.termination_reason,
                "manual_review_required": bool(self.manual_review_required),
                "unresolved_checks": list(self.unresolved_checks or []),
            },
        }
        with self.repair_plan.open("w", encoding="utf-8") as f:
            json.dump(repair_plan, f, ensure_ascii=False, indent=2)

        checks_after = loop._post_repair_checks(final_panel)
        repair_log = {
            "project": "financial_table_workflow_agent",
            "repair_version": REPAIR_VERSION,
            "input_panel_path": str(self.prepared_panel).replace("\\", "/"),
            "input_validation_report_path": str(self.initial_validation_json).replace("\\", "/"),
            "rows_before": rows_before,
            "rows_after": rows_after,
            "rows_removed": rows_before - rows_after,
            "actions_applied": applied_strategies,
            "checks_after_repair": checks_after,
            "warnings": [],
            "next_step": (
                "validation_passed" if self.termination_reason == "validation_passed"
                else "manual review required"
            ),
            "remediation_summary": {
                "repair_rounds": self.repair_rounds_run,
                "termination_reason": self.termination_reason,
                "manual_review_required": bool(self.manual_review_required),
                "unresolved_checks": list(self.unresolved_checks or []),
            },
        }
        with self.repair_log.open("w", encoding="utf-8") as f:
            json.dump(repair_log, f, ensure_ascii=False, indent=2)

        # repair_report.md
        self.repair_report.write_text(
            self._render_remediation_report(
                rows_before, rows_after, final_report, applied_strategies
            ),
            encoding="utf-8",
        )

    def _render_remediation_report(
        self,
        rows_before: int,
        rows_after: int,
        final_report: dict[str, Any],
        applied_strategies: list[dict[str, Any]],
    ) -> str:
        lines: list[str] = []
        lines.append("# Remediation Agent Report (bounded multi-round)")
        lines.append("")
        lines.append(
            f"- project: `financial_table_workflow_agent`  |  repair_version: `{REPAIR_VERSION}`"
        )
        lines.append(f"- repair_rounds: {self.repair_rounds_run}")
        lines.append(f"- termination_reason: `{self.termination_reason}`")
        lines.append(
            f"- manual_review_required: {bool(self.manual_review_required)}"
        )
        lines.append(
            f"- unresolved_checks: {list(self.unresolved_checks or [])}"
        )
        lines.append("")
        lines.append("## 1. Loop Model")
        lines.append("")
        lines.append(
            "Observe validation report → Decide actionable strategy → Safety check → "
            "Apply repair → Re-run Critic → Reflect → Decide whether to continue."
        )
        lines.append("")
        lines.append(
            "This is a **bounded feedback loop**, not infinite retry, and not a model "
            "directly editing financial data. Each round is based on the previous round's "
            "repaired panel and the latest Critic result."
        )
        lines.append("")
        lines.append("## 2. Round History")
        lines.append("")
        lines.append("| round | status_before | rows_before | rows_after | cum_loss | status_after | termination |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in self.repair_history:
            lines.append(
                f"| {r['round']} | {r['validation_status_before']} | "
                f"{r['rows_before']} | {r['rows_after']} | "
                f"{r['cumulative_row_loss_ratio']:.4f} | "
                f"{r['validation_status_after']} | "
                f"{r['termination_reason'] or '-'} |"
            )
        lines.append("")
        lines.append("## 3. Strategies Applied")
        lines.append("")
        if applied_strategies:
            lines.append("| strategy | target_check | rows_removed | status |")
            lines.append("|---|---|---|---|")
            for a in applied_strategies:
                lines.append(
                    f"| {a.get('strategy')} | {a.get('target_check')} | "
                    f"{a.get('rows_removed')} | {a.get('status')} |"
                )
        else:
            lines.append("(none)")
        lines.append("")
        lines.append("## 4. Result")
        lines.append("")
        lines.append(f"- rows before: {rows_before}")
        lines.append(f"- rows after: {rows_after}")
        lines.append(f"- rows removed: {rows_before - rows_after}")
        lines.append(
            f"- final validation status: {final_report.get('overall_status', 'unknown')}"
        )
        lines.append("")
        lines.append("## 5. Safety Gates")
        lines.append("")
        lines.append(
            f"- max_row_loss_ratio: {self.max_row_loss_ratio} "
            "(cumulative deleted rows / original panel rows)"
        )
        lines.append("- announce_date is never fabricated or backfilled.")
        lines.append("- label_next_5d role is never changed; it never enters approved_feature_columns.")
        lines.append("- No LLM, dynamic code, or arbitrary shell command modifies the DataFrame.")
        lines.append("- Original CSVs are never overwritten; only derived artifacts are produced.")
        lines.append("")
        return "\n".join(lines)

    def _write_repair_history(
        self,
        rounds: list[dict[str, Any]],
        termination_reason: str,
        manual_review_required: bool,
        unresolved_checks: list[str],
    ) -> None:
        """写 outputs/repaired/repair_history.json（v2 审计记录）。"""
        self.repaired_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "project": "financial_table_workflow_agent",
            "repair_version": REPAIR_VERSION,
            "max_repair_rounds": self.max_repair_rounds,
            "max_row_loss_ratio": self.max_row_loss_ratio,
            "repair_rounds": len(rounds),
            "termination_reason": termination_reason,
            "manual_review_required": manual_review_required,
            "unresolved_checks": unresolved_checks,
            "rounds": rounds,
        }
        with self.repair_history_json.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _repaired_critic_impl(self) -> dict[str, Any]:
        """Stage 6 实现：对 repaired_panel 重新运行 Critic。"""
        if not self.repaired_panel.exists():
            raise FileNotFoundError(
                f"repaired panel not found: {self.repaired_panel}. Run repair first."
            )
        report = self._run_critic(
            panel_path=self.repaired_panel,
            output_dir=self.validation_repaired_dir,
        )
        s = report.get("summary", {})
        return {
            "output_files": [
                str(self.final_validation_json).replace("\\", "/"),
                str(self.final_validation_md).replace("\\", "/"),
                str(self.final_approved).replace("\\", "/"),
            ],
            "summary": {
                "overall_status": report.get("overall_status", "unknown"),
                "total_checks": s.get("total_checks"),
                "passed": s.get("passed"),
                "warnings": s.get("warnings"),
                "failed": s.get("failed"),
            },
        }

    def _final_report_impl(self) -> dict[str, Any]:
        """Stage 7 实现：Final Report Generator。"""
        inputs = [
            self.profile_json,
            self.plan_json,
            self.prepared_panel,
            self.execution_log,
            self.initial_validation_json,
            self.repair_plan,
            self.repair_log,
            self.repaired_panel,
            self.final_validation_json,
            self.final_approved,
            self.data_dictionary,
        ]
        for p in inputs:
            if not p.exists():
                raise FileNotFoundError(f"report input not found: {p}")

        # Stage 12：fetch_metadata.json 可选（自然语言抓取模式有；已有 CSV 模式可能无）。
        # 不存在时 ReportGenerator 显示"用户提供的已有 CSV"。
        fetch_metadata_path = self.input_dir / "fetch_metadata.json"

        gen = ReportGenerator()
        gen.load_inputs(
            profile_json=self.profile_json,
            workflow_plan_json=self.plan_json,
            prepared_panel=self.prepared_panel,
            execution_log=self.execution_log,
            initial_validation_report=self.initial_validation_json,
            repair_plan=self.repair_plan,
            repair_log=self.repair_log,
            repaired_panel=self.repaired_panel,
            final_validation_report=self.final_validation_json,
            approved_features=self.final_approved,
            data_dictionary=self.data_dictionary,
            fetch_metadata=fetch_metadata_path,
            input_dir=self.input_dir,
        )
        paths = gen.save_all(self.final_report_dir)
        summary = gen.build_summary()
        cl = summary.get("closed_loop_result", {})
        return {
            "output_files": [str(p).replace("\\", "/") for p in paths.values()],
            "summary": {
                "initial_validation_status": summary.get(
                    "initial_validation_status"
                ),
                "final_validation_status": summary.get(
                    "final_validation_status"
                ),
                "rows_removed_by_repair": summary.get("rows_removed_by_repair"),
                "one_line": cl.get("one_line", ""),
            },
        }

    # ------------------------------------------------------------------
    # 内部：Critic 复用
    # ------------------------------------------------------------------

    def _run_critic(
        self,
        panel_path: Path,
        output_dir: Path,
        critic_factory: "CriticFactory | None" = None,
    ) -> dict[str, Any]:
        """对指定 panel 运行 Critic，输出到 output_dir。

        critic_factory：可选的可注入 Critic 工厂，用于测试。为 None 时用真实
        ValidityCritic。工厂签名为 ``() -> ValidityCritic``，返回的对象需支持
        load_inputs / run_all_checks / save_* 接口。
        """
        self._check_critic_inputs(panel_path)
        critic = critic_factory() if critic_factory is not None else ValidityCritic()
        critic.load_inputs(
            panel_path=panel_path,
            data_dictionary_path=self.data_dictionary,
            execution_log_path=self.execution_log,
            plan_path=self.plan_json,
            executor_source_path=self.executor_source,
            calendar_path=self.calendar_csv if self.calendar_csv.exists() else None,
        )
        report = critic.run_all_checks()
        critic.save_json_report(report, output_dir / "validation_report.json")
        critic.save_markdown_report(report, output_dir / "validation_report.md")
        critic.save_approved_feature_columns(
            report, output_dir / "approved_feature_columns.json"
        )
        return report

    def _check_critic_inputs(self, panel_path: Path) -> None:
        for label, path in [
            ("panel", panel_path),
            ("data_dictionary", self.data_dictionary),
            ("execution_log", self.execution_log),
            ("plan", self.plan_json),
            ("executor_source", self.executor_source),
        ]:
            if not Path(path).exists():
                raise FileNotFoundError(f"{label} not found: {path}")

    # ------------------------------------------------------------------
    # 内部：阶段执行框架
    # ------------------------------------------------------------------

    def _run_stage(
        self,
        stage: str,
        impl: "callable",  # type: ignore[type-arg]
    ) -> dict[str, Any]:
        """运行单个阶段，记录 start/end/duration/status/output/summary/error。"""
        rec = self.stages[stage]
        rec["status"] = "running"
        rec["start_time"] = _now_iso()
        start_dt = datetime.now()
        try:
            result = impl()
            end_dt = datetime.now()
            rec["end_time"] = _now_iso()
            rec["duration_seconds"] = round((end_dt - start_dt).total_seconds(), 3)
            rec["status"] = "completed"
            rec["output_files"] = result.get("output_files", [])
            rec["summary"] = result.get("summary", {})
            rec["error_message"] = None
            self._log(f"{STAGE_DISPLAY[stage]} ... completed")
        except Exception as exc:  # noqa: BLE001
            end_dt = datetime.now()
            rec["end_time"] = _now_iso()
            rec["duration_seconds"] = round((end_dt - start_dt).total_seconds(), 3)
            rec["status"] = "failed"
            rec["error_message"] = f"{type(exc).__name__}: {exc}"
            rec["traceback"] = traceback.format_exc()
            print(
                f"[pipeline] ERROR in {STAGE_DISPLAY[stage]}: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            if self.verbose:
                traceback.print_exc()
        return rec

    def _mark_skipped(self, stage: str, reason: str) -> None:
        rec = self.stages[stage]
        rec["status"] = "skipped"
        rec["start_time"] = _now_iso()
        rec["end_time"] = rec["start_time"]
        rec["duration_seconds"] = 0.0
        rec["summary"] = {"skip_reason": reason}
        rec["error_message"] = None
        self._log(f"{STAGE_DISPLAY[stage]} ... skipped ({reason})")

    def _fail_fast(self, stage: str) -> None:
        print(
            f"[pipeline] {STAGE_DISPLAY[stage]} failed; stopping full pipeline.",
            file=sys.stderr,
        )

    def _skip_repair_reason(self, initial_status: str) -> str:
        if initial_status in ("passed", "passed_with_warnings"):
            return (
                f"initial critic status={initial_status} (not failed); "
                "no repair needed"
            )
        if not self.auto_repair:
            return "auto_repair=False; repair skipped by user"
        return f"initial critic status={initial_status}; repair not triggered"

    # ------------------------------------------------------------------
    # 内部：no-op repair 产物（无需 Repair 时生成，区分两种 kind）
    # ------------------------------------------------------------------

    def _write_noop_repair_artifacts(
        self, initial_status: str, no_op_kind: str
    ) -> None:
        """当无需实际 Repair 时生成统一 no-op 产物，让 final_report 输入齐全。

        no_op_kind:
          - no_repair_needed: initial critic 未失败（passed/passed_with_warnings）。
            final_status = initial_status。
          - repair_disabled:  initial critic failed 但 --no_repair。最终仍 failed。
        """
        import shutil

        self.repaired_dir.mkdir(parents=True, exist_ok=True)
        self.validation_repaired_dir.mkdir(parents=True, exist_ok=True)

        # 1. prepared_panel -> repaired_panel（原样复制，不修复）
        if self.prepared_panel.exists() and not self.repaired_panel.exists():
            shutil.copyfile(self.prepared_panel, self.repaired_panel)
        elif self.prepared_panel.exists():
            shutil.copyfile(self.prepared_panel, self.repaired_panel)

        # 行数实算
        rows_n = self._count_rows(self.prepared_panel) or 0

        # 2. repair_plan.json
        failed_checks: list[dict[str, Any]] = []
        if self.initial_validation_json.exists():
            try:
                with self.initial_validation_json.open("r", encoding="utf-8") as f:
                    init_report = json.load(f)
                failed_checks = [
                    {
                        "check_name": c.get("check_name"),
                        "category": c.get("category"),
                        "severity": c.get("severity"),
                        "status": c.get("status"),
                        "description": c.get("description"),
                        "evidence": c.get("evidence"),
                        "recommendation": c.get("recommendation"),
                    }
                    for c in init_report.get("checks", [])
                    if c.get("status") == "failed"
                ]
            except Exception:  # noqa: BLE001
                init_report = {}
        else:
            init_report = {}

        if no_op_kind == "no_repair_needed":
            plan_reason = "initial critic did not fail; no repair needed"
            log_next = "no repair needed; initial validation copied as repaired validation"
        else:  # repair_disabled
            plan_reason = "initial critic failed but --no_repair set; repair disabled by user"
            log_next = "repair disabled; panel unchanged; final status remains failed"

        repair_plan = {
            "project": "financial_table_workflow_agent",
            "repair_version": "0.1",
            "input_validation_status": initial_status,
            "failed_checks": failed_checks,
            "warning_checks": [],
            "repair_actions": [],
            "not_repaired_items": [],
            "next_validation_required": False,
            "no_op": True,
            "no_op_kind": no_op_kind,
            "reason": plan_reason,
        }
        with self.repair_plan.open("w", encoding="utf-8") as f:
            json.dump(repair_plan, f, ensure_ascii=False, indent=2)

        # 3. repair_log.json
        checks_after = self._noop_checks_after_repair()
        repair_log = {
            "project": "financial_table_workflow_agent",
            "repair_version": "0.1",
            "input_panel_path": str(self.prepared_panel).replace("\\", "/"),
            "input_validation_report_path": str(self.initial_validation_json).replace("\\", "/"),
            "rows_before": rows_n,
            "rows_after": rows_n,
            "rows_removed": 0,
            "actions_applied": [],
            "checks_after_repair": checks_after,
            "warnings": [],
            "no_op": True,
            "no_op_kind": no_op_kind,
            "next_step": log_next,
        }
        with self.repair_log.open("w", encoding="utf-8") as f:
            json.dump(repair_log, f, ensure_ascii=False, indent=2)

        # 4. repair_report.md
        self.repair_report.write_text(
            self._render_noop_repair_report(initial_status, no_op_kind, rows_n, failed_checks),
            encoding="utf-8",
        )

        # 5. 复制 initial validation -> repaired validation（json/md/approved）
        for src, dst in [
            (self.initial_validation_json, self.final_validation_json),
            (self.initial_validation_md, self.final_validation_md),
            (self.initial_approved, self.final_approved),
        ]:
            if src.exists():
                shutil.copyfile(src, dst)

        self._log(
            f"no-op repair artifacts written (kind={no_op_kind}, "
            f"initial_status={initial_status})"
        )

    def _noop_checks_after_repair(self) -> dict[str, Any]:
        """no-op 场景的 checks_after_repair（实算自 prepared_panel）。"""
        close_missing = -1
        pk_dup = -1
        label_preserved = False
        label_not_in_features = True
        approved: list[str] = []
        if self.prepared_panel.exists():
            try:
                df = pd.read_csv(self.prepared_panel)
                close_missing = int(df["close"].isna().sum()) if "close" in df.columns else -1
                if all(c in df.columns for c in ["date", "ticker"]):
                    pk_dup = int(df.duplicated(subset=["date", "ticker"]).sum())
                label_preserved = "label_next_5d" in df.columns
            except Exception:  # noqa: BLE001
                pass
        if self.initial_approved.exists():
            try:
                with self.initial_approved.open("r", encoding="utf-8") as f:
                    approved = json.load(f).get("approved_feature_columns", [])
                label_not_in_features = "label_next_5d" not in approved
            except Exception:  # noqa: BLE001
                pass
        return {
            "close_missing_count": close_missing,
            "primary_key_unique": pk_dup == 0,
            "primary_key_duplicate_count": pk_dup,
            "label_column_preserved": label_preserved,
            "label_not_in_approved_features": label_not_in_features,
            "approved_feature_columns_unchanged": approved,
        }

    def _render_noop_repair_report(
        self,
        initial_status: str,
        no_op_kind: str,
        rows_n: int | None,
        failed_checks: list[dict[str, Any]],
    ) -> str:
        lines: list[str] = []
        lines.append("# Repair Loop Report (no-op)")
        lines.append("")
        lines.append("- project: `financial_table_workflow_agent`  |  repair_version: `0.1`")
        lines.append(f"- no_op_kind: `{no_op_kind}`")
        lines.append(f"- input_validation_status: `{initial_status}`")
        lines.append("")
        if no_op_kind == "no_repair_needed":
            lines.append("## 1. Why No Repair Was Needed")
            lines.append("")
            lines.append(
                f"The initial Validity Critic reported `overall_status = {initial_status}`, "
                "which is not `failed`. No failed checks require repair, so the Repair Loop "
                "was skipped and `prepared_panel.csv` was copied unchanged to "
                "`repaired_panel.csv`. The initial validation report was copied as the "
                "repaired (re-run) validation report."
            )
        else:  # repair_disabled
            lines.append("## 1. Why Repair Was Disabled")
            lines.append("")
            lines.append(
                f"The initial Validity Critic reported `overall_status = {initial_status}` "
                "(failed), but `--no_repair` was set, so the Repair Loop was disabled by "
                "the user. `prepared_panel.csv` was copied unchanged to `repaired_panel.csv`; "
                "the panel is NOT repaired and the final validation status remains `failed`."
            )
        lines.append("")
        lines.append("## 2. Failed Checks From Initial Critic")
        lines.append("")
        if failed_checks:
            lines.append("| check_name | category | description |")
            lines.append("|---|---|---|")
            for c in failed_checks:
                lines.append(
                    f"| {c.get('check_name')} | {c.get('category')} | {c.get('description')} |"
                )
        else:
            lines.append("(none)")
        lines.append("")
        lines.append("## 3. Repair Result (no-op)")
        lines.append("")
        lines.append(f"- rows before: {rows_n}")
        lines.append(f"- rows after: {rows_n}")
        lines.append("- rows removed: 0")
        lines.append("- actions applied: (none)")
        lines.append("")
        lines.append("## 4. Next Step")
        lines.append("")
        if no_op_kind == "no_repair_needed":
            lines.append("No repair needed; the Final Report Generator reads the copied "
                         "validation artifacts and proceeds.")
        else:
            lines.append("Repair is disabled; the final status remains `failed`. Re-run "
                         "without `--no_repair` to enable the Repair Loop.")
        lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 内部：读取产物状态
    # ------------------------------------------------------------------

    def _read_final_validation_status(self) -> str | None:
        if self.final_validation_json.exists():
            try:
                with self.final_validation_json.open("r", encoding="utf-8") as f:
                    return json.load(f).get("overall_status")
            except Exception:  # noqa: BLE001
                return None
        return None

    def _read_initial_validation_status(self) -> str | None:
        if self.initial_validation_json.exists():
            try:
                with self.initial_validation_json.open("r", encoding="utf-8") as f:
                    return json.load(f).get("overall_status")
            except Exception:  # noqa: BLE001
                return None
        return None

    def _read_rows_removed(self) -> int | None:
        if self.repair_log.exists():
            try:
                with self.repair_log.open("r", encoding="utf-8") as f:
                    return int(json.load(f).get("rows_removed", 0))
            except Exception:  # noqa: BLE001
                return None
        return None

    def _read_failed_count(self, path: Path) -> int | None:
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                return int(json.load(f).get("summary", {}).get("failed", 0))
        except Exception:  # noqa: BLE001
            return None

    def _read_failed_check_names(self, path: Path) -> list[str]:
        """读取 validation_report.json 中所有 status=failed 的 check_name。"""
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as f:
                report = json.load(f)
            return [
                c.get("check_name")
                for c in report.get("checks", [])
                if c.get("status") == "failed"
            ]
        except Exception:  # noqa: BLE001
            return []

    def _read_approved_features(
        self,
    ) -> tuple[list[str], str, bool]:
        """返回 (approved_features, label_column, label_in_features)。"""
        for path in (self.final_approved, self.initial_approved):
            if path.exists():
                try:
                    with path.open("r", encoding="utf-8") as f:
                        data = json.load(f)
                    approved = data.get("approved_feature_columns", [])
                    label_col = data.get("label_column", "label_next_5d")
                    return approved, label_col, label_col in approved
                except Exception:  # noqa: BLE001
                    continue
        return [], "label_next_5d", False

    @staticmethod
    def _count_rows(path: Path) -> int | None:
        if not path.exists():
            return None
        try:
            df = pd.read_csv(path)
            return int(len(df))
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _fresh_stage_record(stage: str) -> dict[str, Any]:
        return {
            "stage": stage,
            "display": STAGE_DISPLAY[stage],
            "status": "pending",
            "start_time": None,
            "end_time": None,
            "duration_seconds": None,
            "output_files": [],
            "summary": {},
            "error_message": None,
        }

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[pipeline] {msg}")


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
