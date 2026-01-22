"""Configuration loader for SWE-QA-Bench (default/local/CLI)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from minisweagent.utils.log import logger


_PATH_KEYS = {
    "dataset_root",
    "repos_root",
    "indexes_root",
    "model_root",
    "output_root",
    "output_dir",
    "agent_config",
    "tool_config",
}


def _find_project_root(start: Path) -> Path:
    for parent in [start, *start.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return start.parent


def project_root() -> Path:
    return _find_project_root(Path(__file__).resolve())


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a YAML mapping: {path}")
    return data


def _deep_merge(base: Any, override: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _expand_path(value: str, root: Path) -> str:
    expanded = os.path.expanduser(value)
    path = Path(expanded)
    if not path.is_absolute():
        path = root / path
    return str(path)


def _expand_paths(config: dict[str, Any], root: Path) -> None:
    paths = config.get("paths")
    if isinstance(paths, dict):
        for key, value in list(paths.items()):
            if key not in _PATH_KEYS:
                continue
            if not isinstance(value, str) or not value.strip():
                continue
            paths[key] = _expand_path(value.strip(), root)
    run = config.get("run")
    if isinstance(run, dict):
        for key in ("output_dir", "agent_config", "tool_config"):
            value = run.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            run[key] = _expand_path(value.strip(), root)


def _apply_env(env: dict[str, Any] | None) -> None:
    if not env:
        return
    for key, value in env.items():
        if value is None:
            continue
        os.environ[str(key)] = str(value)


def load_config(
    *,
    config_dir: Path | None = None,
    default_name: str = "default.yaml",
    local_name: str = "local.yaml",
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = project_root()
    config_dir = config_dir or (root / "swe_qa_bench" / "config")
    default_path = config_dir / default_name
    local_path = config_dir / local_name

    config = _read_yaml(default_path)
    if local_path.exists():
        local_config = _read_yaml(local_path)
        config = _deep_merge(config, local_config)
    else:
        logger.info("No local config found at %s, using defaults.", local_path)

    if overrides:
        config = _deep_merge(config, overrides)

    _expand_paths(config, root)
    _apply_env(config.get("env"))
    return config
