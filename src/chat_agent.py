"""Natural Language Agent CLI（Stage 11 Demo）。

把"自然语言 → 真实 LLM → 自主工具调用 → 报告"的完整闭环暴露给终端用户。

调用链::

    用户自然语言
    → OpenAICompatibleModelClient（真实 LLM，OpenAI-compatible tool calling）
    → AgentRuntime（有界 tool-calling 循环 + 重复检测）
    → PolicyEngine（执行前 allow/ask/deny；guarded→ASK）
    → ToolRegistry → PipelineRunner 领域工具
    → ToolResult 回填模型上下文
    → 模型继续或输出最终自然语言总结

用法（PowerShell，从项目根目录运行）::

    python -B src/chat_agent.py `
      --input_dir test_data/real_market_sample `
      --output_base outputs_real `
      --prompt "检查这些真实市场数据，生成建模宽表，必要时安全修复并输出报告"

环境变量（API Key 只从环境变量读取，不写入日志/事件/错误信息）::

    FTA_LLM_API_KEY   API Key（只放进 HTTP Authorization 头）
    FTA_LLM_BASE_URL  OpenAI-compatible base URL
    FTA_LLM_MODEL     模型名

设计原则：
- CLI 只通过 AgentRuntime 调用工具，绝不直接调 PipelineRunner。
- 不打印完整 messages / 隐藏推理 / API Key。
- 审批只决定"是否执行"，执行仍走 PipelineRunner → Remediation Agent，不绕过安全门。
- 本阶段 session 只存在进程内，不实现跨进程持久化。
- 不把 Demo 描述成生产级系统。
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

# 让脚本无论从哪里调用都能 import 同级模块
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from agent_runtime.context import AgentContext  # noqa: E402
from agent_runtime.models import (  # noqa: E402
    AgentEvent,
    EventType,
    StopReason,
)
from agent_runtime.policy import (  # noqa: E402
    ApprovalResponse,
    PolicyAction,
    PolicyConfig,
    PolicyEngine,
)
from agent_runtime.runtime import AgentRuntime  # noqa: E402
from agent_tools.pipeline_tools import build_default_registry  # noqa: E402

# 默认 system prompt 文件（相对项目根）
DEFAULT_SYSTEM_PROMPT = "prompts/financial_agent_system.md"

# 工具调用进度行宽（左对齐工具名）
_TOOL_NAME_WIDTH = 28


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析 CLI 参数。"""
    p = argparse.ArgumentParser(
        description=(
            "Natural-language Financial Table Workflow Agent (Stage 11 demo). "
            "Drives the Pipeline via a real OpenAI-compatible LLM."
        ),
    )
    p.add_argument(
        "--input_dir",
        default="test_data/real_market_sample",
        help="Directory with real market CSVs (default: test_data/real_market_sample).",
    )
    p.add_argument(
        "--output_base",
        default="outputs_real",
        help="Root for run outputs (default: outputs_real). Each run is isolated "
        "under <output_base>/runs/<run_id>/.",
    )
    p.add_argument(
        "--run_id",
        default=None,
        help="Run id (default: auto-generated run_<timestamp>_<short>).",
    )
    p.add_argument(
        "--prompt",
        default=None,
        help="User natural-language request. If omitted, read from stdin.",
    )
    p.add_argument(
        "--model",
        default=None,
        help="Model name (overrides FTA_LLM_MODEL).",
    )
    p.add_argument(
        "--base_url",
        default=None,
        help="OpenAI-compatible base URL (overrides FTA_LLM_BASE_URL).",
    )
    p.add_argument(
        "--max_tool_turns",
        type=int,
        default=12,
        help="Max model tool-calling turns (default: 12).",
    )
    p.add_argument(
        "--auto_approve_remediation",
        action="store_true",
        help="Auto-approve guarded remediation (run_safe_remediation) for live demo. "
        "Execution still respects internal safety gates.",
    )
    p.add_argument(
        "--max_repair_rounds",
        type=int,
        default=3,
        help="Max remediation rounds (default: 3).",
    )
    p.add_argument(
        "--max_row_loss_ratio",
        type=float,
        default=0.05,
        help="Max cumulative deleted rows ratio before manual review (default: 0.05).",
    )
    p.add_argument(
        "--analysis_goal",
        default=None,
        help="Optional downstream analysis goal passed to the planner.",
    )
    return p.parse_args(argv)


