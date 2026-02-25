import json
from pathlib import Path

import pytest

from minisweagent.tools.code_search.tool import _INDEX_VERSION, CodeSearchTool, sanitize_id


def _build_tool(tmp_path: Path, *, index_build_policy: str = "auto") -> CodeSearchTool:
    return CodeSearchTool(
        {
            "embedding_provider": "local",
            "embedding_model": "dummy-embedder",
            "embedding_device": "cpu",
            "index_root": str(tmp_path / "indexes"),
            "chunker": "sliding",
            "chunk_size": 800,
            "overlap": 200,
            "index_build_policy": index_build_policy,
        }
    )


def _index_dir(tool: CodeSearchTool, repo_dir: str, commit: str) -> Path:
    embedder_id = sanitize_id(f"{tool.config.embedding_provider}_{tool.config.embedding_model}")
    return Path(tool.config.index_root) / repo_dir / commit[:8] / embedder_id


def _write_prebuilt_index(
    tool: CodeSearchTool,
    *,
    repo_dir: str,
    commit: str,
    override_meta: dict[str, object] | None = None,
) -> Path:
    index_dir = _index_dir(tool, repo_dir, commit)
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "embeddings.pt").write_text("dummy", encoding="utf-8")
    (index_dir / "metadata.jsonl").write_text("{}\n", encoding="utf-8")
    meta = {
        "index_version": _INDEX_VERSION,
        "repo_dir": repo_dir,
        "base_commit": commit,
        "embedding_provider": tool.config.embedding_provider,
        "embedding_model": tool.config.embedding_model,
        "chunker": tool.config.chunker,
        "chunk_size": tool.config.chunk_size,
        "overlap": tool.config.overlap,
    }
    if override_meta:
        meta.update(override_meta)
    (index_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return index_dir


def test_read_only_policy_blocks_incompatible_prebuilt_index(tmp_path: Path):
    repo_dir = "demo_repo"
    commit = "abcdef12"
    tool = _build_tool(tmp_path, index_build_policy="read_only")
    _write_prebuilt_index(tool, repo_dir=repo_dir, commit=commit, override_meta={"base_commit": "deadbeef"})

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    with pytest.raises(RuntimeError) as exc_info:
        tool._get_or_build_index(repo_path=repo_path, repo_dir=repo_dir, commit=commit)

    message = str(exc_info.value)
    assert "index_build_policy=read_only" in message
    assert "base_commit_mismatch" in message


def test_read_only_policy_blocks_missing_index(tmp_path: Path):
    repo_dir = "demo_repo"
    commit = "abcdef12"
    tool = _build_tool(tmp_path, index_build_policy="read_only")

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    with pytest.raises(RuntimeError) as exc_info:
        tool._get_or_build_index(repo_path=repo_path, repo_dir=repo_dir, commit=commit)

    message = str(exc_info.value)
    assert "index_build_policy=read_only" in message
    assert "index_missing" in message


def test_auto_policy_rebuilds_when_prebuilt_index_incompatible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_dir = "demo_repo"
    commit = "abcdef12"
    tool = _build_tool(tmp_path, index_build_policy="auto")
    _write_prebuilt_index(tool, repo_dir=repo_dir, commit=commit, override_meta={"base_commit": "deadbeef"})

    rebuilt_index = object()
    monkeypatch.setattr(tool, "_load_index", lambda *args, **kwargs: pytest.fail("unexpected disk hit"))
    monkeypatch.setattr(tool, "_build_index", lambda *args, **kwargs: rebuilt_index)

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    index, diag = tool._get_or_build_index(repo_path=repo_path, repo_dir=repo_dir, commit=commit)

    assert index is rebuilt_index
    assert diag["index_status"] == "rebuilt"
    assert diag["compat_reason"] == "base_commit_mismatch"


def test_disk_hit_reports_status_and_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_dir = "demo_repo"
    commit = "abcdef12"
    tool = _build_tool(tmp_path, index_build_policy="auto")
    _write_prebuilt_index(tool, repo_dir=repo_dir, commit=commit)

    expected_index = object()
    monkeypatch.setattr(tool, "_load_index", lambda *args, **kwargs: expected_index)
    monkeypatch.setattr(tool, "_build_index", lambda *args, **kwargs: pytest.fail("unexpected rebuild"))

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    index, diag = tool._get_or_build_index(repo_path=repo_path, repo_dir=repo_dir, commit=commit)

    assert index is expected_index
    assert diag["index_status"] == "disk_hit"
    assert diag["compat_reason"] == "ok"


def test_cache_hit_reports_status_and_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_dir = "demo_repo"
    commit = "abcdef12"
    tool = _build_tool(tmp_path, index_build_policy="auto")
    _write_prebuilt_index(tool, repo_dir=repo_dir, commit=commit)

    expected_index = object()
    monkeypatch.setattr(tool, "_load_index", lambda *args, **kwargs: expected_index)
    monkeypatch.setattr(tool, "_build_index", lambda *args, **kwargs: pytest.fail("unexpected rebuild"))

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    first_index, first_diag = tool._get_or_build_index(repo_path=repo_path, repo_dir=repo_dir, commit=commit)
    second_index, second_diag = tool._get_or_build_index(repo_path=repo_path, repo_dir=repo_dir, commit=commit)

    assert first_index is expected_index
    assert first_diag["index_status"] == "disk_hit"
    assert second_index is expected_index
    assert second_diag["index_status"] == "cache_hit"
    assert second_diag["compat_reason"] == "cached"
