#!/usr/bin/env python3

"""Unified LocBench runner entrypoint."""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path
from typing import Any

from minisweagent.locbench import runners
from minisweagent.locbench.config_loader import load_config, project_root
from minisweagent.locbench.utils import validate_output_model_name
from minisweagent.utils.log import logger

_ALLOWED_MODES = {"bash", "tools", "ir"}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LocBench with layered config")
    parser.add_argument("--config-dir", default="", help="Config directory (default: locbench/config)")

    parser.add_argument("--mode", choices=sorted(_ALLOWED_MODES), help="Run mode: bash, tools, or ir")
    parser.add_argument("--dataset-root", help="Override paths.dataset_root")
    parser.add_argument("--repos-root", help="Override paths.repos_root")
    parser.add_argument("--indexes-root", help="Override paths.indexes_root (tools/ir)")
    parser.add_argument("--model-root", help="Override paths.model_root (tools/ir)")
    parser.add_argument("--worktrees-root", help="Override paths.worktrees_root")
    parser.add_argument("--output-root", help="Override paths.output_root")
    parser.add_argument("--output-model-name", help="Override paths.output_model_name")
    parser.add_argument("--output-dir", help="Override run.output_dir")

    parser.add_argument("--slice", dest="slice_spec", help="Slice spec, e.g. 0:20")
    parser.add_argument("--filter", dest="filter_spec", help="Regex filter on instance_id")
    parser.add_argument("--shuffle", action=argparse.BooleanOptionalAction, help="Shuffle instances")
    parser.add_argument("--shuffle-seed", type=int, help="Random seed for shuffling")
    parser.add_argument("--skip-missing", action=argparse.BooleanOptionalAction, help="Skip missing repos")
    parser.add_argument("--workers", type=int, help="Number of worker threads")
    parser.add_argument("--redo-existing", action=argparse.BooleanOptionalAction, help="Redo existing outputs")
    parser.add_argument("--keep-worktrees", action=argparse.BooleanOptionalAction, help="Keep worktrees after run")
    parser.add_argument("--worktrees-mode", choices=["ephemeral", "reusable"], help="Worktree mode")
    parser.add_argument("--worktrees-gc-hours", type=int, help="GC worktrees older than N hours (0 to disable)")

    parser.add_argument("--model", dest="model_name", help="Override model.model_name")
    parser.add_argument("--model-class", help="Override model.model_class")
    parser.add_argument("--image", help="Override run.image")
    parser.add_argument("--method", help="Override run.method")
    parser.add_argument("--agent-config", help="Override run.agent_config")
    parser.add_argument("--tool-config", help="Override run.tool_config")
    parser.add_argument("--environment-class", help="Override run.environment_class")

    parser.add_argument("--topk-blocks", type=int, help="IR: top blocks")
    parser.add_argument("--topk-files", type=int, help="IR: top files")
    parser.add_argument("--topk-modules", type=int, help="IR: top modules")
    parser.add_argument("--topk-entities", type=int, help="IR: top entities")
    parser.add_argument("--filters", help="IR: code_search filters")
    parser.add_argument("--mapper-type", help="IR: mapper type (ast/graph)")
    parser.add_argument("--graph-index-dir", help="IR: graph index directory")
    parser.add_argument("--force-rebuild", action=argparse.BooleanOptionalAction, help="IR: rebuild index")

    return parser.parse_args(argv)


def _normalize_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _build_overrides(args: argparse.Namespace) -> dict[str, Any]:
    overrides: dict[str, Any] = {"run": {}, "model": {}, "paths": {}}

    if args.mode:
        overrides["run"]["mode"] = args.mode
    if args.slice_spec is not None:
        overrides["run"]["slice"] = args.slice_spec
    if args.filter_spec is not None:
        overrides["run"]["filter"] = args.filter_spec
    if args.shuffle is not None:
        overrides["run"]["shuffle"] = args.shuffle
    if args.shuffle_seed is not None:
        overrides["run"]["shuffle_seed"] = args.shuffle_seed
    if args.skip_missing is not None:
        overrides["run"]["skip_missing"] = args.skip_missing
    if args.workers is not None:
        overrides["run"]["workers"] = args.workers
    if args.redo_existing is not None:
        overrides["run"]["redo_existing"] = args.redo_existing
    if args.keep_worktrees is not None:
        overrides["run"]["keep_worktrees"] = args.keep_worktrees
    if args.worktrees_mode:
        overrides["run"]["worktrees_mode"] = args.worktrees_mode
    if args.worktrees_gc_hours is not None:
        overrides["run"]["worktrees_gc_hours"] = args.worktrees_gc_hours
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
    if args.environment_class:
        overrides["run"]["environment_class"] = args.environment_class

    if args.topk_blocks is not None:
        overrides["run"]["topk_blocks"] = args.topk_blocks
    if args.topk_files is not None:
        overrides["run"]["topk_files"] = args.topk_files
    if args.topk_modules is not None:
        overrides["run"]["topk_modules"] = args.topk_modules
    if args.topk_entities is not None:
        overrides["run"]["topk_entities"] = args.topk_entities
    if args.filters is not None:
        overrides["run"]["filters"] = args.filters
    if args.mapper_type is not None:
        overrides["run"]["mapper_type"] = args.mapper_type
    if args.graph_index_dir is not None:
        overrides["run"]["graph_index_dir"] = args.graph_index_dir
    if args.force_rebuild is not None:
        overrides["run"]["force_rebuild"] = args.force_rebuild

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
    if args.worktrees_root:
        overrides["paths"]["worktrees_root"] = args.worktrees_root
    if args.output_root:
        overrides["paths"]["output_root"] = args.output_root
    if args.output_model_name:
        overrides["paths"]["output_model_name"] = args.output_model_name

    overrides = {k: v for k, v in overrides.items() if v}
    return overrides


