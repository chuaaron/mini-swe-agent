from pathlib import Path

import pytest
import yaml

from minisweagent.agents.default import NonTerminatingException, Submitted
from minisweagent.environments.local import LocalEnvironment
from minisweagent.locbench.feedback_loop_agent import FeedbackLoopBashAgent
from minisweagent.models.test_models import DeterministicModel


def _load_agent_config() -> dict:
    config_path = Path("locbench/config/agent_bash.yaml")
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)["agent"]


def _build_agent(**kwargs) -> FeedbackLoopBashAgent:
    defaults = {
        "feedback_mode": "rule",
        "feedback_every_n_steps": 1,
        "feedback_max_rounds": 5,
        "feedback_submission_gate": True,
    }
    defaults.update(kwargs)
    return FeedbackLoopBashAgent(
        model=DeterministicModel(outputs=[]),
        env=LocalEnvironment(),
        **defaults,
        **_load_agent_config(),
    )


def test_submission_gate_blocks_unverified_hints_and_allows_verified_hints():
    agent = _build_agent()
    payload = 'MINI_SWE_AGENT_FINAL_OUTPUT\n{"functions":[{"function":"target","file_hint":"pkg/a.py"}]}\n'

    with pytest.raises(NonTerminatingException):
        agent.has_finished({"output": payload})
    assert agent.get_feedback_stats()["blocked_submissions"] == 1

    agent.add_observed_file("pkg/a.py")
    with pytest.raises(Submitted):
        agent.has_finished({"output": payload})


def test_repeated_commands_trigger_feedback_message():
    agent = _build_agent()
    response = {"content": "THOUGHT: inspect\n```bash\necho hello\n```"}

    agent.get_observation(response)
    agent.get_observation(response)
    agent.get_observation(response)

    user_messages = [msg.get("content", "") for msg in agent.messages if msg.get("role") == "user"]
    assert any("SYSTEM_INTERCEPTION: External feedback loop triggered." in content for content in user_messages)
    assert agent.get_feedback_stats()["reason_counts"].get("repeat_command", 0) >= 1
