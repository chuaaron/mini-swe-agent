"""Tool registry and command parsing."""

from __future__ import annotations

import shlex
from typing import Any

from minisweagent.tools.base import Tool, ToolResult


class ToolRegistryError(Exception):
    """Base class for tool registry errors."""


class ToolCommandError(ToolRegistryError):
    """Raised when a tool command cannot be parsed."""


class ToolNotFoundError(ToolRegistryError):
    """Raised when a tool name is not registered."""


def parse_tool_command(command: str) -> tuple[str, dict[str, Any]]:
    """Parse a @tool command into (tool_name, args_dict)."""
    if not command.startswith("@tool "):
        raise ToolCommandError("Tool command must start with '@tool '.")

    parts = shlex.split(command[len("@tool ") :])
    if not parts:
        raise ToolCommandError("Tool name missing.")

    tool_name = parts[0]
    args: dict[str, Any] = {}
    idx = 1
    while idx < len(parts):
        token = parts[idx]
        if not token.startswith("--"):
            raise ToolCommandError(f"Unexpected token: {token}")
        key = token[2:]
        if not key:
            raise ToolCommandError("Empty argument name.")
        if key == "queries":
            values: list[str] = []
            next_idx = idx + 1
            while next_idx < len(parts) and not parts[next_idx].startswith("--"):
                values.append(parts[next_idx])
                next_idx += 1
            if not values:
                raise ToolCommandError("Argument 'queries' requires at least one value.")
            existing = args.get(key)
            if existing is None:
                args[key] = values
            elif isinstance(existing, list):
                existing.extend(values)
            else:
                args[key] = [existing, *values]
            idx = next_idx
            continue
        if idx + 1 < len(parts) and not parts[idx + 1].startswith("--"):
            args[key] = parts[idx + 1]
            idx += 2
        else:
            args[key] = True
            idx += 1
    return tool_name, args


class ToolRegistry:
    """Registry for tools and a single execution interface."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def available_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def execute(self, command: str, *, context: dict[str, Any]) -> ToolResult:
        tool_name, args = parse_tool_command(command)
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ToolNotFoundError(f"Tool not found: {tool_name}")
        try:
            return tool.run(args, context)
        except ValueError as exc:
            raise ToolCommandError(str(exc)) from exc
