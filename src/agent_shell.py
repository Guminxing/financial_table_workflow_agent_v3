"""Interactive Agent Shell（第七阶段）。

提供一个可交互的命令行 agent shell，让用户通过简单命令运行、查看状态、
查看失败项、打开报告、调整 analysis_goal。

启动::

    python src/agent_shell.py

非交互测试模式（自动执行一组命令后退出）::

    python src/agent_shell.py --demo_commands

设计原则：
- 不调用任何外部 LLM API，离线可运行。
- 不连接真实券商系统，不获取真实市场数据，不训练模型，不输出投资建议。
- 路径用 pathlib，兼容 Windows，不写死绝对路径。
- 复用 PipelineRunner，不复制粘贴业务逻辑。
- 不删除/重写前六阶段代码。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# 让脚本无论从哪里调用都能 import 同级模块
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from pipeline_runner import PipelineRunner  # noqa: E402

SHELL_VERSION = "0.1"


class AgentShell:
    """交互式 Agent Shell。

    用法::

        shell = AgentShell(input_dir="data/real_market", output_root="outputs_real")
        shell.loop()  # 进入交互循环
    """

    # 模糊命令 -> 标准命令的 intent mapping
    INTENT_ALIASES = {
        "run pipeline": "run all",
        "full run": "run all",
        "fullrun": "run all",
        "summary": "show summary",
        "failures": "show failures",
        "features": "show features",
        "open final report": "open report",
        "open final": "open report",
        "quit": "exit",
        "q": "exit",
    }

    def __init__(
        self,
        input_dir: str | Path = "data/real_market",
        output_root: str | Path = "outputs_real",
        analysis_goal: str | None = None,
        auto_repair: bool = True,
    ) -> None:
        self.input_dir = Path(input_dir)
        self.output_root = Path(output_root)
        self.analysis_goal = analysis_goal
        self.auto_repair = auto_repair
        # 复用一个 runner，但每次 run all 重建以应用最新设置
        self.runner: PipelineRunner | None = None

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def loop(self) -> None:
        """交互式主循环。"""
        self._print_banner()
        while True:
            try:
                raw = input("agent> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                self._say("Goodbye.")
                break
            if not raw:
                continue
            if not self.dispatch(raw):
                break  # exit 命令返回 False，结束循环

    def dispatch(self, raw: str) -> bool:
        """解析并执行一条命令，返回 False 表示应退出 shell。"""
        cmd = self._normalize(raw)
        if cmd is None:
            self._unknown(raw)
            return True

        # set goal <text>
        if cmd.startswith("set goal "):
            self._set_goal(cmd[len("set goal "):])
            return True
        if cmd.startswith("set input_dir "):
            self._set_input_dir(cmd[len("set input_dir "):])
            return True
        if cmd.startswith("set output_root "):
            self._set_output_root(cmd[len("set output_root "):])
            return True

        handlers = {
            "help": self._cmd_help,
            "status": self._cmd_status,
            "run all": self._cmd_run_all,
            "run profile": self._cmd_run_profile,
            "run planner": self._cmd_run_planner,
            "run executor": self._cmd_run_executor,
            "run critic": self._cmd_run_critic,
            "run repair": self._cmd_run_repair,
            "run recritic": self._cmd_run_recritic,
            "run report": self._cmd_run_report,
            "show summary": self._cmd_show_summary,
            "show failures": self._cmd_show_failures,
            "show features": self._cmd_show_features,
            "open report": self._cmd_open_report,
            "open outputs": self._cmd_open_outputs,
            "reset session": self._cmd_reset_session,
            "exit": self._cmd_exit,
            "quit": self._cmd_exit,
        }
        handler = handlers.get(cmd)
        if handler is None:
            self._unknown(raw)
            return True
        return handler()

    # ------------------------------------------------------------------
    # 命令实现
    # ------------------------------------------------------------------

    def _cmd_help(self) -> bool:
        print()
        print("Available commands:")
        print("  help                       Show this help.")
        print("  status                     Show current pipeline status.")
        print("  set goal <text>            Set the analysis goal.")
        print("  set input_dir <path>       Set the input data directory.")
        print("  set output_root <path>     Set the output root directory.")
        print("  run all                    Run the full pipeline.")
        print("  run profile                Run Stage 1 Data Profiler only.")
        print("  run planner               Run Stage 2 Workflow Planner only.")
        print("  run executor              Run Stage 3 Code Executor only.")
        print("  run critic                Run the initial Validity Critic.")
        print("  run repair                Run the Repair Loop.")
        print("  run recritic              Re-run the Critic on the repaired panel.")
        print("  run report                Run the Final Report Generator.")
        print("  show summary              Print the final workflow summary.")
        print("  show failures             List failed/warning checks.")
        print("  show features             Show approved features, label, exclusions.")
        print("  open report               Open the final report (Windows).")
        print("  open outputs              Open the outputs folder.")
        print("  reset session             Reset shell session state (keeps outputs).")
        print("  exit / quit               Exit the shell.")
        print()
        print("Aliases: 'run pipeline'/'full run' = run all; 'summary' = show summary;")
        print("         'failures' = show failures; 'features' = show features;")
        print("         'open final report' = open report.")
        print()
        return True

    def _cmd_status(self) -> bool:
        runner = self._get_runner()
        status = runner.get_status()
        print()
        print("Pipeline status:")
        print(f"  input_dir:   {status['input_dir']}")
        print(f"  output_root: {status['output_root']}")
        print(f"  analysis_goal: {status['analysis_goal']}")
        print(f"  auto_repair: {status['auto_repair']}")
        print()
        print("Stages:")
        for s in [
            "profile",
            "planner",
            "executor",
            "initial_critic",
            "repair",
            "repaired_critic",
            "final_report",
        ]:
            rec = status["stages"][s]
            display = rec["display"]
            dots = "." * max(2, 38 - len(display))
            print(f"  {display} {dots} {rec['status']}")
        print()
        print(f"  initial validation status: {status['initial_validation_status'] or 'n/a'}")
        print(f"  final validation status:   {status['final_validation_status'] or 'n/a'}")
        if status["prepared_panel_rows"] is not None:
            print(f"  prepared panel rows: {status['prepared_panel_rows']}")
        if status["repaired_panel_rows"] is not None:
            print(f"  repaired panel rows: {status['repaired_panel_rows']}")
        if status["rows_removed_by_repair"] is not None:
            print(f"  rows removed by repair: {status['rows_removed_by_repair']}")
        # v2 Remediation Agent 字段（get_status 已从 repair_history.json 恢复历史状态）
        print(f"  repair rounds: {status.get('repair_rounds', 0)}")
        print(f"  termination reason: {status.get('termination_reason') or 'n/a'}")
        if status.get("manual_review_required"):
            print("  manual review required: YES")
        unresolved = status.get("unresolved_checks") or []
        if unresolved:
            print(f"  unresolved checks: {unresolved}")
        print(f"  approved features: {len(status['approved_feature_columns'])}")
        print(f"  label column: {status['label_column']}")
        label_in = status["label_in_approved_features"]
        print(
            f"  label in features: {'YES (leak!)' if label_in else 'no (safe)'}"
        )
        if status["final_report_path"]:
            print(f"  final report: {status['final_report_path']}")
        print()
        self._suggest_next(status)
        return True

    def _set_goal(self, text: str) -> bool:
        text = text.strip().strip('"').strip("'")
        if not text:
            self._say("Goal text is empty. Usage: set goal <text>")
            return True
        self.analysis_goal = text
        self.runner = None  # 设置变更，下次重建 runner
        self._say(f"Analysis goal set: {text}")
        self._say("Next suggested step: run all (or run planner).")
        return True

    def _set_input_dir(self, path: str) -> bool:
        path = path.strip().strip('"').strip("'")
        if not path:
            self._say("Path is empty. Usage: set input_dir <path>")
            return True
        self.input_dir = Path(path)
        self.runner = None
        self._say(f"input_dir set to: {self.input_dir}")
        return True

    def _set_output_root(self, path: str) -> bool:
        path = path.strip().strip('"').strip("'")
        if not path:
            self._say("Path is empty. Usage: set output_root <path>")
            return True
        self.output_root = Path(path)
        self.runner = None
        self._say(f"output_root set to: {self.output_root}")
        return True

    def _cmd_run_all(self) -> bool:
        self._say("Running full pipeline ...")
        runner = self._get_runner()
        runner.run_full_pipeline()
        runner.save_session_log()
        status = runner.get_status()
        self._say(f"Final status: {status['final_validation_status'] or 'n/a'}")
        if status["final_report_path"]:
            self._say(f"Final report generated: {status['final_report_path']}")
            self._say("You can use 'open report'.")
        else:
            self._suggest_next(status)
        return True

    def _cmd_run_profile(self) -> bool:
        runner = self._get_runner()
        runner.run_profile()
        return self._after_single_stage("profile")

    def _cmd_run_planner(self) -> bool:
        runner = self._get_runner()
        runner.run_planner()
        return self._after_single_stage("planner")

    def _cmd_run_executor(self) -> bool:
        runner = self._get_runner()
        runner.run_executor()
        return self._after_single_stage("executor")

    def _cmd_run_critic(self) -> bool:
        runner = self._get_runner()
        runner.run_initial_critic()
        return self._after_single_stage("initial_critic")

    def _cmd_run_repair(self) -> bool:
        runner = self._get_runner()
        runner.run_repair()
        return self._after_single_stage("repair")

    def _cmd_run_recritic(self) -> bool:
        runner = self._get_runner()
        runner.run_repaired_critic()
        return self._after_single_stage("repaired_critic")

    def _cmd_run_report(self) -> bool:
        runner = self._get_runner()
        runner.run_final_report()
        return self._after_single_stage("final_report")

    def _cmd_show_summary(self) -> bool:
        summary_path = self._get_runner().summary_json
        if not summary_path.exists():
            self._say(
                f"Summary not found: {summary_path}. Run 'run all' or 'run report' first."
            )
            return True
        with summary_path.open("r", encoding="utf-8") as f:
            s = json.load(f)
        cl = s.get("closed_loop_result", {})
        print()
        print("Final workflow summary:")
        print(f"  initial validation status: {s.get('initial_validation_status')}")
        print(f"  final validation status:   {s.get('final_validation_status')}")
        print(f"  rows removed by repair:    {s.get('rows_removed_by_repair')}")
        # v2 Remediation Agent 字段（从 repair_history.json 读，若存在）
        rh_path = self._get_runner().repair_history_json
        if rh_path.exists():
            with rh_path.open("r", encoding="utf-8") as f:
                rh = json.load(f)
            print(f"  repair rounds:             {rh.get('repair_rounds', 0)}")
            print(f"  termination reason:        {rh.get('termination_reason')}")
            if rh.get("manual_review_required"):
                print("  manual review required:   YES")
            unc = rh.get("unresolved_checks") or []
            if unc:
                print(f"  unresolved checks:         {unc}")
        if cl:
            print(f"  initial rows:   {cl.get('initial_rows')}")
            print(f"  repaired rows:  {cl.get('repaired_rows')}")
            print(f"  failed check:   {cl.get('failed_check')}")
            print(f"  label not in approved features: {cl.get('label_not_in_approved_features')}")
            print(f"  one-line: {cl.get('one_line')}")
        print(f"  approved features: {s.get('approved_feature_columns', [])}")
        print(f"  label column: {s.get('label_column')}")
        print()
        return True

    def _cmd_show_failures(self) -> bool:
        runner = self._get_runner()
        # 优先复审报告，其次初始报告
        for label, path in [
            ("final (repaired)", runner.final_validation_json),
            ("initial", runner.initial_validation_json),
        ]:
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8") as f:
                report = json.load(f)
            print()
            print(f"Validation report [{label}]: {path}")
            print(f"  overall_status: {report.get('overall_status')}")
            s = report.get("summary", {})
            print(
                f"  total={s.get('total_checks')} "
                f"passed={s.get('passed')} "
                f"warnings={s.get('warnings')} "
                f"failed={s.get('failed')}"
            )
            print("  failed / warning checks:")
            any_item = False
            for c in report.get("checks", []):
                if c.get("status") in ("failed", "warning"):
                    any_item = True
                    print(
                        f"    [{c.get('status')}] {c.get('check_name')} "
                        f"({c.get('category')}): {c.get('description')}"
                    )
                    ev = c.get("evidence", {})
                    if ev:
                        print(f"        evidence: {ev}")
                    rec = c.get("recommendation")
                    if rec:
                        print(f"        recommendation: {rec}")
            if not any_item:
                print("    (none)")
            print()
        if not runner.final_validation_json.exists() and not runner.initial_validation_json.exists():
            self._say("No validation report found. Run 'run critic' first.")
        return True

    def _cmd_show_features(self) -> bool:
        runner = self._get_runner()
        approved_path = (
            runner.final_approved
            if runner.final_approved.exists()
            else runner.initial_approved
        )
        if not approved_path.exists():
            self._say(
                f"Approved features not found: {approved_path}. Run 'run critic' first."
            )
            return True
        with approved_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        approved = data.get("approved_feature_columns", [])
        excluded = data.get("excluded_columns", [])
        label_col = data.get("label_column", "label_next_5d")
        label_in = label_col in approved
        print()
        print(f"Approved features ({len(approved)}):")
        for c in approved:
            print(f"  - {c}")
        print()
        print(f"Label column: {label_col}")
        print(
            f"label_next_5d in features? {'YES (leak!)' if label_in else 'no'}"
        )
        if label_in:
            print("  WARNING: label leakage detected!")
        else:
            print("  label_next_5d is NOT in approved features (safe).")
        print()
        print(f"Excluded columns ({len(excluded)}): {excluded}")
        print()
        return True

    def _cmd_open_report(self) -> bool:
        runner = self._get_runner()
        report = runner.full_report_md
        if not report.exists():
            self._say(
                f"Final report not found: {report}. Run 'run all' or 'run report' first."
            )
            return True
        self._say(f"Opening final report: {report}")
        self._open_path(report)
        return True

    def _cmd_open_outputs(self) -> bool:
        out = self.output_root
        if not out.exists():
            self._say(f"Outputs folder not found: {out}. Run a stage first.")
            return True
        self._say(f"Opening outputs folder: {out}")
        self._open_path(out)
        return True

    def _cmd_reset_session(self) -> bool:
        self.runner = None
        self.analysis_goal = None
        self.auto_repair = True
        self._say("Session state reset (outputs on disk are kept).")
        self._say("Current input_dir: " + str(self.input_dir))
        self._say("Current output_root: " + str(self.output_root))
        return True

    def _cmd_exit(self) -> bool:
        self._say("Goodbye.")
        return False

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _get_runner(self) -> PipelineRunner:
        """获取/重建 runner，应用当前 input_dir/output_root/goal 设置。"""
        if self.runner is None:
            self.runner = PipelineRunner(
                input_dir=self.input_dir,
                output_root=self.output_root,
                analysis_goal=self.analysis_goal,
                auto_repair=self.auto_repair,
            )
        return self.runner

    def _after_single_stage(self, stage: str) -> bool:
        """单阶段运行后的 agent-like 反馈与下一步建议。"""
        runner = self._get_runner()
        rec = runner.stages[stage]
        status = rec["status"]
        if status == "failed":
            self._say(f"{rec['display']} failed: {rec.get('error_message')}")
            self._say("Fix the error and re-run this stage.")
            return True
        # 阶段成功后的下一步建议
        suggestions = {
            "profile": "Profiler completed. Next suggested step: run planner.",
            "planner": "Planner completed. Next suggested step: run executor.",
            "executor": "Executor completed. Next suggested step: run critic.",
            "initial_critic": None,  # 动态
            "repair": "Repair completed. Next suggested step: run recritic.",
            "repaired_critic": None,  # 动态
            "final_report": "Final report generated. You can use 'open report'.",
        }
        if stage == "initial_critic":
            summ = rec.get("summary", {})
            overall = summ.get("overall_status", "unknown")
            if overall == "failed":
                self._say(
                    f"Critic found failed checks (status={overall}). "
                    "Next suggested step: run repair."
                )
            else:
                self._say(
                    f"Critic status={overall}. No repair needed; "
                    "next suggested step: run report."
                )
        elif stage == "repaired_critic":
            summ = rec.get("summary", {})
            overall = summ.get("overall_status", "unknown")
            self._say(f"Re-run Critic status={overall}.")
            if overall in ("passed", "passed_with_warnings"):
                self._say("Next suggested step: run report.")
            else:
                self._say("Critic still failed; consider further repair or review.")
        else:
            msg = suggestions.get(stage)
            if msg:
                self._say(msg)
        return True

    def _suggest_next(self, status: dict[str, Any]) -> None:
        """根据整体状态给出下一步建议。"""
        stages = status["stages"]
        final_status = status["final_validation_status"]
        # 找到最后一个非 pending 的阶段
        last_done = None
        for s in [
            "profile",
            "planner",
            "executor",
            "initial_critic",
            "repair",
            "repaired_critic",
            "final_report",
        ]:
            if stages[s]["status"] != "pending":
                last_done = s
        if last_done is None:
            self._say("No stages run yet. Next suggested step: run all (or run profile).")
            return
        if last_done == "final_report":
            self._say("Final report generated. You can use 'open report'.")
            return
        if last_done == "repaired_critic":
            if final_status in ("passed", "passed_with_warnings"):
                self._say("Next suggested step: run report.")
            else:
                self._say("Critic still failed; consider further repair or review.")
            return
        if last_done == "initial_critic":
            overall = stages["initial_critic"]["summary"].get("overall_status")
            if overall == "failed":
                self._say("Critic found failed checks. Next suggested step: run repair.")
            else:
                self._say("Critic passed. Next suggested step: run report.")
            return
        nxt = {
            "profile": "run planner",
            "planner": "run executor",
            "executor": "run critic",
            "repair": "run recritic",
        }
        self._say(f"Next suggested step: {nxt.get(last_done, 'run all')}.")

    def _normalize(self, raw: str) -> str | None:
        """把用户输入归一化为标准命令（含 intent mapping）。"""
        cmd = raw.strip().lower()
        if not cmd:
            return None
        # 精确别名
        if cmd in self.INTENT_ALIASES:
            return self.INTENT_ALIASES[cmd]
        return cmd

    def _unknown(self, raw: str) -> None:
        print(
            "I did not understand that command. Type 'help' for available commands."
        )

    def _say(self, msg: str) -> None:
        """agent-like 自然语言反馈。"""
        print(f"[agent] {msg}")

    def _open_path(self, path: Path) -> None:
        """跨平台打开文件/文件夹。Windows 用 os.startfile，否则打印路径。"""
        p = str(path)
        if os.name == "nt":  # Windows
            try:
                os.startfile(p)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                self._say(f"Could not open {p}: {exc}")
        elif sys.platform == "darwin":
            import subprocess

            subprocess.run(["open", p], check=False)
        else:
            self._say(f"Open manually: {p}")

    def _print_banner(self) -> None:
        print("Financial Table Workflow Agent Shell")
        print("Type 'help' to see available commands.")
        print(f"Current input_dir: {self.input_dir}")
        print(f"Current output_root: {self.output_root}")
        print()


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Interactive Agent Shell for the Financial Table Workflow Agent."
    )
    p.add_argument(
        "--input_dir",
        default="data/real_market",
        help="Input data directory (default: data/real_market)",
    )
    p.add_argument(
        "--output_root",
        default="outputs_real",
        help="Output root directory (default: outputs_real)",
    )
    p.add_argument(
        "--analysis_goal",
        default=None,
        help="Downstream analysis goal (default: planner default).",
    )
    p.add_argument(
        "--demo_commands",
        action="store_true",
        help="Run a non-interactive demo: status, show summary, show failures, "
        "show features, then exit.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    shell = AgentShell(
        input_dir=args.input_dir,
        output_root=args.output_root,
        analysis_goal=args.analysis_goal,
    )

    if args.demo_commands:
        # 非交互模式：自动执行一组只读命令后退出
        demo = ["status", "show summary", "show failures", "show features"]
        print("[agent_shell] demo_commands mode (non-interactive)")
        for cmd in demo:
            print(f"\nagent> {cmd}")
            shell.dispatch(cmd)
        print("\n[agent_shell] demo_commands finished.")
        return 0

    shell.loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
