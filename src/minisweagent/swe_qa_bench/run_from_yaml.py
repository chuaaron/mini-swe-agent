#!/usr/bin/env python3

"""Run SWE-QA-Bench from a single YAML config."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import yaml

from minisweagent import package_dir
from minisweagent.swe_qa_bench.runners import bash_runner, tools_runner

_ALLOWED_TOOLS_PROMPTS = {"neutral", "search_first", "search_fallback"}


def _load_config(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("run config must be a YAML mapping")
    return data


def _apply_env(env: dict[str, Any] | None) -> None:
    if not env:
        return
    for key, value in env.items():
        if value is None:
            continue
        os.environ[str(key)] = str(value)


def _as_repos(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ",".join(str(item) for item in value if str(item).strip())
    return str(value)


def _default_agent_config(mode: str) -> Path:
    base = package_dir.parents[1] / "swe_qa_bench" / "config"
    if mode == "tools":
        return base / "agent_tools.yaml"
    return base / "agent_bash.yaml"


def _default_tool_config() -> Path:
    return package_dir.parents[1] / "swe_qa_bench" / "config" / "code_search.yaml"


def _resolve_path(value: Any) -> Path:
    if not value:
        raise ValueError("missing required path in run config")
    return Path(os.path.expandvars(os.path.expanduser(str(value))))


def _get_method(mode: str, value: Any) -> str:
    if value:
        return str(value)
    return "miniswe_tools" if mode == "tools" else "miniswe_bash"


def _normalize_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_tools_prompt(value: Any) -> str:
    text = _normalize_optional(value)
    if not text:
        return "neutral"
    return text.lower()


def _apply_tools_prompt_suffix(method: str, tools_prompt: str) -> str:
    if tools_prompt in {"search_first", "search_fallback"}:
        suffix = f"__{tools_prompt}"
        return method if method.endswith(suffix) else f"{method}{suffix}"
    return method


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SWE-QA-Bench from a YAML config")
    parser.add_argument("--config", required=True, help="Path to run config YAML")
    args = parser.parse_args()

    print("Warning: run_from_yaml is deprecated. Use `python -m minisweagent.run_swe_qa` instead.")

    config_path = Path(args.config).expanduser().resolve()
    config = _load_config(config_path)

    mode = str(config.get("mode", "")).strip().lower()
    if mode not in {"bash", "tools"}:
        raise ValueError("mode must be 'bash' or 'tools'")

    _apply_env(config.get("env"))

    dataset_root = _resolve_path(config.get("dataset_root"))
    repos_root_value = config.get("repos_root")
    if not repos_root_value or not str(repos_root_value).strip():
        repos_root = dataset_root / "repos"
    else:
        repos_root = _resolve_path(repos_root_value)
    output_root = _resolve_path(config.get("output_root") or config.get("dataset_root"))
    repos = _as_repos(config.get("repos"))
    slice_spec = str(config.get("slice", ""))
    shuffle = bool(config.get("shuffle", False))
    shuffle_seed = int(config.get("shuffle_seed", 42))
    workers = int(config.get("workers", 1))

    output_model_name = str(config.get("output_model_name", "")).strip()
    if not output_model_name:
        raise ValueError("output_model_name must be set")

    tools_prompt = _normalize_tools_prompt(config.get("tools_prompt"))
    if mode == "tools" and tools_prompt not in _ALLOWED_TOOLS_PROMPTS:
        raise ValueError(f"Invalid tools_prompt: {tools_prompt}")

    method = _get_method(mode, config.get("method"))
    effective_method = _apply_tools_prompt_suffix(method, tools_prompt) if mode == "tools" else method
    output_dir = _normalize_optional(config.get("output_dir")) or ""
    image = _normalize_optional(config.get("image"))
    environment_class = _normalize_optional(config.get("environment_class"))
    model = _normalize_optional(config.get("model"))
    model_class = _normalize_optional(config.get("model_class"))
    redo_existing = bool(config.get("redo_existing", False))
    billing = config.get("billing")
    pricing = None

    agent_config_value = config.get("agent_config")
    if agent_config_value:
        agent_config_path = Path(agent_config_value).expanduser().resolve()
    elif mode == "tools":
        agent_config_path = _default_agent_config(mode).with_name(f"agent_tools_{tools_prompt}.yaml")
    else:
        agent_config_path = _default_agent_config(mode)

    if mode == "bash":
        runner = bash_runner.BashRunner(
            dataset_root=dataset_root,
            repos_root=repos_root,
            output_root=output_root,
            repos=[item for item in repos.split(",") if item] if repos else [],
            slice_spec=slice_spec,
            shuffle=shuffle,
            shuffle_seed=shuffle_seed,
            workers=workers,
            config_path=agent_config_path,
            model=model,
            model_class=model_class,
            environment_class=environment_class,
            image=image,
            output_model_name=output_model_name,
            method=method,
            output_dir=output_dir,
            redo_existing=redo_existing,
            pricing=pricing,
            billing=billing,
        )
        runner.run()
        return

    tool_config = config.get("tool_config") or _default_tool_config()
    tool_config_path = Path(tool_config).expanduser().resolve()
    runner = tools_runner.ToolsRunner(
        dataset_root=dataset_root,
        repos_root=repos_root,
        output_root=output_root,
        repos=[item for item in repos.split(",") if item] if repos else [],
        slice_spec=slice_spec,
        shuffle=shuffle,
        shuffle_seed=shuffle_seed,
        workers=workers,
        config_path=agent_config_path,
        tool_config_path=tool_config_path,
        model=model,
        model_class=model_class,
        environment_class=environment_class,
        image=image,
        output_model_name=output_model_name,
        method=effective_method,
        output_dir=output_dir,
        redo_existing=redo_existing,
        indexes_root=None,
        model_root=None,
        tools_prompt=tools_prompt,
        pricing=pricing,
        billing=billing,
    )
    runner.run()


if __name__ == "__main__":
    main()
