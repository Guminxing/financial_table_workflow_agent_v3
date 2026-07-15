"""Agent 工具包（Stage 9 MVP）。

把现有 PipelineRunner 阶段包装成领域工具，供 Agent Runtime 通过 ToolRegistry 调用。

本轮只实现 pipeline 领域工具（见 :mod:`agent_tools.pipeline_tools`）。
不实现任意 shell / 任意 Python 执行 / MCP / 网络工具。
"""

from __future__ import annotations

from .pipeline_tools import build_default_registry, build_default_registry_specs

__all__ = ["build_default_registry", "build_default_registry_specs"]