def _default_method(mode: str, value: str | None) -> str:
    if value:
        return value
    if mode == "tools":
        return "miniswe_tools"
    if mode == "ir":
        return "miniswe_ir"
    return "miniswe_bash"


def _resolve_path(value: Any, label: str) -> Path:
    if not value or not str(value).strip():
        raise ValueError(f"Missing required path: {label}")
    path = Path(str(value)).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"Path not found for {label}: {path}")
    return path


def _resolve_dir(value: Any, label: str, *, create: bool = False) -> Path:
    if not value or not str(value).strip():
        raise ValueError(f"Missing required path: {label}")
    path = Path(str(value)).expanduser().resolve()
    if path.exists():
        return path
    if create:
        path.mkdir(parents=True, exist_ok=True)
        return path
    raise ValueError(f"Path not found for {label}: {path}")


def _resolve_output_root(value: Any, *, root: Path) -> Path:
    if not value or not str(value).strip():
        path = root / "locbench" / "results"
    else:
        path = Path(str(value)).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _gc_worktrees(worktrees_root: Path, max_age_hours: int) -> None:
    if max_age_hours <= 0:
        return
    if not worktrees_root.exists():
        return
    cutoff = time.time() - (max_age_hours * 3600)
    for mode_dir in worktrees_root.iterdir():
        if not mode_dir.is_dir():
            continue
        for entry in mode_dir.iterdir():
            if not entry.is_dir():
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue


def _normalize_graph_index_dir(value: Any) -> str:
    if not value or not str(value).strip():
        return ""
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = project_root() / path
    return str(path.resolve())


