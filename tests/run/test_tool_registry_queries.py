import pytest

from minisweagent.tools.registry import ToolCommandError, parse_tool_command


def test_parse_tool_command_accepts_multi_queries_values():
    tool_name, args = parse_tool_command(
        '@tool file_radar_search --queries "switch socket on off" "async_turn_on fritzbox" --topk-files 20'
    )

    assert tool_name == "file_radar_search"
    assert args["queries"] == ["switch socket on off", "async_turn_on fritzbox"]
    assert args["topk-files"] == "20"


def test_parse_tool_command_queries_requires_at_least_one_value():
    with pytest.raises(ToolCommandError, match="queries"):
        parse_tool_command("@tool file_radar_search --queries --topk-files 10")
