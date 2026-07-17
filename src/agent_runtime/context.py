"""AgentContext + run_id 隔离（Stage 9 MVP）。

为每次 Agent run 提供独立的运行目录与上下文，确保不同 run_id 的产物互不干扰。

核心约束（写在代码中，不只写在文档里）：

1. ``run_id`` 必须可验证和规范化；禁止包含 ``..``、``/``、``\\`` 或路径穿越。
2. ``run_root`` 必须严格位于 ``output_base / "runs" / run_id``。
3. 不允许 Agent 工具清理整个 outputs_real。
4. 不允许 Agent 工具覆盖原始输入 CSV。
5. ``input_dir`` 不存在、为空或缺少必要 CSV 时返回明确错误（**仅当显式传入
   input_dir 或进入需要输入的阶段时校验**；Stage 12 起支持"先抓取再 configure"
   的无 input_dir 启动状态）。
6. 绝不创建合成数据（本模块不生成任何 CSV）。
7. 不同 run_id 的产物不能互相读取或恢复（每个 run 一个独立 PipelineRunner）。
8. 旧 PipelineRunner API 继续接受普通 output_root（不强制 runs/ 结构）。

Stage 12 增量（自然语言抓取真实数据）：
- ``AgentContext`` 支持"尚未配置输入目录"的启动状态（``input_dir=None``）。
- ``create_without_input_dir`` 工厂用于"先自然语言抓取，再 configure"流程。
- ``set_input_dir`` 在 fetch 成功后把当前 run 的 raw_data 设为 input_dir，
  并做路径边界检查（raw_data 必须位于 run_root 之下）。
- 只有以下情况才校验五张 CSV：用户显式传入 ``--input_dir``、
  ``configure_workflow`` 准备创建 PipelineRunner、Pipeline 阶段开始运行。
- 没有 input_dir 时，profile/plan/prepare 等工具应返回清晰的
  ``PRECONDITION_NOT_MET``（由工具层实现，本模块只提供状态）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅用于类型提示，避免运行时循环 import
    from pipeline_runner import PipelineRunner


# run_id 规范：run_YYYYMMDD_HHMMSS_<short-id>，或测试中传入的固定 run_id。
# 允许字符：字母、数字、下划线、短横线。禁止任何路径分隔符与点号。
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# Executor 必需的 5 张原始 CSV（与 src/executor.py 的 T_* 常量一致）
REQUIRED_INPUT_CSVS = (
    "price.csv",
    "volume.csv",
    "fundamentals.csv",
    "industry.csv",
    "calendar.csv",
)


class RunIdError(ValueError):
    """run_id 非法（含路径穿越字符 / 非法字符 / 为空）。"""


class InputDirError(FileNotFoundError):
    """input_dir 不存在、为空或缺少必要 CSV。"""


def normalize_run_id(run_id: str) -> str:
    """规范化并校验 run_id。

    规则：
    - 去除首尾空白。
    - 必须非空。
    - 禁止包含 ``..``、``/``、``\\``（路径穿越防护）。
    - 必须匹配 ``^[A-Za-z0-9][A-Za-z0-9_-]*$``（首字符为字母/数字，其余允许
      字母/数字/下划线/短横线）。

    返回规范化后的 run_id。非法时抛 :class:`RunIdError`。
    """
    if run_id is None:
        raise RunIdError("run_id is None")
    rid = str(run_id).strip()
    if not rid:
        raise RunIdError("run_id is empty")
    # 路径穿越防护：显式拒绝 .. / 斜杠 / 反斜杠
    if ".." in rid or "/" in rid or "\\" in rid:
        raise RunIdError(
            f"run_id must not contain '..', '/' or '\\': got {rid!r}"
        )
    if not _RUN_ID_PATTERN.match(rid):
        raise RunIdError(
            f"run_id must match [A-Za-z0-9][A-Za-z0-9_-]* (no path separators, "
            f"no leading '-' or '_'): got {rid!r}"
        )
    return rid


def validate_input_dir(input_dir: str | Path) -> Path:
    """校验 input_dir：必须存在、非空、且包含全部必需 CSV。

    返回绝对 Path。非法时抛 :class:`InputDirError`（FileNotFoundError 子类），
    给出可操作错误信息，**绝不**回退到合成数据。
    """
    d = Path(input_dir).resolve()
    if not d.exists():
        raise InputDirError(
            f"input_dir does not exist: {d}. "
            "Download real market data first, e.g.:\n"
            "  python -B src/run_fetch_real_data.py --tickers 600519 "
            "--start_date 2024-01-01 --end_date 2024-01-10 "
            "--output_dir data/real_market "
            "--no_snapshot_fundamentals"
        )
    if not d.is_dir():
        raise InputDirError(f"input_dir is not a directory: {d}")
    csv_files = sorted(d.glob("*.csv"))
    if not csv_files:
        raise InputDirError(
            f"no CSV files in {d}. "
            "Download real market data first (see run_fetch_real_data.py); "
            "synthetic sample data generation has been removed in v3."
        )
    missing = [name for name in REQUIRED_INPUT_CSVS if not (d / name).exists()]
    if missing:
        raise InputDirError(
            f"input_dir {d} is missing required CSV files: {missing}. "
            "All of price/volume/fundamentals/industry/calendar are required."
        )
    return d


class AgentContext:
    """一次 Agent run 的上下文。

    持有：
    - ``workspace_root``: 项目根（用于定位 src/executor.py 等）。
    - ``input_dir``: 真实市场数据目录（只读，绝不覆盖）。
      Stage 12 起可为 ``None``（"先抓取再 configure"启动状态）。
    - ``output_base``: 产物根（如 outputs_real）。
    - ``run_id``: 规范化后的 run_id。
    - ``run_root``: ``output_base / "runs" / run_id``（严格隔离）。
    - ``analysis_goal`` / ``auto_repair`` / ``max_repair_rounds`` /
      ``max_row_loss_ratio``: 传给 PipelineRunner 的配置。
    - ``runner``: 当前 run 专属的 PipelineRunner 实例（configure 后创建）。

    用法（已有 CSV 模式）::

        ctx = AgentContext.create(
            workspace_root=Path("..."),
            input_dir="data/real_market",
            output_base="outputs_real",
            run_id="run_20260715_120000_ab12",
        )
        ctx.configure_runner(analysis_goal=None, auto_repair=True, ...)
        runner = ctx.runner  # PipelineRunner，output_root == run_root

    用法（自然语言抓取模式，Stage 12）::

        ctx = AgentContext.create_without_input_dir(
            workspace_root=Path("..."),
            output_base="outputs_real",
            run_id="run_xxx",
        )
        # 模型先调 fetch_real_market_data → ctx.set_input_dir(raw_data)
        # 再调 configure_workflow → ctx.configure_runner(...)
    """

    def __init__(
        self,
        workspace_root: str | Path,
        input_dir: str | Path | None,
        output_base: str | Path,
        run_id: str,
        analysis_goal: str | None = None,
        auto_repair: bool = True,
        max_repair_rounds: int = 3,
        max_row_loss_ratio: float = 0.05,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        # input_dir 可为 None（Stage 12 无 input_dir 启动状态）
        self.input_dir: Path | None = (
            Path(input_dir).resolve() if input_dir is not None else None
        )
        self.output_base = Path(output_base).resolve()
        self.run_id = normalize_run_id(run_id)
        self.analysis_goal = analysis_goal
        self.auto_repair = bool(auto_repair)
        self.max_repair_rounds = int(max_repair_rounds)
        self.max_row_loss_ratio = float(max_row_loss_ratio)
        # run_root 严格位于 output_base / "runs" / run_id
        self.run_root = (self.output_base / "runs" / self.run_id).resolve()

        # 防御性：确保 run_root 确实在 output_base 之下（resolve 后比较）
        try:
            self.run_root.relative_to(self.output_base)
        except ValueError as exc:
            raise RunIdError(
                f"run_root {self.run_root} must be inside output_base "
                f"{self.output_base}; run_id may be malicious"
            ) from exc

        self.runner: "PipelineRunner | None" = None

    # ------------------------------------------------------------------
    # 工厂
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        workspace_root: str | Path,
        input_dir: str | Path,
        output_base: str | Path,
        run_id: str,
        analysis_goal: str | None = None,
        auto_repair: bool = True,
        max_repair_rounds: int = 3,
        max_row_loss_ratio: float = 0.05,
    ) -> "AgentContext":
        """创建并校验 AgentContext（已有 CSV 模式）。

        先校验 input_dir（不存在/为空/缺 CSV 时明确失败，绝不生成合成数据），
        再校验 run_id（路径穿越防护），最后构造 run_root。
        """
        validated_input = validate_input_dir(input_dir)
        return cls(
            workspace_root=workspace_root,
            input_dir=validated_input,
            output_base=output_base,
            run_id=run_id,
            analysis_goal=analysis_goal,
            auto_repair=auto_repair,
            max_repair_rounds=max_repair_rounds,
            max_row_loss_ratio=max_row_loss_ratio,
        )

    @classmethod
    def create_without_input_dir(
        cls,
        workspace_root: str | Path,
        output_base: str | Path,
        run_id: str,
        analysis_goal: str | None = None,
        auto_repair: bool = True,
        max_repair_rounds: int = 3,
        max_row_loss_ratio: float = 0.05,
    ) -> "AgentContext":
        """创建"尚未配置输入目录"的 AgentContext（Stage 12 自然语言抓取模式）。

        - 不校验 input_dir（因为还没有）。
        - 校验 run_id（路径穿越防护）。
        - 构造 run_root。
        - 后续由 fetch_real_market_data 成功后调 :meth:`set_input_dir` 设置。
        - configure_workflow 在 input_dir 仍为 None 时必须明确失败
          （建议先调用 fetch_real_market_data）。
        """
        return cls(
            workspace_root=workspace_root,
            input_dir=None,
            output_base=output_base,
            run_id=run_id,
            analysis_goal=analysis_goal,
            auto_repair=auto_repair,
            max_repair_rounds=max_repair_rounds,
            max_row_loss_ratio=max_row_loss_ratio,
        )

    # ------------------------------------------------------------------
    # input_dir 生命周期（Stage 12）
    # ------------------------------------------------------------------

    def set_input_dir(self, input_dir: str | Path) -> Path:
        """fetch 成功后把当前 run 的 raw_data 设为 input_dir。

        - 校验目标目录存在且包含全部必需 CSV（绝不回退合成数据）。
        - 路径边界检查：raw_data 必须位于当前 run_root 之下（fetch 工具只允许
          写当前 run 的 raw_data，禁止路径穿越、禁止写出 run_root）。
        - 设置后 ``ctx.input_dir`` 指向该 raw_data；configure_workflow 随后
          使用这个 input_dir 创建 PipelineRunner。
        - 原始抓取 CSV 只读，后续 Pipeline 不得覆盖它们（PipelineRunner 只写
          run_root 下的派生产物，不写 input_dir）。
        """
        validated = validate_input_dir(input_dir)
        # 路径边界检查：raw_data 必须位于 run_root 之下
        try:
            validated.relative_to(self.run_root)
        except ValueError as exc:
            raise ValueError(
                f"input_dir {validated} is outside current run_root "
                f"{self.run_root}; fetch tool may only write inside its own "
                f"run_root/raw_data"
            ) from exc
        self.input_dir = validated
        return validated

    def has_input_dir(self) -> bool:
        """是否已配置有效 input_dir。"""
        return self.input_dir is not None

    # ------------------------------------------------------------------
    # runner 生命周期
    # ------------------------------------------------------------------

    def configure_runner(
        self,
        analysis_goal: str | None = ...,
        auto_repair: bool = ...,
        max_repair_rounds: int = ...,
        max_row_loss_ratio: float = ...,
    ) -> "PipelineRunner":
        """创建只属于当前 run_id/run_root 的 PipelineRunner。

        - 不执行 pipeline。
        - 不生成模拟数据。
        - output_root 严格等于 run_root。
        - 任何参数传 ``...``（默认哨兵）表示沿用构造时的值。
        - Stage 12：input_dir 未配置时抛 RuntimeError（configure_workflow 工具
          会先捕获并返回 PRECONDITION_NOT_MET，建议先 fetch_real_market_data）。
        """
        from pipeline_runner import PipelineRunner  # 局部 import 避免循环

        if analysis_goal is not ...:
            self.analysis_goal = analysis_goal
        if auto_repair is not ...:
            self.auto_repair = bool(auto_repair)
        if max_repair_rounds is not ...:
            self.max_repair_rounds = int(max_repair_rounds)
        if max_row_loss_ratio is not ...:
            self.max_row_loss_ratio = float(max_row_loss_ratio)

        # Stage 12：configure 前必须有有效 input_dir
        if self.input_dir is None:
            raise RuntimeError(
                "configure_runner: input_dir is not configured for this run. "
                "Call fetch_real_market_data first (natural-language fetch mode), "
                "or pass --input_dir (existing-CSV mode)."
            )

        # 确保 run_root 存在（PipelineRunner 也会 mkdir，但显式创建更清晰）
        self.run_root.mkdir(parents=True, exist_ok=True)

        self.runner = PipelineRunner(
            input_dir=self.input_dir,
            output_root=self.run_root,
            analysis_goal=self.analysis_goal,
            auto_repair=self.auto_repair,
            max_repair_rounds=self.max_repair_rounds,
            max_row_loss_ratio=self.max_row_loss_ratio,
        )
        return self.runner

    def get_runner(self) -> "PipelineRunner":
        """返回当前 runner；未 configure 时抛 RuntimeError。"""
        if self.runner is None:
            raise RuntimeError(
                "PipelineRunner not configured for this run. "
                "Call configure_workflow tool / ctx.configure_runner() first."
            )
        return self.runner

    # ------------------------------------------------------------------
    # 路径安全辅助（供工具使用）
    # ------------------------------------------------------------------

    def ensure_artifact_in_run_root(self, path: str | Path) -> str:
        """校验产物路径属于当前 run_root，返回正斜杠相对/绝对路径串。

        工具返回的 artifact path 必须在 run_root 之下；否则视为越权，抛 ValueError。
        """
        p = Path(path).resolve()
        try:
            p.relative_to(self.run_root)
        except ValueError as exc:
            raise ValueError(
                f"artifact path {p} is outside current run_root {self.run_root}; "
                "tool may only write inside its own run_root"
            ) from exc
        return str(p).replace("\\", "/")

    def ensure_path_in_run_root(self, path: str | Path) -> Path:
        """校验路径属于当前 run_root，返回 resolve 后的绝对 Path。

        供 fetch 工具等需要写 run_root 子目录（如 raw_data）的场景使用：
        禁止路径穿越，禁止写出当前 run_root。
        """
        p = Path(path).resolve()
        try:
            p.relative_to(self.run_root)
        except ValueError as exc:
            raise ValueError(
                f"path {p} is outside current run_root {self.run_root}; "
                "tool may only write inside its own run_root"
            ) from exc
        return p

    def to_dict(self) -> dict[str, Any]:
        """可序列化摘要（不含 runner 对象）。"""
        return {
            "workspace_root": str(self.workspace_root).replace("\\", "/"),
            "input_dir": (
                str(self.input_dir).replace("\\", "/")
                if self.input_dir is not None
                else None
            ),
            "output_base": str(self.output_base).replace("\\", "/"),
            "run_id": self.run_id,
            "run_root": str(self.run_root).replace("\\", "/"),
            "analysis_goal": self.analysis_goal,
            "auto_repair": self.auto_repair,
            "max_repair_rounds": self.max_repair_rounds,
            "max_row_loss_ratio": self.max_row_loss_ratio,
            "runner_configured": self.runner is not None,
        }
