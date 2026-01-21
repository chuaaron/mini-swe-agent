"""Agent variant that supports @tool calls alongside bash."""

from __future__ import annotations

import re
import subprocess
import time

from jinja2 import StrictUndefined, Template
from pydantic import BaseModel

from minisweagent import Environment, Model
from minisweagent.tools.registry import ToolRegistry, ToolRegistryError


class ToolAgentConfig(BaseModel):
    system_template: str
    instance_template: str
    timeout_template: str
    format_error_template: str
    action_observation_template: str
    tool_format_error_template: str
    tool_error_template: str
    action_regex: str = r"```bash\s*\n(.*?)\n```"
    step_limit: int = 0
    cost_limit: float = 3.0


class NonTerminatingException(Exception):
    """Raised for conditions that can be handled by the agent."""


class FormatError(NonTerminatingException):
    """Raised when the LM output is not in the expected format."""


class ExecutionTimeoutError(NonTerminatingException):
    """Raised when the action execution timed out."""


class ToolFormatError(NonTerminatingException):
    """Raised when a tool command is malformed."""


class ToolExecutionError(NonTerminatingException):
    """Raised when a tool fails to execute."""


class TerminatingException(Exception):
    """Raised for conditions that terminate the agent."""


class Submitted(TerminatingException):
    """Raised when the LM declares that the agent has finished its task."""


class LimitsExceeded(TerminatingException):
    """Raised when the agent has reached its cost or step limit."""


class ToolAgent:
    def __init__(
        self,
        model: Model,
        env: Environment,
        tool_registry: ToolRegistry,
        *,
        config_class: type = ToolAgentConfig,
        **kwargs,
    ):
        self.config = config_class(**kwargs)
        self.messages: list[dict] = []
        self.model = model
        self.env = env
        self.tool_registry = tool_registry
        self.extra_template_vars = {}

    def render_template(self, template: str, **kwargs) -> str:
        template_vars = self.config.model_dump() | self.env.get_template_vars() | self.model.get_template_vars()
        return Template(template, undefined=StrictUndefined).render(
            **kwargs, **template_vars, **self.extra_template_vars
        )

    def add_message(self, role: str, content: str, **kwargs):
        self.messages.append({"role": role, "content": content, "timestamp": time.time(), **kwargs})

    def run(self, task: str, **kwargs) -> tuple[str, str]:
        """Run step() until agent is finished. Return exit status & message."""
        self.extra_template_vars |= {"task": task, **kwargs}
        self.messages = []
        self.add_message("system", self.render_template(self.config.system_template))
        self.add_message("user", self.render_template(self.config.instance_template))
        while True:
            try:
                self.step()
            except NonTerminatingException as exc:
                self.add_message("user", str(exc))
            except TerminatingException as exc:
                self.add_message("user", str(exc))
                return type(exc).__name__, str(exc)

    def step(self) -> dict:
        return self.get_observation(self.query())

    def query(self) -> dict:
        if 0 < self.config.step_limit <= self.model.n_calls or 0 < self.config.cost_limit <= self.model.cost:
            raise LimitsExceeded()
        response = self.model.query(self.messages)
        self.add_message("assistant", **response)
        return response

    def get_observation(self, response: dict) -> dict:
        output = self.execute_action(self.parse_action(response))
        observation = self.render_template(self.config.action_observation_template, output=output)
        self.add_message("user", observation)
        return output

    def parse_action(self, response: dict) -> dict:
        actions = re.findall(self.config.action_regex, response["content"], re.DOTALL)
        if len(actions) != 1:
            raise FormatError(self.render_template(self.config.format_error_template, actions=actions))
        cmd = actions[0].strip()
        if cmd.startswith("@tool "):
            return {"type": "tool", "raw": cmd, **response}
        return {"type": "bash", "command": cmd, **response}

    def execute_action(self, action: dict) -> dict:
        if action["type"] == "tool":
            return self.execute_tool(action)
        return self.execute_bash(action)

    def execute_tool(self, action: dict) -> dict:
        try:
            result = self.tool_registry.execute(action["raw"], context=self.extra_template_vars)
        except ToolRegistryError as exc:
            available = self.tool_registry.available_tools()
            raise ToolFormatError(
                self.render_template(
                    self.config.tool_format_error_template,
                    command=action["raw"],
                    available_tools=available,
                )
            ) from exc
        if not result.success:
            raise ToolExecutionError(
                self.render_template(
                    self.config.tool_error_template,
                    tool_name=action["raw"].split()[1] if action["raw"].split() else "unknown",
                    error=result.error or result.output,
                )
            )
        return {
            "type": "tool",
            "output": result.output,
            "returncode": result.returncode,
            "action": action["raw"],
        }

    def execute_bash(self, action: dict) -> dict:
        try:
            output = self.env.execute(action["command"])
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            output = exc.output.decode("utf-8", errors="replace") if getattr(exc, "output", None) else ""
            raise ExecutionTimeoutError(
                self.render_template(self.config.timeout_template, action=action, output=output)
            ) from exc
        self.has_finished(output)
        return output | {"type": "bash", "action": action["command"]}

    def has_finished(self, output: dict[str, str]):
        lines = output.get("output", "").lstrip().splitlines(keepends=True)
        if lines and lines[0].strip() in ["MINI_SWE_AGENT_FINAL_OUTPUT", "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]:
            raise Submitted("".join(lines[1:]))
