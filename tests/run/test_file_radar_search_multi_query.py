from pathlib import Path

import pytest

from minisweagent.tools.file_radar_search.tool import FileRadarSearchArgs, FileRadarSearchTool


def _build_tool(tmp_path: Path, **overrides) -> FileRadarSearchTool:
    config = {
        "embedding_provider": "local",
        "embedding_model": "dummy-embedder",
        "embedding_device": "cpu",
        "index_root": str(tmp_path / "indexes"),
        "chunker": "sliding",
        "chunk_size": 800,
        "overlap": 200,
        "aggregation": "hybrid",
        "index_validation_mode": "static",
        "index_build_policy": "read_only",
    }
    config.update(overrides)
    return FileRadarSearchTool(config)


def test_file_radar_args_accepts_queries_and_deduplicates():
    parsed = FileRadarSearchArgs.from_raw(
        {
            "query": "socket update",
            "queries": ["switch socket on off", "socket update", "async_turn_on fritzbox"],
            "topk-files": "12",
            "topk-blocks": "90",
        }
    )

    assert parsed.query == "switch socket on off"
    assert parsed.queries == ["switch socket on off", "socket update", "async_turn_on fritzbox"]
    assert parsed.query_display == "switch socket on off | socket update | async_turn_on fritzbox"
    assert parsed.queries_provided is True
    assert parsed.topk_files == 12
    assert parsed.topk_blocks == 90


def test_file_radar_args_requires_query_or_queries():
    with pytest.raises(ValueError, match="query or queries"):
        FileRadarSearchArgs.from_raw({})


def test_fuse_ranked_files_prefers_multi_query_support(tmp_path: Path):
    tool = _build_tool(tmp_path)
    ranked_by_query = [
        [
            {"path": "src/a.py", "score": 0.80, "evidence_count": 3, "language": "python"},
            {"path": "src/b.py", "score": 0.91, "evidence_count": 2, "language": "python"},
        ],
        [
            {"path": "src/a.py", "score": 0.76, "evidence_count": 2, "language": "python"},
            {"path": "src/c.py", "score": 0.97, "evidence_count": 4, "language": "python"},
        ],
        [
            {"path": "src/a.py", "score": 0.72, "evidence_count": 1, "language": "python"},
            {"path": "src/d.py", "score": 0.99, "evidence_count": 5, "language": "python"},
        ],
    ]

    fused = tool._fuse_ranked_files(ranked_by_query, query_count=3)

    assert fused[0]["path"] == "src/a.py"
    assert fused[0]["support_count"] == 3
    assert fused[0]["query_count"] == 3
    assert any(item["path"] == "src/d.py" and item["support_count"] == 1 for item in fused)


def test_format_results_shows_support_for_multi_query(tmp_path: Path):
    tool = _build_tool(tmp_path)
    output = tool._format_results(
        "q1 | q2 | q3",
        [
            {
                "path": "src/a.py",
                "score": 0.88,
                "evidence_count": 7,
                "support_count": 2,
                "query_count": 3,
            }
        ],
        auto_skeleton={"enabled": False, "files": []},
    )

    assert "support: 2/3" in output
    assert 'Found 1 candidate files for "q1 | q2 | q3":' in output


def test_format_results_blind_alpha_hides_score_and_sorts_paths(tmp_path: Path):
    tool = _build_tool(tmp_path, display_mode="blind_alpha")
    output = tool._format_results(
        "auth",
        [
            {"path": "src/zeta.py", "score": 0.95, "evidence_count": 3},
            {"path": "src/alpha.py", "score": 0.12, "evidence_count": 2},
        ],
        auto_skeleton={"enabled": False, "files": []},
    )

    assert "score:" not in output
    assert output.index("- src/alpha.py") < output.index("- src/zeta.py")


def test_format_results_clustered_groups_by_directory_without_scores(tmp_path: Path):
    tool = _build_tool(tmp_path, display_mode="clustered")
    output = tool._format_results(
        "auth",
        [
            {"path": "src/auth/session.py", "score": 0.40, "evidence_count": 2},
            {"path": "tests/test_auth.py", "score": 0.97, "evidence_count": 5},
            {"path": "src/auth/login.py", "score": 0.91, "evidence_count": 4},
            {"path": "src/core/router.py", "score": 0.82, "evidence_count": 3},
        ],
        auto_skeleton={"enabled": False, "files": []},
    )

    assert "score:" not in output
    assert "[DIR] tests/" in output
    assert "[DIR] src/auth/" in output
    assert output.index("[DIR] tests/") < output.index("[DIR] src/auth/") < output.index("[DIR] src/core/")
    assert output.index("src/auth/login.py") < output.index("src/auth/session.py")
    assert "📂" not in output


def test_effective_queries_auto_expands_single_query(tmp_path: Path):
    tool = _build_tool(tmp_path, auto_query_expansion_enabled=True, auto_query_expansion_max_queries=3)
    parsed = FileRadarSearchArgs.from_raw({"query": "switch socket on off async_turn_on fritzbox"})

    effective, expanded = tool._effective_queries(parsed)

    assert effective[0] == "switch socket on off async_turn_on fritzbox"
    assert 1 < len(effective) <= 3
    assert expanded is True


def test_effective_queries_respects_explicit_queries_without_auto_expansion(tmp_path: Path):
    tool = _build_tool(tmp_path, auto_query_expansion_enabled=True, auto_query_expansion_max_queries=3)
    parsed = FileRadarSearchArgs.from_raw({"queries": ["switch socket on off"]})

    effective, expanded = tool._effective_queries(parsed)

    assert effective == ["switch socket on off"]
    assert expanded is False
