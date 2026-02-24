from pathlib import Path

import pytest
import yaml

from minisweagent.agents.tool_agent import FormatError
from minisweagent.environments.local import LocalEnvironment
from minisweagent.locbench.runners.tools_runner import ProgressTrackingAgent, _extract_oracle_files
from minisweagent.models.test_models import DeterministicModel
from minisweagent.run.extra.utils.run_summary import _build_overall_stats
from minisweagent.tools.registry import ToolRegistry


class _NoopProgress:
    def update_instance_status(self, *_args, **_kwargs):
        return None


def _load_oracle_agent_config() -> dict:
    config_path = Path("locbench/config/agent_tools_radar_oracle_sniper.yaml")
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)["agent"]


def _build_oracle_agent(oracle_files: list[str] | None = None) -> ProgressTrackingAgent:
    return ProgressTrackingAgent(
        model=DeterministicModel(outputs=[]),
        env=LocalEnvironment(),
        tool_registry=ToolRegistry(),
        progress_manager=_NoopProgress(),
        instance_id="oracle-instance",
        enforce_tool_verification=True,
        disallow_tools=True,
        oracle_files=oracle_files or ["src/core/api.py"],
        **_load_oracle_agent_config(),
    )


def test_extract_oracle_files_filters_tests():
    record = {
        "patch": (
            "diff --git a/src/core/api.py b/src/core/api.py\n"
            "index 0000000..1111111 100644\n"
            "--- a/src/core/api.py\n"
            "+++ b/src/core/api.py\n"
            "diff --git a/tests/test_api.py b/tests/test_api.py\n"
        ),
        "edit_functions": ["tests/test_api.py:test_login_flow"],
    }
    files, fallback_to_tests = _extract_oracle_files(record)
    assert files == ["src/core/api.py"]
    assert fallback_to_tests is False


def test_extract_oracle_files_fallback_to_tests_when_needed():
    record = {
        "patch": "diff --git a/tests/test_api.py b/tests/test_api.py\n",
        "edit_functions": ["tests/test_other.py:test_login_flow"],
    }
    files, fallback_to_tests = _extract_oracle_files(record)
    assert files == ["tests/test_api.py", "tests/test_other.py"]
    assert fallback_to_tests is True


def test_oracle_agent_initializes_with_verification_gate():
    agent = _build_oracle_agent(["src/core/api.py"])
    assert agent.candidate_files == {"src/core/api.py"}
    assert agent.needs_verification is True


def test_oracle_agent_rejects_tool_calls():
    agent = _build_oracle_agent(["src/core/api.py"])

    with pytest.raises(FormatError) as exc_info:
        agent.parse_action({"content": 'THOUGHT: x\n```bash\n@tool file_radar_search --query "auth"\n```'})

    message = str(exc_info.value)
    assert "SYSTEM_INTERCEPTION: Tools Disabled in Oracle-Sniper Mode." in message
    assert "This is not a JSON formatting error." in message


def test_run_summary_computes_oracle_metrics():
    summaries = [
        {
            "instance_id": "a",
            "exit_status": "Submitted",
            "steps": 6,
            "trace_tokens": 100,
            "billed_tokens": 120,
            "cost_usd": 0.1,
            "correct": True,
            "entity_hit_any": True,
            "radar_called": False,
            "radar_tool_calls": 0,
            "radar_tool_output_chars": 0,
            "blocked_submission_count": 1,
            "radar_verification_satisfied": True,
            "oracle_sniper_mode": True,
            "oracle_file_provided": True,
            "oracle_verification_satisfied": True,
        },
        {
            "instance_id": "b",
            "exit_status": "Submitted",
            "steps": 10,
            "trace_tokens": 90,
            "billed_tokens": 110,
            "cost_usd": 0.2,
            "correct": False,
            "entity_hit_any": False,
            "radar_called": False,
            "radar_tool_calls": 0,
            "radar_tool_output_chars": 0,
            "blocked_submission_count": 2,
            "radar_verification_satisfied": False,
            "oracle_sniper_mode": True,
            "oracle_file_provided": True,
            "oracle_verification_satisfied": False,
        },
        {
            "instance_id": "c",
            "exit_status": "Submitted",
            "steps": 4,
            "trace_tokens": 80,
            "billed_tokens": 100,
            "cost_usd": 0.05,
            "correct": False,
            "entity_hit_any": False,
            "radar_called": False,
            "radar_tool_calls": 0,
            "radar_tool_output_chars": 0,
            "blocked_submission_count": 0,
            "radar_verification_satisfied": None,
            "oracle_sniper_mode": True,
            "oracle_file_provided": False,
            "oracle_verification_satisfied": None,
        },
    ]

    stats = _build_overall_stats(summaries)

    assert stats["oracle_instance_count"] == 3
    assert stats["oracle_file_provided_count"] == 2
    assert stats["oracle_file_provided_rate"] == pytest.approx(2 / 3)
    assert stats["oracle_verification_compliance_rate"] == pytest.approx(0.5)
    assert stats["entity_hit_rate_given_oracle_file"] == pytest.approx(0.5)
    assert stats["oracle_blocked_submission_count"] == 3
    assert stats["steps_to_success_in_oracle_count"] == 1
    assert stats["steps_to_success_in_oracle_mean"] == pytest.approx(6.0)
    assert stats["steps_to_success_in_oracle_p50"] == 6
    assert stats["steps_to_success_in_oracle_p90"] == 6
