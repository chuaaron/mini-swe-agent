from pathlib import Path

import pytest

from minisweagent.environments.repo_mounts import build_repo_mount_args


def _count_mount(run_args: list[str], mount_spec: str) -> int:
    return sum(1 for item in run_args if item == mount_spec)


def test_single_mount_replaces_repos_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    repo_path = repo_root / "demo_repo"
    repo_path.mkdir()

    run_args = ["--rm", "-v", f"{repo_root}:/repos:ro", "--ipc", "host"]
    result = build_repo_mount_args(
        run_args=run_args,
        repo_mount_mode="single",
        repo_root=repo_root,
        repo_source_path=repo_path,
        repo_mount_path="/repos/demo_repo",
    )

    assert f"{repo_root}:/repos:ro" not in result
    assert "-v" in result
    assert f"{repo_path}:/repos/demo_repo:ro" in result
    assert "--ipc" in result


def test_all_mount_adds_repos_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    repo_path = repo_root / "demo_repo"
    repo_path.mkdir()

    result = build_repo_mount_args(
        run_args=["--rm"],
        repo_mount_mode="all",
        repo_root=repo_root,
        repo_source_path=repo_path,
        repo_mount_path="/repos/demo_repo",
    )

    assert f"{repo_root}:/repos:ro" in result


def test_all_mount_does_not_duplicate(tmp_path: Path) -> None:
    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    repo_path = repo_root / "demo_repo"
    repo_path.mkdir()

    run_args = ["--rm", "-v", f"{repo_root}:/repos:ro"]
    result = build_repo_mount_args(
        run_args=run_args,
        repo_mount_mode="all",
        repo_root=repo_root,
        repo_source_path=repo_path,
        repo_mount_path="/repos/demo_repo",
    )

    assert _count_mount(result, f"{repo_root}:/repos:ro") == 1


def test_single_mount_missing_repo_fails(tmp_path: Path) -> None:
    repo_root = tmp_path / "repos"
    repo_root.mkdir()

    with pytest.raises(ValueError, match="Repo path not found"):
        build_repo_mount_args(
            run_args=["--rm"],
            repo_mount_mode="single",
            repo_root=repo_root,
            repo_source_path=repo_root / "missing_repo",
            repo_mount_path="/repos/missing_repo",
        )


def test_all_mount_missing_root_fails(tmp_path: Path) -> None:
    repo_root = tmp_path / "repos"

    with pytest.raises(ValueError, match="Repo root not found"):
        build_repo_mount_args(
            run_args=["--rm"],
            repo_mount_mode="all",
            repo_root=repo_root,
            repo_source_path=None,
            repo_mount_path="/repos/demo_repo",
        )


def test_invalid_mode_fails(tmp_path: Path) -> None:
    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    repo_path = repo_root / "demo_repo"
    repo_path.mkdir()

    with pytest.raises(ValueError, match="Invalid repo_mount_mode"):
        build_repo_mount_args(
            run_args=["--rm"],
            repo_mount_mode="maybe",
            repo_root=repo_root,
            repo_source_path=repo_path,
            repo_mount_path="/repos/demo_repo",
        )
