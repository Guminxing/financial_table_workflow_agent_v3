"""Agent 工具包（Stage 9 MVP）。

把现有 PipelineRunner 阶段包装成领域工具，供 Agent Runtime 通过 ToolRegistry 调用。

设计原则：
- 优先调用 PipelineRunner 的公开方法（run_profile / run_planner / run_executor /
  run_initial_critic / run_remediation_agent / run_repaired_critic / run_final_report /
  get_status）。
- **不**复制 profiler/planner/executor/critic/repair/report 业务代码。
- **不**把完整 CSV / 完整报告 / 完整 DataFrame 放入 ToolResult；只返回摘要、
  指标、产物路径和下一步建议。
- artifact path 必须属于当前 run_root（由 AgentContext.ensure_artifact_in_run_root 校验）。
- stage status=failed 时，ToolResult.ok 必须为 False。
- manual_review_required 时必须设置 requires_user_action=True。
- label_in_approved_features=True 时必须返回安全错误。
- 每个写工具只允许写当前 run_root。
- 不暴露 fetch_real_market_data（本轮不实现网络工具）。
- 绝不生成合成数据。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runtime.context import AgentContext
from agent_runtime.models import RiskLevel, ToolResult, ToolSpec


# ======================================================================
# 辅助
# ======================================================================


def _stage_failed(result: ToolResult, runner: Any, stage: str) -> ToolResult:
    """若阶段 failed，把 ToolResult 转为 ok=False。"""
    rec = runner.stages.get(stage, {})
    if rec.get("status") == "failed":
        return ToolResult.failure(
            f"{stage} failed: {rec.get('error_message')}",
            code="STAGE_FAILED",
            status="failed",
            retryable=False,
            metrics=result.metrics,
            artifacts=result.artifacts,
            next_actions=[stage],
        )
    return result


def _check_label_safety(ctx: AgentContext, runner: Any) -> ToolResult | None:
    """检查 label 是否泄漏进 approved features；泄漏时返回安全错误 ToolResult。

    返回 None 表示安全。
    """
    status = runner.get_status()
    if status.get("label_in_approved_features"):
        return ToolResult.failure(
            f"SECURITY: label column '{status.get('label_column')}' is in approved "
            "feature columns; refusing to proceed (label leakage).",
            code="LABEL_LEAKAGE_DETECTED",
            status="manual_review_required",
            retryable=False,
            requires_user_action=True,
            metrics={
                "label_column": status.get("label_column"),
                "approved_feature_columns": status.get("approved_feature_columns", []),
            },
        )
    return None


def _artifacts(ctx: AgentContext, paths: list[Path | str]) -> list[str]:
    """把产物路径列表转为正斜杠串，并校验都在 run_root 下。"""
    out: list[str] = []
    for p in paths:
        out.append(ctx.ensure_artifact_in_run_root(p))
    return out


# ======================================================================
# 工具实现
# ======================================================================


def _tool_configure_workflow(arguments: dict[str, Any], context: Any) -> ToolResult:
    """configure_workflow：校验输入目录 + 更新 AgentContext + 创建当前 run 的 runner。

    不执行 pipeline；不生成模拟数据。
    """
    ctx: AgentContext = context
    input_dir = arguments.get("input_dir")
    if input_dir:
        # 允许在 configure 时切换 input_dir（会重新校验，绝不回退合成数据）
        from agent_runtime.context import validate_input_dir

        ctx.input_dir = validate_input_dir(input_dir)

    runner = ctx.configure_runner(
        analysis_goal=arguments.get("analysis_goal", ...),
        auto_repair=arguments.get("auto_repair", ...),
        max_repair_rounds=arguments.get("max_repair_rounds", ...),
        max_row_loss_ratio=arguments.get("max_row_loss_ratio", ...),
    )
    return ToolResult.success(
        f"workflow configured for run_id={ctx.run_id}; runner output_root={ctx.run_root}",
        status="configured",
        metrics={
            "run_id": ctx.run_id,
            "run_root": str(ctx.run_root).replace("\\", "/"),
            "input_dir": str(ctx.input_dir).replace("\\", "/"),
            "analysis_goal": ctx.analysis_goal,
            "auto_repair": ctx.auto_repair,
            "max_repair_rounds": ctx.max_repair_rounds,
            "max_row_loss_ratio": ctx.max_row_loss_ratio,
            "runner_output_root": str(runner.output_root).replace("\\", "/"),
        },
        next_actions=["profile_financial_data"],
    )


def _tool_inspect_pipeline_status(
    arguments: dict[str, Any], context: Any
) -> ToolResult:
    """inspect_pipeline_status：只读当前 run 的 pipeline 状态。risk=read。"""
    ctx: AgentContext = context
    runner = ctx.get_runner()
    status = runner.get_status()
    # 只保留可序列化的扁平摘要（get_status 已是 dict，但裁掉过深字段更安全）
    summary_metrics = {
        "input_dir": status.get("input_dir"),
        "output_root": status.get("output_root"),
        "initial_validation_status": status.get("initial_validation_status"),
        "final_validation_status": status.get("final_validation_status"),
        "prepared_panel_rows": status.get("prepared_panel_rows"),
        "repaired_panel_rows": status.get("repaired_panel_rows"),
        "rows_removed_by_repair": status.get("rows_removed_by_repair"),
        "failed_checks_initial": status.get("failed_checks_initial"),
        "failed_checks_final": status.get("failed_checks_final"),
        "approved_feature_columns_count": len(
            status.get("approved_feature_columns", [])
        ),
        "label_column": status.get("label_column"),
        "label_in_approved_features": status.get("label_in_approved_features"),
        "repair_rounds": status.get("repair_rounds"),
        "termination_reason": status.get("termination_reason"),
        "manual_review_required": status.get("manual_review_required"),
        "unresolved_checks": status.get("unresolved_checks"),
    }
    stage_statuses = {
        s: status["stages"][s]["status"] for s in status.get("stages", {})
    }
    summary_metrics["stage_statuses"] = stage_statuses

    # label 安全检查（只读，但若发现泄漏仍需告警）
    leak = _check_label_safety(ctx, runner)
    if leak is not None:
        return leak

    return ToolResult.success(
        f"pipeline status: initial={summary_metrics['initial_validation_status']}, "
        f"final={summary_metrics['final_validation_status']}",
        status="ok",
        metrics=summary_metrics,
        next_actions=_suggest_next_from_status(status),
    )


def _suggest_next_from_status(status: dict[str, Any]) -> list[str]:
    """根据状态给出下一步工具建议。"""
    stages = status.get("stages", {})
    order = [
        "profile",
        "planner",
        "executor",
        "initial_critic",
        "repair",
        "repaired_critic",
        "final_report",
    ]
    last_done = None
    for s in order:
        if stages.get(s, {}).get("status") not in (None, "pending"):
            last_done = s
    if last_done is None:
        return ["profile_financial_data"]
    nxt = {
        "profile": "create_workflow_plan",
        "planner": "prepare_financial_panel",
        "executor": "validate_financial_panel",
        "initial_critic": "run_safe_remediation",
        "repair": "validate_repaired_panel",
        "repaired_critic": "generate_workflow_report",
        "final_report": "inspect_pipeline_status",
    }
    return [nxt.get(last_done, "inspect_pipeline_status")]


def _tool_profile_financial_data(
    arguments: dict[str, Any], context: Any
) -> ToolResult:
    """profile_financial_data：Stage 1 Data Profiler。"""
    ctx: AgentContext = context
    runner = ctx.get_runner()
    runner.run_profile()
    summ = runner.stages["profile"]["summary"]
    result = ToolResult.success(
        f"profiled {summ.get('n_tables')} tables; {summ.get('total_issues')} issues",
        status=runner.stages["profile"]["status"],
        metrics={
            "n_tables": summ.get("n_tables"),
            "total_issues": summ.get("total_issues"),
        },
        artifacts=_artifacts(
            ctx, [runner.profile_json, runner.profile_md]
        ),
        next_actions=["create_workflow_plan"],
    )
    return _stage_failed(result, runner, "profile")


def _tool_create_workflow_plan(
    arguments: dict[str, Any], context: Any
) -> ToolResult:
    """create_workflow_plan：Stage 2 Workflow Planner。"""
    ctx: AgentContext = context
    runner = ctx.get_runner()
    runner.run_planner()
    summ = runner.stages["planner"]["summary"]
    result = ToolResult.success(
        f"plan: {summ.get('n_workflow_steps')} steps, "
        f"{summ.get('n_validation_checks')} validation checks",
        status=runner.stages["planner"]["status"],
        metrics={
            "n_workflow_steps": summ.get("n_workflow_steps"),
            "n_validation_checks": summ.get("n_validation_checks"),
            "analysis_goal": summ.get("analysis_goal"),
        },
        artifacts=_artifacts(ctx, [runner.plan_json, runner.plan_md]),
        next_actions=["prepare_financial_panel"],
    )
    return _stage_failed(result, runner, "planner")


def _tool_prepare_financial_panel(
    arguments: dict[str, Any], context: Any
) -> ToolResult:
    """prepare_financial_panel：Stage 3 Code Executor。"""
    ctx: AgentContext = context
    runner = ctx.get_runner()
    runner.run_executor()
    summ = runner.stages["executor"]["summary"]
    result = ToolResult.success(
        f"prepared panel: {summ.get('n_rows')} rows x {summ.get('n_columns')} cols; "
        f"pk_unique={summ.get('primary_key_unique')}",
        status=runner.stages["executor"]["status"],
        metrics={
            "n_rows": summ.get("n_rows"),
            "n_columns": summ.get("n_columns"),
            "primary_key_unique": summ.get("primary_key_unique"),
            "date_min": summ.get("date_min"),
            "date_max": summ.get("date_max"),
        },
        artifacts=_artifacts(
            ctx,
            [runner.prepared_panel, runner.data_dictionary, runner.execution_log],
        ),
        next_actions=["validate_financial_panel"],
    )
    return _stage_failed(result, runner, "executor")


def _tool_validate_financial_panel(
    arguments: dict[str, Any], context: Any
) -> ToolResult:
    """validate_financial_panel：Stage 4 initial Validity Critic。"""
    ctx: AgentContext = context
    runner = ctx.get_runner()
    runner.run_initial_critic()
    summ = runner.stages["initial_critic"]["summary"]
    overall = summ.get("overall_status", "unknown")
    result = ToolResult.success(
        f"initial critic: {overall} (passed={summ.get('passed')}, "
        f"warnings={summ.get('warnings')}, failed={summ.get('failed')})",
        status=runner.stages["initial_critic"]["status"],
        metrics={
            "overall_status": overall,
            "total_checks": summ.get("total_checks"),
            "passed": summ.get("passed"),
            "warnings": summ.get("warnings"),
            "failed": summ.get("failed"),
        },
        artifacts=_artifacts(
            ctx,
            [runner.initial_validation_json, runner.initial_validation_md, runner.initial_approved],
        ),
        next_actions=(
            ["run_safe_remediation"] if overall == "failed"
            else ["generate_workflow_report"]
        ),
    )
    # label 安全检查
    leak = _check_label_safety(ctx, runner)
    if leak is not None:
        return leak
    return _stage_failed(result, runner, "initial_critic")


def _tool_run_safe_remediation(
    arguments: dict[str, Any], context: Any
) -> ToolResult:
    """run_safe_remediation：有界多轮 Remediation Agent（仅在 initial failed 时执行）。

    - 若 initial critic 已 passed / passed_with_warnings，返回 not_needed。
    - 必须继续使用现有 max_repair_rounds / max_row_loss_ratio / no_progress /
      manual_review_required / unresolved_checks / label 泄漏保护。
    - 不重写修复策略；不绕过现有安全门。
    """
    ctx: AgentContext = context
    runner = ctx.get_runner()

    # 前置：initial critic 必须已运行
    init_rec = runner.stages.get("initial_critic", {})
    if init_rec.get("status") in (None, "pending"):
        return ToolResult.failure(
            "run_safe_remediation: initial_critic has not run; "
            "call validate_financial_panel first.",
            code="PRECONDITION_NOT_MET",
            status="precondition_not_met",
            retryable=True,
            next_actions=["validate_financial_panel"],
        )

    initial_status = init_rec.get("summary", {}).get("overall_status", "unknown")
    # 若未 failed，无需修复；但仍需生成 no-op 产物（repaired_panel / repair_plan /
    # repair_log / 复审 validation），让后续 validate_repaired_panel / generate_workflow_report
    # 的输入齐全。委托 PipelineRunner.run_noop_repair()（薄公开方法，与 run_full_pipeline
    # 一致），不再触碰 _write_noop_repair_artifacts / _write_repair_history / _mark_skipped
    # 等私有方法。
    if initial_status != "failed":
        runner.run_noop_repair(initial_status, "no_repair_needed")
        return ToolResult.success(
            f"no remediation needed; initial critic status={initial_status}",
            status="not_needed",
            metrics={
                "initial_validation_status": initial_status,
                "repair_rounds": 0,
                "termination_reason": "validation_passed",
                "manual_review_required": False,
                "unresolved_checks": [],
            },
            artifacts=_artifacts(
                ctx,
                [runner.repair_history_json, runner.repaired_panel, runner.repair_plan],
            ),
            next_actions=["validate_repaired_panel"],
        )

    # 委托 PipelineRunner.run_remediation_agent()（薄公开方法 → 现有私有实现）
    runner.run_remediation_agent()
    repair_rec = runner.stages["repair"]

    # label 安全检查（修复后必须复核）
    leak = _check_label_safety(ctx, runner)
    if leak is not None:
        return leak

    status = runner.get_status()
    manual = bool(status.get("manual_review_required"))
    term = status.get("termination_reason")
    unresolved = status.get("unresolved_checks") or []

    result = ToolResult.success(
        f"remediation: rounds={status.get('repair_rounds')}, "
        f"termination={term}, manual_review={manual}",
        status=repair_rec.get("status", "completed"),
        metrics={
            "initial_validation_status": initial_status,
            "repair_rounds": status.get("repair_rounds"),
            "termination_reason": term,
            "manual_review_required": manual,
            "unresolved_checks": unresolved,
            "rows_removed_by_repair": status.get("rows_removed_by_repair"),
            "repaired_panel_rows": status.get("repaired_panel_rows"),
        },
        artifacts=_artifacts(
            ctx,
            [
                runner.repair_history_json,
                runner.repaired_panel,
                runner.repair_plan,
                runner.repair_log,
            ],
        ),
        next_actions=(
            ["inspect_validation_failures"] if manual
            else ["validate_repaired_panel"]
        ),
    )

    # manual_review_required → requires_user_action=True，Runtime 见此即停
    if manual:
        result.requires_user_action = True
        result.ok = False
        result.status = "manual_review_required"
        result.error = None  # 不是错误，是安全停止；保持 requires_user_action
        # 重新构造为 failure 风格但保留 metrics/artifacts
        return ToolResult(
            ok=False,
            status="manual_review_required",
            summary=result.summary,
            metrics=result.metrics,
            artifacts=result.artifacts,
            next_actions=result.next_actions,
            error=None,
            requires_user_action=True,
        )

    # stage_failed
    if repair_rec.get("status") == "failed":
        return _stage_failed(result, runner, "repair")
    return result


def _tool_validate_repaired_panel(
    arguments: dict[str, Any], context: Any
) -> ToolResult:
    """validate_repaired_panel：Stage 6 对 repaired panel 重新运行 Critic。"""
    ctx: AgentContext = context
    runner = ctx.get_runner()
    runner.run_repaired_critic()
    summ = runner.stages["repaired_critic"]["summary"]
    overall = summ.get("overall_status", "unknown")
    result = ToolResult.success(
        f"re-run critic: {overall} (passed={summ.get('passed')}, "
        f"warnings={summ.get('warnings')}, failed={summ.get('failed')})",
        status=runner.stages["repaired_critic"]["status"],
        metrics={
            "overall_status": overall,
            "total_checks": summ.get("total_checks"),
            "passed": summ.get("passed"),
            "warnings": summ.get("warnings"),
            "failed": summ.get("failed"),
        },
        artifacts=_artifacts(
            ctx,
            [runner.final_validation_json, runner.final_validation_md, runner.final_approved],
        ),
        next_actions=["generate_workflow_report"],
    )
    leak = _check_label_safety(ctx, runner)
    if leak is not None:
        return leak
    return _stage_failed(result, runner, "repaired_critic")


def _tool_generate_workflow_report(
    arguments: dict[str, Any], context: Any
) -> ToolResult:
    """generate_workflow_report：Stage 7 Final Report Generator。"""
    ctx: AgentContext = context
    runner = ctx.get_runner()
    runner.run_final_report()
    summ = runner.stages["final_report"]["summary"]
    result = ToolResult.success(
        f"final report: initial={summ.get('initial_validation_status')}, "
        f"final={summ.get('final_validation_status')}, "
        f"rows_removed={summ.get('rows_removed_by_repair')}",
        status=runner.stages["final_report"]["status"],
        metrics={
            "initial_validation_status": summ.get("initial_validation_status"),
            "final_validation_status": summ.get("final_validation_status"),
            "rows_removed_by_repair": summ.get("rows_removed_by_repair"),
            "one_line": summ.get("one_line"),
        },
        artifacts=_artifacts(
            ctx,
            [
                runner.summary_json,
                runner.full_report_md,
                runner.one_page_md,
                runner.artifacts_index,
            ],
        ),
        next_actions=["inspect_pipeline_status"],
    )
    return _stage_failed(result, runner, "final_report")


def _tool_inspect_validation_failures(
    arguments: dict[str, Any], context: Any
) -> ToolResult:
    """inspect_validation_failures：只读当前 run 的 validation JSON，结构化返回失败项。

    返回 overall_status / failed checks / warnings / recommendations / artifact path。
    """
    import json

    ctx: AgentContext = context
    runner = ctx.get_runner()
    # 优先复审报告，其次初始报告
    report_path = (
        runner.final_validation_json
        if runner.final_validation_json.exists()
        else runner.initial_validation_json
    )
    if not report_path.exists():
        return ToolResult.failure(
            "no validation report found; run validate_financial_panel first.",
            code="PRECONDITION_NOT_MET",
            status="precondition_not_met",
            retryable=True,
            next_actions=["validate_financial_panel"],
        )

    with report_path.open("r", encoding="utf-8") as f:
        report = json.load(f)

    overall = report.get("overall_status", "unknown")
    failed_checks = [
        {
            "check_name": c.get("check_name"),
            "category": c.get("category"),
            "description": c.get("description"),
            "evidence": c.get("evidence"),
            "recommendation": c.get("recommendation"),
        }
        for c in report.get("checks", [])
        if c.get("status") == "failed"
    ]
    warnings = [
        {
            "check_name": c.get("check_name"),
            "description": c.get("description"),
            "evidence": c.get("evidence"),
        }
        for c in report.get("checks", [])
        if c.get("status") == "warning"
    ]
    recommendations = [
        c.get("recommendation")
        for c in report.get("checks", [])
        if c.get("status") in ("failed", "warning") and c.get("recommendation")
    ]

    return ToolResult.success(
        f"validation report: {overall}; {len(failed_checks)} failed, "
        f"{len(warnings)} warnings",
        status="ok",
        metrics={
            "overall_status": overall,
            "failed_checks": failed_checks,
            "warnings": warnings,
            "recommendations": recommendations,
            "report_source": "repaired" if report_path == runner.final_validation_json else "initial",
        },
        artifacts=_artifacts(ctx, [report_path]),
        next_actions=(
            ["run_safe_remediation"] if overall == "failed" else ["generate_workflow_report"]
        ),
    )


# ======================================================================
# ToolSpec 定义
# ======================================================================


def _bool_or_default(v: Any) -> Any:
    return v


CONFIGURE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "input_dir": {"type": "string"},
        "analysis_goal": {"type": "string"},
        "auto_repair": {"type": "boolean"},
        "max_repair_rounds": {"type": "integer"},
        "max_row_loss_ratio": {"type": "number"},
    },
    "required": [],
}

EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "required": []}


def build_default_registry_specs() -> list[ToolSpec]:
    """返回 10 个领域工具的 ToolSpec 列表（按 pipeline 顺序）。"""
    return [
        ToolSpec(
            name="configure_workflow",
            description=(
                "Validate the real-market input directory, update the AgentContext, "
                "and create a PipelineRunner isolated to the current run_id. "
                "Does NOT run the pipeline. Does NOT generate synthetic data."
            ),
            input_schema=CONFIGURE_SCHEMA,
            risk_level=RiskLevel.WORKSPACE_WRITE,
            handler=_tool_configure_workflow,
        ),
        ToolSpec(
            name="inspect_pipeline_status",
            description=(
                "Read-only snapshot of the current run's pipeline status "
                "(stage statuses, validation status, repair rounds, label safety)."
            ),
            input_schema=EMPTY_SCHEMA,
            risk_level=RiskLevel.READ,
            handler=_tool_inspect_pipeline_status,
        ),
        ToolSpec(
            name="profile_financial_data",
            description="Stage 1: profile the real market CSVs (schema/missing/duplicates).",
            input_schema=EMPTY_SCHEMA,
            risk_level=RiskLevel.WORKSPACE_WRITE,
            handler=_tool_profile_financial_data,
        ),
        ToolSpec(
            name="create_workflow_plan",
            description="Stage 2: build the workflow plan from profile + analysis goal.",
            input_schema=EMPTY_SCHEMA,
            risk_level=RiskLevel.WORKSPACE_WRITE,
            handler=_tool_create_workflow_plan,
        ),
        ToolSpec(
            name="prepare_financial_panel",
            description="Stage 3: execute the plan to produce the analysis-ready panel.",
            input_schema=EMPTY_SCHEMA,
            risk_level=RiskLevel.WORKSPACE_WRITE,
            handler=_tool_prepare_financial_panel,
        ),
        ToolSpec(
            name="validate_financial_panel",
            description="Stage 4: run the initial Validity Critic (look-ahead/label leakage).",
            input_schema=EMPTY_SCHEMA,
            risk_level=RiskLevel.WORKSPACE_WRITE,
            handler=_tool_validate_financial_panel,
        ),
        ToolSpec(
            name="run_safe_remediation",
            description=(
                "Stage 5: bounded multi-round Remediation Agent. Only runs if the "
                "initial critic failed; otherwise returns not_needed. Respects "
                "max_row_loss_ratio, no_progress, manual_review_required, and label "
                "leakage protection. Sets requires_user_action when manual review is needed."
            ),
            input_schema=EMPTY_SCHEMA,
            risk_level=RiskLevel.GUARDED,
            handler=_tool_run_safe_remediation,
        ),
        ToolSpec(
            name="validate_repaired_panel",
            description="Stage 6: re-run the Validity Critic on the repaired panel.",
            input_schema=EMPTY_SCHEMA,
            risk_level=RiskLevel.WORKSPACE_WRITE,
            handler=_tool_validate_repaired_panel,
        ),
        ToolSpec(
            name="generate_workflow_report",
            description="Stage 7: generate the final workflow report (reads prior artifacts only).",
            input_schema=EMPTY_SCHEMA,
            risk_level=RiskLevel.WORKSPACE_WRITE,
            handler=_tool_generate_workflow_report,
        ),
        ToolSpec(
            name="inspect_validation_failures",
            description=(
                "Read-only: return structured failed checks / warnings / recommendations "
                "from the current run's validation report."
            ),
            input_schema=EMPTY_SCHEMA,
            risk_level=RiskLevel.READ,
            handler=_tool_inspect_validation_failures,
        ),
    ]


def build_default_registry():
    """构造并返回装满 10 个领域工具的 ToolRegistry。"""
    from agent_runtime.registry import build_registry

    return build_registry(build_default_registry_specs())
