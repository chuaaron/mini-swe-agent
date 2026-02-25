from pathlib import Path

import pytest
import yaml

from minisweagent.agents.tool_agent import FormatError, Submitted
from minisweagent.locbench.runners.tools_runner import ProgressTrackingAgent
from minisweagent.models.test_models import DeterministicModel
from minisweagent.environments.local import LocalEnvironment
from minisweagent.tools.base import ToolResult
from minisweagent.tools.registry import ToolRegistry


class _NoopProgress:
    def update_instance_status(self, *_args, **_kwargs):
        return None


def _load_agent_config() -> dict:
    config_path = Path("locbench/config/agent_tools_radar_neutral.yaml")
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)["agent"]


def _build_agent() -> ProgressTrackingAgent:
    return ProgressTrackingAgent(
        model=DeterministicModel(outputs=[]),
        env=LocalEnvironment(),
        tool_registry=ToolRegistry(),
        progress_manager=_NoopProgress(),
        instance_id="test-instance",
        enforce_tool_verification=True,
        **_load_agent_config(),
    )


def test_submission_requires_hint_file_to_be_read():
    agent = _build_agent()
    agent.radar_called_count = 1
    agent.candidate_files = {"pkg/a.py"}
    agent.verified_files = {"pkg/a.py"}
    agent.inspected_files = {"pkg/a.py"}
    agent.needs_verification = False

    with pytest.raises(Submitted):
        agent.has_finished(
            {
                "output": (
                    "MINI_SWE_AGENT_FINAL_OUTPUT\n"
                    '{"functions":[{"function":"target","file_hint":"pkg/a.py"}]}\n'
                )
            }
        )

    with pytest.raises(FormatError):
        agent.has_finished(
            {
                "output": (
                    "MINI_SWE_AGENT_FINAL_OUTPUT\n"
                    '{"functions":[{"function":"target","file_hint":"pkg/not_read.py"}]}\n'
                )
            }
        )
    assert agent.blocked_submission_count == 1


def test_strict_recovery_mode_after_repeated_interceptions():
    agent = _build_agent()
    agent.radar_called_count = 1
    agent.candidate_files = {"pkg/a.py"}
    agent.needs_verification = True

    payload = "MINI_SWE_AGENT_FINAL_OUTPUT\n{\"functions\":[{\"function\":\"target\",\"file_hint\":\"pkg/a.py\"}]}\n"
    for _ in range(3):
        with pytest.raises(FormatError):
            agent.has_finished({"output": payload})

    assert agent.blocked_submission_count == 3
    assert agent._strict_recovery_mode is True  # noqa: SLF001

    with pytest.raises(FormatError):
        agent.parse_action({"content": "THOUGHT: x\n```bash\necho nope\n```"})

    action = agent.parse_action(
        {
            "content": (
                "THOUGHT: x\n```bash\n"
                "sed -n '1,40p' pkg/a.py >/dev/null && "
                "printf 'MINI_SWE_AGENT_FINAL_OUTPUT\\n{\"functions\":[{\"function\":\"target\",\"file_hint\":\"pkg/a.py\"}]}\\n'\n"
                "```"
            )
        }
    )
    assert action["type"] == "bash"


def test_code_search_index_diagnostics_are_tracked():
    class _StubCodeSearchTool:
        name = "code_search"
        description = "stub"

        def run(self, _args: dict, _context: dict) -> ToolResult:
            return ToolResult(
                success=True,
                output="ok",
                data={
                    "index_status": "disk_hit",
                    "index_compat_reason": "ok",
                    "index_dir": "/tmp/index-dir",
                },
                returncode=0,
            )

    registry = ToolRegistry()
    registry.register(_StubCodeSearchTool())
    agent = ProgressTrackingAgent(
        model=DeterministicModel(outputs=[]),
        env=LocalEnvironment(),
        tool_registry=registry,
        progress_manager=_NoopProgress(),
        instance_id="test-instance",
        enforce_tool_verification=False,
        **_load_agent_config(),
    )

    result = agent.execute_tool({"raw": "@tool code_search --query token"})

    assert result["returncode"] == 0
    assert agent.code_search_called_count == 1
    assert agent.code_search_tool_output_chars == 2
    assert agent.code_search_index_status_counts == {"disk_hit": 1}
    assert agent.code_search_last_index_status == "disk_hit"
    assert agent.code_search_last_index_reason == "ok"
    assert agent.code_search_last_index_dir == "/tmp/index-dir"
