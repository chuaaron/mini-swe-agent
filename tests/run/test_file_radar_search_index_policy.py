import json
from pathlib import Path

import pytest

from minisweagent.tools.file_radar_search.tool import _INDEX_VERSION, FileRadarSearchTool, sanitize_id


def _build_tool(
    tmp_path: Path,
    *,
    index_validation_mode: str = "strict",
    index_build_policy: str = "auto",
) -> FileRadarSearchTool:
    return FileRadarSearchTool(
        {
            "embedding_provider": "local",
            "embedding_model": "dummy-embedder",
            "embedding_device": "cpu",
            "index_root": str(tmp_path / "indexes"),
            "chunker": "sliding",
            "chunk_size": 800,
            "overlap": 200,
            "aggregation": "hybrid",
            "index_validation_mode": index_validation_mode,
            "index_build_policy": index_build_policy,
        }
    )


def _index_dir(tool: FileRadarSearchTool, repo_slug: str, repo_dir: str, commit: str) -> Path:
    embedder_id = sanitize_id(f"{tool.config.embedding_provider}_{tool.config.embedding_model}")
    safe_repo = sanitize_id(repo_slug or repo_dir)
    safe_commit = sanitize_id(commit)
    return Path(tool.config.index_root) / _INDEX_VERSION / safe_repo / safe_commit / embedder_id


def _write_prebuilt_index(
    tool: FileRadarSearchTool,
    *,
    repo_slug: str,
    repo_dir: str,
    commit: str,
    repo_fingerprint: str,
) -> Path:
    index_dir = _index_dir(tool, repo_slug, repo_dir, commit)
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "embeddings.pt").write_text("dummy", encoding="utf-8")
    (index_dir / "metadata.jsonl").write_text("{}\n", encoding="utf-8")
    meta = {
        "index_version": _INDEX_VERSION,
        "repo_dir": repo_dir,
        "repo_slug": repo_slug,
        "base_commit": commit,
        "repo_fingerprint": repo_fingerprint,
        "embedding_provider": tool.config.embedding_provider,
        "embedding_model": tool.config.embedding_model,
        "chunker": tool.config.chunker,
        "chunk_size": tool.config.chunk_size,
        "overlap": tool.config.overlap,
        "aggregation": tool.config.aggregation,
    }
    (index_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return index_dir


def test_static_validation_ignores_repo_fingerprint_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_slug = "demo/repo"
    repo_dir = "demo_repo"
    commit = "abcdef12"
    tool = _build_tool(tmp_path, index_validation_mode="static", index_build_policy="auto")
    _write_prebuilt_index(
        tool,
        repo_slug=repo_slug,
        repo_dir=repo_dir,
        commit=commit,
        repo_fingerprint="old-fingerprint",
    )

    expected_index = object()
    monkeypatch.setattr(tool, "_load_index", lambda *args, **kwargs: expected_index)
    monkeypatch.setattr(tool, "_build_index", lambda *args, **kwargs: pytest.fail("unexpected rebuild"))

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    index, diag = tool._get_or_build_index(
        repo_path=repo_path,
        repo_dir=repo_dir,
        repo_slug=repo_slug,
        commit=commit,
        repo_fingerprint="new-fingerprint",
    )

    assert index is expected_index
    assert diag["index_status"] == "disk_hit"
    assert diag["compat_reason"] == "ok"


def test_strict_validation_rebuilds_when_repo_fingerprint_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_slug = "demo/repo"
    repo_dir = "demo_repo"
    commit = "abcdef12"
    tool = _build_tool(tmp_path, index_validation_mode="strict", index_build_policy="auto")
    _write_prebuilt_index(
        tool,
        repo_slug=repo_slug,
        repo_dir=repo_dir,
        commit=commit,
        repo_fingerprint="old-fingerprint",
    )

    rebuilt_index = object()
    monkeypatch.setattr(tool, "_load_index", lambda *args, **kwargs: pytest.fail("unexpected disk hit"))
    monkeypatch.setattr(tool, "_build_index", lambda *args, **kwargs: rebuilt_index)

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    index, diag = tool._get_or_build_index(
        repo_path=repo_path,
        repo_dir=repo_dir,
        repo_slug=repo_slug,
        commit=commit,
        repo_fingerprint="new-fingerprint",
    )

    assert index is rebuilt_index
    assert diag["index_status"] == "rebuilt"
    assert diag["compat_reason"] == "repo_fingerprint_mismatch"


def test_read_only_policy_blocks_incompatible_prebuilt_index(tmp_path: Path):
    repo_slug = "demo/repo"
    repo_dir = "demo_repo"
    commit = "abcdef12"
    tool = _build_tool(tmp_path, index_validation_mode="strict", index_build_policy="read_only")
    _write_prebuilt_index(
        tool,
        repo_slug=repo_slug,
        repo_dir=repo_dir,
        commit=commit,
        repo_fingerprint="old-fingerprint",
    )

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    with pytest.raises(RuntimeError) as exc_info:
        tool._get_or_build_index(
            repo_path=repo_path,
            repo_dir=repo_dir,
            repo_slug=repo_slug,
            commit=commit,
            repo_fingerprint="new-fingerprint",
        )

    message = str(exc_info.value)
    assert "index_build_policy=read_only" in message
    assert "repo_fingerprint_mismatch" in message


def test_read_only_policy_blocks_missing_index(tmp_path: Path):
    repo_slug = "demo/repo"
    repo_dir = "demo_repo"
    commit = "abcdef12"
    tool = _build_tool(tmp_path, index_validation_mode="static", index_build_policy="read_only")

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    with pytest.raises(RuntimeError) as exc_info:
        tool._get_or_build_index(
            repo_path=repo_path,
            repo_dir=repo_dir,
            repo_slug=repo_slug,
            commit=commit,
            repo_fingerprint="new-fingerprint",
        )

    message = str(exc_info.value)
    assert "index_build_policy=read_only" in message
    assert "index_missing" in message


def test_static_validation_accepts_legacy_repo_level_index_without_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_slug = "demo/repo"
    repo_dir = "demo_repo"
    commit = "abcdef12"
    tool = _build_tool(tmp_path, index_validation_mode="static", index_build_policy="read_only")
    legacy_dir = Path(tool.config.index_root) / sanitize_id(repo_slug)
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "embeddings.pt").write_text("dummy", encoding="utf-8")
    (legacy_dir / "metadata.jsonl").write_text("{}\n", encoding="utf-8")

    expected_index = object()
    monkeypatch.setattr(tool, "_load_index", lambda *args, **kwargs: expected_index)
    monkeypatch.setattr(tool, "_build_index", lambda *args, **kwargs: pytest.fail("unexpected rebuild"))

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    index, diag = tool._get_or_build_index(
        repo_path=repo_path,
        repo_dir=repo_dir,
        repo_slug=repo_slug,
        commit=commit,
        repo_fingerprint="new-fingerprint",
    )

    assert index is expected_index
    assert diag["index_status"] == "disk_hit"
    assert diag["compat_reason"] == "legacy_no_meta"