def _new_run_id() -> str:
    """生成 run_id（run_<timestamp>_<short>）。"""
    # 不依赖墙钟业务语义；仅用于目录隔离。uuid 提供唯一性。
    short = uuid.uuid4().hex[:8]
    # 用一个稳定可读前缀；时间戳由调用环境保证，这里只用 uuid 保证唯一。
    return f"run_{short}"


def _load_system_prompt(workspace_root: Path) -> str:
    """读取 system prompt 文件；缺失时回退到最小内联 prompt。"""
    path = workspace_root / DEFAULT_SYSTEM_PROMPT
    if path.exists():
        return path.read_text(encoding="utf-8")
    # 最小回退（不依赖文件，保证 Demo 可跑）
    return (
        "You are a financial-table data-preparation agent. Use the provided "
        "Pipeline tools to profile, plan, prepare, validate, repair (if needed), "
        "revalidate, and report. Follow tool dependencies. Never fabricate results "
        "or files. Never treat the label as a feature. Escalate to manual review "
        "when safety gates trip. Do not give investment advice."
    )


def _make_event_printer(output_fn: Callable[[str], None]) -> Callable[[AgentEvent], None]:
    """构造一个 AgentEvent 回调，打印简洁的工具调用进度。

    只打印 tool_call / approval_requested / runtime_stop 的关键行；不打印完整
    messages、隐藏推理或 API Key。
    """

    def _print(event: AgentEvent) -> None:
        et = event.event_type
        pl = event.payload or {}
        if et == EventType.TOOL_CALL.value:
            name = str(pl.get("name", "?"))[:_TOOL_NAME_WIDTH]
            output_fn(f"[tool] {name:<{_TOOL_NAME_WIDTH}} ... running")
        elif et == EventType.TOOL_RESULT.value:
            name = str(pl.get("name", "?"))[:_TOOL_NAME_WIDTH]
            result = pl.get("result") or {}
            status = result.get("status", "?")
            output_fn(f"[tool] {name:<{_TOOL_NAME_WIDTH}} ... {status}")
        elif et == EventType.APPROVAL_REQUESTED.value:
            output_fn(
                "[approval] requested: "
                f"{pl.get('tool_name')} (request_id={pl.get('request_id')})"
            )
        elif et == EventType.RUNTIME_STOP.value:
            output_fn(f"[stop] {pl.get('stop_reason')}")

    return _print


def _resolve_policy() -> PolicyEngine:
    """构造 PolicyEngine。

    本阶段始终用默认策略（read/workspace_write→ALLOW，guarded→ASK，未知→DENY）。
    ``--auto_approve_remediation`` 不改策略，而是在 CLI 层对待审批请求自动回复 approved
    （仍走 ASK 门，执行仍受内部安全门约束）。
    """
    return PolicyEngine()


def _build_model_client(args: argparse.Namespace, workspace_root: Path):
    """构造真实 OpenAICompatibleModelClient。失败时抛 ModelConfigError（由 run_chat 处理）。"""
    from agent_runtime.openai_compatible_client import (
        OpenAICompatibleModelClient,
    )

    system_prompt = _load_system_prompt(workspace_root)
    return OpenAICompatibleModelClient(
        api_key=os.environ.get("FTA_LLM_API_KEY"),
        base_url=args.base_url or os.environ.get("FTA_LLM_BASE_URL"),
        model=args.model or os.environ.get("FTA_LLM_MODEL"),
        system_prompt=system_prompt,
    )


