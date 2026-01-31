#!/usr/bin/env python3

"""Unified SWE-QA-Bench runner entrypoint."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from minisweagent.swe_qa_bench import runners
from minisweagent.swe_qa_bench.config_loader import load_config, project_root
from minisweagent.swe_qa_bench.utils import validate_output_model_name
from minisweagent.utils.log import logger


_ALLOWED_MODES = {"bash", "tools"}
_ALLOWED_TOOLS_PROMPTS = {"neutral", "search_first", "search_fallback"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SWE-QA-Bench with layered config")
    parser.add_argument("--config-dir", default="", help="Config directory (default: swe_qa_bench/config)")

    parser.add_argument("--mode", choices=sorted(_ALLOWED_MODES), help="Run mode: bash or tools")
    parser.add_argument("--dataset-root", help="Override paths.dataset_root")
    parser.add_argument("--repos-root", help="Override paths.repos_root")
    parser.add_argument("--indexes-root", help="Override paths.indexes_root (tools only)")
    parser.add_argument("--model-root", help="Override paths.model_root (tools only)")
    parser.add_argument("--output-model-name", help="Override paths.output_model_name")
    parser.add_argument("--output-dir", help="Override run.output_dir")

    parser.add_argument("--repos", help="Comma-separated repo list override")
    parser.add_argument("--slice", dest="slice_spec", help="Slice spec, e.g. 0:20")
    parser.add_argument("--shuffle", action=argparse.BooleanOptionalAction, help="Shuffle instances")
    parser.add_argument("--shuffle-seed", type=int, help="Random seed for shuffling")
    parser.add_argument("--workers", type=int, help="Number of worker threads")
    parser.add_argument("--redo-existing", action=argparse.BooleanOptionalAction, help="Redo existing answers")

    parser.add_argument("--model", dest="model_name", help="Override model.model_name")
    parser.add_argument("--model-class", help="Override model.model_class")
    parser.add_argument("--image", help="Override run.image")
    parser.add_argument("--method", help="Override run.method")
    parser.add_argument("--agent-config", help="Override run.agent_config")
    parser.add_argument("--tool-config", help="Override run.tool_config")
    parser.add_argument(
        "--tools-prompt",
        choices=sorted(_ALLOWED_TOOLS_PROMPTS),
        help="Tools prompt variant (tools mode only)",
    )

    return parser.parse_args()


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


def _build_overrides(args: argparse.Namespace) -> dict[str, Any]:
    overrides: dict[str, Any] = {"run": {}, "model": {}, "paths": {}}

    if args.mode:
        overrides["run"]["mode"] = args.mode
    if args.repos:
        overrides["run"]["repos"] = args.repos
    if args.slice_spec is not None:
        overrides["run"]["slice"] = args.slice_spec
    if args.shuffle is not None:
        overrides["run"]["shuffle"] = args.shuffle
    if args.shuffle_seed is not None:
        overrides["run"]["shuffle_seed"] = args.shuffle_seed
    if args.workers is not None:
        overrides["run"]["workers"] = args.workers
    if args.redo_existing is not None:
        overrides["run"]["redo_existing"] = args.redo_existing
    if args.output_dir:
        overrides["run"]["output_dir"] = args.output_dir
    if args.image:
        overrides["run"]["image"] = args.image
    if args.method:
        overrides["run"]["method"] = args.method
    if args.agent_config:
        overrides["run"]["agent_config"] = args.agent_config
    if args.tool_config:
        overrides["run"]["tool_config"] = args.tool_config
    if args.tools_prompt is not None:
        overrides["run"]["tools_prompt"] = args.tools_prompt

    if args.model_name:
        overrides["model"]["model_name"] = args.model_name
    if args.model_class:
        overrides["model"]["model_class"] = args.model_class

    if args.dataset_root:
        overrides["paths"]["dataset_root"] = args.dataset_root
    if args.repos_root:
        overrides["paths"]["repos_root"] = args.repos_root
    if args.indexes_root:
        overrides["paths"]["indexes_root"] = args.indexes_root
    if args.model_root:
        overrides["paths"]["model_root"] = args.model_root
    if args.output_model_name:
        overrides["paths"]["output_model_name"] = args.output_model_name

    overrides = {k: v for k, v in overrides.items() if v}
    return overrides


def _normalize_repos(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def _default_method(mode: str, value: str | None) -> str:
    if value:
        return value
    return "miniswe_tools" if mode == "tools" else "miniswe_bash"


def _resolve_path(value: Any, label: str) -> Path:
    if not value or not str(value).strip():
        raise ValueError(f"Missing required path: {label}")
    path = Path(str(value)).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"Path not found for {label}: {path}")
    return path


def _resolve_output_root(value: Any, *, root: Path) -> Path:
    if not value or not str(value).strip():
        path = root / "swe_qa_bench" / "results"
    else:
        path = Path(str(value)).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _log_summary(config: dict[str, Any], *, mode: str, agent_config: Path, tool_config: Path | None) -> None:
    run_cfg = config.get("run", {})
    model_cfg = config.get("model", {})
    paths = config.get("paths", {})

    summary = {
        "mode": mode,
        "dataset_root": paths.get("dataset_root"),
        "repos_root": paths.get("repos_root"),
        "indexes_root": paths.get("indexes_root"),
        "model_root": paths.get("model_root"),
        "output_root": paths.get("output_root"),
        "output_model_name": paths.get("output_model_name"),
        "model_name": model_cfg.get("model_name"),
        "model_class": model_cfg.get("model_class"),
        "image": run_cfg.get("image"),
        "workers": run_cfg.get("workers"),
        "slice": run_cfg.get("slice"),
        "shuffle": run_cfg.get("shuffle"),
        "repos": run_cfg.get("repos"),
        "output_dir": run_cfg.get("output_dir"),
        "agent_config": str(agent_config),
        "tool_config": str(tool_config) if tool_config else "",
        "tools_prompt": run_cfg.get("tools_prompt"),
    }

    logger.info("Effective SWE-QA-Bench config summary:")
    for key, value in summary.items():
        if value is None:
            value = ""
        logger.info("  %s: %s", key, value)


def main() -> None:
    args = _parse_args()
    config_dir = Path(args.config_dir).expanduser().resolve() if args.config_dir else None

    config = load_config(config_dir=config_dir, overrides=_build_overrides(args))

    run_cfg = config.get("run", {})
    model_cfg = config.get("model", {})
    paths = config.get("paths", {})

    mode = str(run_cfg.get("mode", "bash")).strip().lower() or "bash"
    if mode not in _ALLOWED_MODES:
        raise ValueError(f"Invalid mode: {mode}")

    dataset_root = _resolve_path(paths.get("dataset_root"), "paths.dataset_root")
    repos_root_value = paths.get("repos_root")
    if not repos_root_value or not str(repos_root_value).strip():
        repos_root = (dataset_root / "repos").resolve()
    else:
        repos_root = _resolve_path(repos_root_value, "paths.repos_root")
    paths["repos_root"] = str(repos_root)
    output_root = _resolve_output_root(paths.get("output_root"), root=project_root())
    output_model_name = _normalize_optional(paths.get("output_model_name"))
    if not output_model_name:
        raise ValueError("paths.output_model_name must be set")
    validate_output_model_name(output_model_name)

    indexes_root = paths.get("indexes_root")
    model_root = paths.get("model_root")

    repos = _normalize_repos(run_cfg.get("repos"))
    slice_spec = str(run_cfg.get("slice") or "")
    shuffle = bool(run_cfg.get("shuffle", False))
    shuffle_seed = int(run_cfg.get("shuffle_seed", 42))
    workers = int(run_cfg.get("workers", 1))
    redo_existing = bool(run_cfg.get("redo_existing", False))
    output_dir = str(run_cfg.get("output_dir") or "")
    method = _default_method(mode, run_cfg.get("method"))
    image = _normalize_optional(run_cfg.get("image"))
    environment_class = _normalize_optional(run_cfg.get("environment_class"))

    model_name = _normalize_optional(model_cfg.get("model_name"))
    model_class = _normalize_optional(model_cfg.get("model_class"))
    billing = config.get("billing")
    pricing = None

    tools_prompt = _normalize_tools_prompt(run_cfg.get("tools_prompt"))
    if mode == "tools" and tools_prompt not in _ALLOWED_TOOLS_PROMPTS:
        raise ValueError(f"Invalid tools_prompt: {tools_prompt}")

    root = project_root()
    if mode == "tools":
        default_agent = root / "swe_qa_bench" / "config" / f"agent_tools_{tools_prompt}.yaml"
    else:
        default_agent = root / "swe_qa_bench" / "config" / "agent_bash.yaml"

    agent_config_override = _normalize_optional(args.agent_config)
    if agent_config_override:
        agent_config = Path(agent_config_override).expanduser().resolve()
    elif mode == "tools" and args.tools_prompt:
        agent_config = default_agent
    else:
        agent_config = Path(str(run_cfg.get("agent_config") or default_agent)).expanduser().resolve()
    if not agent_config.exists():
        raise ValueError(f"Agent config not found: {agent_config}")

    tool_config = None
    if mode == "tools":
        default_tool = root / "swe_qa_bench" / "config" / "code_search.yaml"
        tool_config = Path(str(run_cfg.get("tool_config") or default_tool)).expanduser().resolve()
        if not tool_config.exists():
            raise ValueError(f"Tool config not found: {tool_config}")
        if not indexes_root or not model_root:
            raise ValueError("paths.indexes_root and paths.model_root must be set for tools mode")
        indexes_root = str(_resolve_path(indexes_root, "paths.indexes_root"))
        model_root = str(_resolve_path(model_root, "paths.model_root"))

    effective_method = _apply_tools_prompt_suffix(method, tools_prompt) if mode == "tools" else method

    _log_summary(config, mode=mode, agent_config=agent_config, tool_config=tool_config)

    if mode == "bash":
        runner = runners.BashRunner(
            dataset_root=dataset_root,
            repos_root=repos_root,
            output_root=output_root,
            repos=repos,
            slice_spec=slice_spec,
            shuffle=shuffle,
            shuffle_seed=shuffle_seed,
            workers=workers,
            config_path=agent_config,
            model=model_name,
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

    runner = runners.ToolsRunner(
        dataset_root=dataset_root,
        repos_root=repos_root,
        output_root=output_root,
        repos=repos,
        slice_spec=slice_spec,
        shuffle=shuffle,
        shuffle_seed=shuffle_seed,
        workers=workers,
        config_path=agent_config,
        tool_config_path=tool_config,
        model=model_name,
        model_class=model_class,
        environment_class=environment_class,
        image=image,
        output_model_name=output_model_name,
        method=effective_method,
        output_dir=output_dir,
        redo_existing=redo_existing,
        indexes_root=indexes_root,
        model_root=model_root,
        tools_prompt=tools_prompt,
        pricing=pricing,
        billing=billing,
    )
    runner.run()


if __name__ == "__main__":
    main()
