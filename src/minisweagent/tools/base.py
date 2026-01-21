"""Base interfaces and result types for tools."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ToolResult:
    """Tool execution result."""

    success: bool
    data: dict[str, Any]
    output: str
    error: str | None = None
    returncode: int = 0


class Tool(Protocol):
    """Tool protocol definition."""

    name: str
    description: str

    def run(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult: ...