def _handle_approval(
    runtime: AgentRuntime,
    result,
    *,
    auto_approve: bool,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> object:
    """处理 awaiting_approval：交互式 approve/reject 或 auto-approve。

    返回 resume 后的 AgentRunResult。
    """
    pending = result.pending_approval
    if pending is None:
        return result
    output_fn("")
    output_fn("Agent requests:")
    output_fn(f"  {pending.tool_name}")
    output_fn(f"  arguments: {pending.arguments}")
    if auto_approve:
        output_fn("  (auto-approved via --auto_approve_remediation)")
        approved = True
    else:
        # 提示行经 output_fn 输出（便于捕获/重定向），再用 input_fn 读取回答
        output_fn("Approve? [y/N]")
        ans = input_fn("").strip().lower()
        approved = ans in ("y", "yes")
    resp = ApprovalResponse(request_id=pending.request_id, approved=approved)
    return runtime.resume(resp)


def run_chat(
    args: argparse.Namespace,
    *,
    model_client=None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    """运行自然语言 Agent。

    可注入 ``model_client``（测试用 Fake Model）与 ``input_fn`` / ``output_fn``。
    返回退出码：0 成功完成；1 配置/运行错误；2 需要人工介入（manual_review /
    awaiting_approval 被拒绝 / requires_user_action）。
    """
    workspace_root = HERE.parent
    run_id = args.run_id or _new_run_id()

    output_fn("Financial Table Workflow Agent")
    output_fn("")
    output_fn(f"Run: {run_id}")
    output_fn(f"Input: {args.input_dir}")
    output_fn("")

    # 1. AgentContext（校验 input_dir，绝不回退合成数据）
    try:
        ctx = AgentContext.create(
            workspace_root=workspace_root,
            input_dir=args.input_dir,
            output_base=args.output_base,
            run_id=run_id,
            analysis_goal=args.analysis_goal,
            max_repair_rounds=args.max_repair_rounds,
            max_row_loss_ratio=args.max_row_loss_ratio,
        )
    except Exception as exc:  # noqa: BLE001
        output_fn(f"[chat_agent] invalid input: {exc}")
        return 1

    # 2. ToolRegistry（10 个领域工具）
    registry = build_default_registry()

    # 3. PolicyEngine（默认策略：guarded→ASK）
    policy = _resolve_policy()

    # 4. ModelClient（真实 LLM 或注入的 Fake Model）
    if model_client is None:
        from agent_runtime.openai_compatible_client import ModelConfigError

        try:
            model_client = _build_model_client(args, workspace_root)
        except ModelConfigError as exc:
            output_fn(
                f"[chat_agent] model not configured: {exc}\n"
                "Set environment variables FTA_LLM_API_KEY / FTA_LLM_BASE_URL / "
                "FTA_LLM_MODEL (or pass --model / --base_url). See .env.example."
            )
            return 1

    # 5. AgentRuntime（注入事件回调打印进度）
    event_cb = _make_event_printer(output_fn)
    runtime = AgentRuntime(
        model=model_client,
        registry=registry,
        context=ctx,
        policy=policy,
        max_tool_turns=args.max_tool_turns,
        event_callback=event_cb,
    )

    # 6. 用户自然语言请求
    prompt = args.prompt
    if not prompt:
        output_fn("[user] (enter your request, Ctrl+Z+Enter or empty line to finish)")
        try:
            prompt = input_fn("> ")
        except EOFError:
            prompt = ""
        if not prompt:
            output_fn("[chat_agent] empty prompt; nothing to do.")
            return 0

    output_fn("")
    output_fn(f"[user] {prompt}")
    output_fn("")

    # 7. 执行
    try:
        result = runtime.run(prompt)
    except Exception as exc:  # noqa: BLE001
        output_fn(f"[chat_agent] runtime error: {type(exc).__name__}: {exc}")
        return 1

    # 8. 处理 awaiting_approval（可能多次）
    while result.stop_reason == StopReason.AWAITING_APPROVAL:
        result = _handle_approval(
            runtime,
            result,
            auto_approve=args.auto_approve_remediation,
            input_fn=input_fn,
            output_fn=output_fn,
        )

    # 9. 输出最终回答 + run_root + 报告路径
    output_fn("")
    output_fn("[assistant]")
    if result.final_text:
        output_fn(result.final_text)
    else:
        output_fn(f"(no final text; stop_reason={result.stop_reason})")

    run_root = str(ctx.run_root).replace("\\", "/")
    output_fn(f"Run root: {run_root}")

    report_path = _find_report_path(ctx)
    if report_path:
        output_fn(f"Final report: {report_path}")

    # 退出码
    if result.stop_reason == StopReason.COMPLETED:
        return 0
    if result.stop_reason == StopReason.REQUIRES_USER_ACTION:
        output_fn(
            "[chat_agent] stopped: manual review required (requires_user_action)."
        )
        return 2
    output_fn(f"[chat_agent] stopped: {result.stop_reason}")
    return 2


def _find_report_path(ctx: AgentContext) -> str | None:
    """从当前 run 的 runner 读取最终报告路径（若已生成）。"""
    try:
        runner = ctx.get_runner()
    except RuntimeError:
        return None
    if runner.full_report_md.exists():
        return str(runner.full_report_md).replace("\\", "/")
    return None


def main() -> int:
    args = parse_args()
    return run_chat(args)


if __name__ == "__main__":
    raise SystemExit(main())
