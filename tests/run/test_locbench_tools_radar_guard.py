from pathlib import Path

import pytest
import yaml

from minisweagent.agents.tool_agent import FormatError, Submitted
from minisweagent.locbench.runners.tools_runner import ProgressTrackingAgent
from minisweagent.models.test_models import DeterministicModel
from minisweagent.environments.local import LocalEnvironment
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