def _log_summary(
    config: dict[str, Any],
    *,
    mode: str,
    agent_config: Path,
    tool_config: Path | None,
) -> None:
    run_cfg = config.get("run", {})
    model_cfg = config.get("model", {})
    paths = config.get("paths", {})

    summary = {
        "mode": mode,
        "dataset_root": paths.get("dataset_root"),
        "repos_root": paths.get("repos_root"),
        "indexes_root": paths.get("indexes_root"),
        "model_root": paths.get("model_root"),
        "worktrees_root": paths.get("worktrees_root"),
        "output_root": paths.get("output_root"),
        "worktrees_mode": run_cfg.get("worktrees_mode"),
        "worktrees_gc_hours": run_cfg.get("worktrees_gc_hours"),
        "output_model_name": paths.get("output_model_name"),
        "model_name": model_cfg.get("model_name"),
        "model_class": model_cfg.get("model_class"),
        "image": run_cfg.get("image"),
        "workers": run_cfg.get("workers"),
        "slice": run_cfg.get("slice"),
        "filter": run_cfg.get("filter"),
        "shuffle": run_cfg.get("shuffle"),
        "skip_missing": run_cfg.get("skip_missing"),
        "redo_existing": run_cfg.get("redo_existing"),
        "output_dir": run_cfg.get("output_dir"),
        "agent_config": str(agent_config),
        "tool_config": str(tool_config) if tool_config else "",
    }

    logger.info("Effective LocBench config summary:")
    for key, value in summary.items():
        if value is None:
            value = ""
        logger.info("  %s: %s", key, value)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    config_dir = Path(args.config_dir).expanduser().resolve() if args.config_dir else None

    config = load_config(config_dir=config_dir, overrides=_build_overrides(args))

    run_cfg = config.get("run", {})
    model_cfg = config.get("model", {})
    paths = config.get("paths", {})

    mode = str(run_cfg.get("mode", "bash")).strip().lower() or "bash"
    if mode not in _ALLOWED_MODES:
        raise ValueError(f"Invalid mode: {mode}")

    dataset_path = _resolve_path(paths.get("dataset_root"), "paths.dataset_root")
    if dataset_path.is_dir():
        dataset_path = (dataset_path / "Loc-Bench_V1_dataset.jsonl").resolve()
    repos_root_value = paths.get("repos_root")
    if not repos_root_value or not str(repos_root_value).strip():
        repos_root = (project_root() / "locbench_repos").resolve()
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
    worktrees_root = _resolve_dir(paths.get("worktrees_root"), "paths.worktrees_root", create=True)

    slice_spec = str(run_cfg.get("slice") or "")
    filter_spec = str(run_cfg.get("filter") or "")
    shuffle = bool(run_cfg.get("shuffle", False))
    shuffle_seed = int(run_cfg.get("shuffle_seed", 42))
    skip_missing = bool(run_cfg.get("skip_missing", False))
    workers = int(run_cfg.get("workers", 1))
    redo_existing = bool(run_cfg.get("redo_existing", False))
    keep_worktrees = bool(run_cfg.get("keep_worktrees", False))
    worktrees_mode = str(run_cfg.get("worktrees_mode", "ephemeral")).strip().lower() or "ephemeral"
    if worktrees_mode not in {"ephemeral", "reusable"}:
        raise ValueError(f"Invalid worktrees_mode: {worktrees_mode}")
    worktrees_gc_hours = int(run_cfg.get("worktrees_gc_hours") or 0)
    output_dir = str(run_cfg.get("output_dir") or "")
    method = _default_method(mode, run_cfg.get("method"))
    image = _normalize_optional(run_cfg.get("image"))
    environment_class = _normalize_optional(run_cfg.get("environment_class"))

    model_name = _normalize_optional(model_cfg.get("model_name"))
    model_class = _normalize_optional(model_cfg.get("model_class"))
    billing = config.get("billing")
    pricing = None

    root = project_root()
    default_agent = root / "locbench" / "config" / ("agent_tools.yaml" if mode == "tools" else "agent_bash.yaml")
    agent_config = Path(str(run_cfg.get("agent_config") or default_agent)).expanduser().resolve()
    if not agent_config.exists():
        raise ValueError(f"Agent config not found: {agent_config}")

    tool_config = None
    if mode in {"tools", "ir"}:
        default_tool = root / "locbench" / "config" / "code_search.yaml"
        tool_config = Path(str(run_cfg.get("tool_config") or default_tool)).expanduser().resolve()
        if not tool_config.exists():
            raise ValueError(f"Tool config not found: {tool_config}")
        if not indexes_root or not model_root:
            raise ValueError("paths.indexes_root and paths.model_root must be set for tools/ir mode")
        indexes_root = str(_resolve_dir(indexes_root, "paths.indexes_root", create=True))
        model_root = str(_resolve_path(model_root, "paths.model_root"))

    run_cfg["graph_index_dir"] = _normalize_graph_index_dir(run_cfg.get("graph_index_dir", ""))

    _log_summary(config, mode=mode, agent_config=agent_config, tool_config=tool_config)

    _gc_worktrees(worktrees_root, worktrees_gc_hours)

    if mode == "bash":
        runner = runners.BashRunner(
            dataset_path=dataset_path,
            repos_root=repos_root,
            output_root=output_root,
            worktrees_root=worktrees_root,
            slice_spec=slice_spec,
            filter_spec=filter_spec,
            shuffle=shuffle,
            shuffle_seed=shuffle_seed,
            skip_missing=skip_missing,
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

    if mode == "tools":
        runner = runners.ToolsRunner(
            dataset_path=dataset_path,
            repos_root=repos_root,
            output_root=output_root,
            worktrees_root=worktrees_root,
            slice_spec=slice_spec,
            filter_spec=filter_spec,
            shuffle=shuffle,
            shuffle_seed=shuffle_seed,
            skip_missing=skip_missing,
            workers=workers,
            config_path=agent_config,
            tool_config_path=tool_config,
            model=model_name,
            model_class=model_class,
            environment_class=environment_class,
            image=image,
            output_model_name=output_model_name,
            method=method,
            output_dir=output_dir,
            redo_existing=redo_existing,
            indexes_root=indexes_root,
            model_root=model_root,
            keep_worktrees=keep_worktrees,
            worktrees_mode=worktrees_mode,
            pricing=pricing,
            billing=billing,
        )
        runner.run()
        return

    runner = runners.IRRunner(
        dataset_path=dataset_path,
        repos_root=repos_root,
        output_root=output_root,
        worktrees_root=worktrees_root,
        slice_spec=slice_spec,
        filter_spec=filter_spec,
        shuffle=shuffle,
        shuffle_seed=shuffle_seed,
        skip_missing=skip_missing,
        output_model_name=output_model_name,
        method=method,
        output_dir=output_dir,
        tool_config_path=tool_config,
        indexes_root=indexes_root,
        model_root=model_root,
        topk_blocks=int(run_cfg.get("topk_blocks", 50)),
        topk_files=int(run_cfg.get("topk_files", 10)),
        topk_modules=int(run_cfg.get("topk_modules", 10)),
        topk_entities=int(run_cfg.get("topk_entities", 50)),
        filters=str(run_cfg.get("filters") or ""),
        mapper_type=str(run_cfg.get("mapper_type") or "ast"),
        graph_index_dir=str(run_cfg.get("graph_index_dir") or ""),
        force_rebuild=bool(run_cfg.get("force_rebuild", False)),
        redo_existing=redo_existing,
        keep_worktrees=keep_worktrees,
        worktrees_mode=worktrees_mode,
    )
    runner.run()


if __name__ == "__main__":
    main()
