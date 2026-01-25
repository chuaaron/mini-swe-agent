"""Helpers for building repo mount args for container environments."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

_VALID_MODES = {"single", "all"}


def _normalize_mode(value: str | None) -> str:
    mode = (value or "single").strip().lower()
    if mode not in _VALID_MODES:
        raise ValueError(f"Invalid repo_mount_mode: {mode}")
    return mode


def _split_mount_spec(spec: str) -> tuple[str, str] | None:
    parts = spec.split(":")
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def _is_repos_target(spec: str) -> bool:
    parsed = _split_mount_spec(spec)
    if parsed is None:
        return False
    _, dest = parsed
    return dest == "/repos" or dest.startswith("/repos/")


def _filter_repos_mounts(run_args: list[str]) -> list[str]:
    filtered: list[str] = []
    idx = 0
    while idx < len(run_args):
        token = run_args[idx]
        if token in {"-v", "--volume"} and idx + 1 < len(run_args):
            spec = run_args[idx + 1]
            if _is_repos_target(spec):
                idx += 2
                continue
            filtered.extend([token, spec])
            idx += 2
            continue
        if token.startswith("-v=") or token.startswith("--volume="):
            spec = token.split("=", 1)[1]
            if _is_repos_target(spec):
                idx += 1
                continue
        filtered.append(token)
        idx += 1
    return filtered


def _iter_mount_specs(run_args: Iterable[str]) -> Iterable[str]:
    idx = 0
    args = list(run_args)
    while idx < len(args):
        token = args[idx]
        if token in {"-v", "--volume"} and idx + 1 < len(args):
            yield args[idx + 1]
            idx += 2
            continue
        if token.startswith("-v=") or token.startswith("--volume="):
            yield token.split("=", 1)[1]
        idx += 1


def _has_repos_root_mount(run_args: Iterable[str]) -> bool:
    for spec in _iter_mount_specs(run_args):
        parsed = _split_mount_spec(spec)
        if parsed is None:
            continue
        _, dest = parsed
        if dest == "/repos":
            return True
    return False


def build_repo_mount_args(
    *,
    run_args: list[str] | None,
    repo_mount_mode: str | None,
    repo_root: Path | None,
    repo_source_path: Path | None,
    repo_mount_path: str,
) -> list[str]:
    args = list(run_args or [])
    if "--rm" not in args:
        args.insert(0, "--rm")

    mode = _normalize_mode(repo_mount_mode)
    if mode == "single":
        if repo_source_path is None:
            raise ValueError("repo_source_path must be set for single repo mount")
        repo_source_path = Path(repo_source_path)
        if not repo_source_path.exists():
            raise ValueError(f"Repo path not found for single mount: {repo_source_path}")
        args = _filter_repos_mounts(args)
        args.extend(["-v", f"{repo_source_path}:{repo_mount_path}:ro"])
        return args

    if repo_root is None:
        raise ValueError("repo_root must be set for all repo mount")
    repo_root = Path(repo_root)
    if not repo_root.exists():
        raise ValueError(f"Repo root not found for all mount: {repo_root}")
    if not _has_repos_root_mount(args):
        args.extend(["-v", f"{repo_root}:/repos:ro"])
    return args
