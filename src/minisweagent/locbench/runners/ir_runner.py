#!/usr/bin/env python3

"""Run LocBench IR-only evaluation using code_search."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import yaml
from rich.live import Live

from minisweagent.config import get_config_path
from minisweagent.run.extra.utils.batch_progress import RunBatchProgressManager
from minisweagent.locbench.config_loader import project_root
from minisweagent.locbench.utils import (
    build_meta,
    build_repo_dir_name,
    build_repo_path,
    filter_instances,
    load_existing_instance_ids,
    load_jsonl,
    sanitize_component,
    validate_output_model_name,
    append_jsonl,
)
from minisweagent.tools.code_search import CodeSearchTool
from minisweagent.tools.code_search.mapping import ASTBasedMapper, GraphBasedMapper
from minisweagent.tools.code_search.tool import sanitize_id
from minisweagent.tools.code_search.utils import clean_file_path
from minisweagent.utils.log import add_file_handler, logger

_OUTPUT_FILE_LOCK = threading.Lock()
_WORKTREE_LOCK = threading.Lock()


class IRRunner:
    def __init__(
        self,
        *,
        dataset_path: Path,
        repos_root: Path,
        output_root: Path,
        worktrees_root: Path,
        slice_spec: str,
        filter_spec: str,
        shuffle: bool,
        shuffle_seed: int,
        skip_missing: bool,
        output_model_name: str,
        method: str,
        output_dir: str,
        tool_config_path: Path,
        indexes_root: str | None,
        model_root: str | None,
        topk_blocks: int,
        topk_files: int,
        topk_modules: int,
        topk_entities: int,
        filters: str,
        mapper_type: str,
        graph_index_dir: str,
        force_rebuild: bool,
        redo_existing: bool,
        keep_worktrees: bool,
    ) -> None:
        self.dataset_path = dataset_path
        self.repos_root = repos_root
        self.output_root = output_root
        self.worktrees_root = worktrees_root
        self.slice_spec = slice_spec
        self.filter_spec = filter_spec
        self.shuffle = shuffle
        self.shuffle_seed = shuffle_seed
        self.skip_missing = skip_missing
        self.output_model_name = output_model_name
        self.method = method
        self.output_dir = output_dir
        self.tool_config_path = tool_config_path
        self.indexes_root = indexes_root
        self.model_root = model_root
        self.topk_blocks = topk_blocks
        self.topk_files = topk_files
        self.topk_modules = topk_modules
        self.topk_entities = topk_entities
        self.filters = filters
        self.mapper_type = mapper_type
        self.graph_index_dir = graph_index_dir
        self.force_rebuild = force_rebuild
        self.redo_existing = redo_existing
        self.keep_worktrees = keep_worktrees

    def run(self) -> None:
        run_ir(
            dataset_path=self.dataset_path,
            repos_root=self.repos_root,
            output_root=self.output_root,
            worktrees_root=self.worktrees_root,
            slice_spec=self.slice_spec,
            filter_spec=self.filter_spec,
            shuffle=self.shuffle,
            shuffle_seed=self.shuffle_seed,
            skip_missing=self.skip_missing,
            output_model_name=self.output_model_name,
            method=self.method,
            output_dir=self.output_dir,
            tool_config_path=self.tool_config_path,
            indexes_root=self.indexes_root,
            model_root=self.model_root,
            topk_blocks=self.topk_blocks,
            topk_files=self.topk_files,
            topk_modules=self.topk_modules,
            topk_entities=self.topk_entities,
            filters=self.filters,
            mapper_type=self.mapper_type,
            graph_index_dir=self.graph_index_dir,
            force_rebuild=self.force_rebuild,
            redo_existing=self.redo_existing,
            keep_worktrees=self.keep_worktrees,
        )


def _default_output_dir(output_model_name: str, method: str) -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    root_dir = project_root() / "locbench" / "outputs"
    model_dir = sanitize_component(output_model_name)
    method_dir = sanitize_component(method)
    return root_dir / model_dir / method_dir / timestamp


def _default_loc_output(output_root: Path, output_model_name: str, method: str) -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    model_dir = sanitize_component(output_model_name)
    method_dir = sanitize_component(method)
    return output_root / "loc_output" / model_dir / method_dir / f"loc_outputs_{timestamp}.jsonl"


def _run_git(repo_path: Path, args: list[str]) -> str:
    cmd = ["git", "-c", "safe.directory=*", "-C", str(repo_path), *args]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _resolve_commit(repo_path: Path, ref: str) -> str:
    return _run_git(repo_path, ["rev-parse", ref])


def _remove_worktree(repo_path: Path, worktree_path: Path) -> None:
    subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(repo_path), "worktree", "remove", "--force", str(worktree_path)],
        capture_output=True,
        text=True,
    )
    if worktree_path.exists():
        shutil.rmtree(worktree_path, ignore_errors=True)


def ensure_worktree(repo_path: Path, repo_dir: str, base_commit: str, worktree_root: Path) -> tuple[Path, str]:
    commit = _resolve_commit(repo_path, base_commit)
    worktree_path = worktree_root / f"{repo_dir}@{commit[:8]}"
    with _WORKTREE_LOCK:
        if worktree_path.exists():
            try:
                head = _run_git(worktree_path, ["rev-parse", "HEAD"])
                if head == commit:
                    return worktree_path, commit
            except subprocess.SubprocessError:
                pass
            _remove_worktree(repo_path, worktree_path)
        worktree_root.mkdir(parents=True, exist_ok=True)
        _run_git(repo_path, ["worktree", "add", "--detach", str(worktree_path), commit])
    return worktree_path, commit


def prepare_mapper_root(worktree_root: Path, instance_id: str, repo_dir: str, worktree_path: Path) -> Path:
    mapper_root = worktree_root / "mapper_roots" / instance_id
    mapper_root.mkdir(parents=True, exist_ok=True)
    link_path = mapper_root / repo_dir
    if link_path.exists() or link_path.is_symlink():
        try:
            if link_path.resolve() == worktree_path.resolve():
                return mapper_root
        except OSError:
            pass
        if link_path.is_dir() and not link_path.is_symlink():
            shutil.rmtree(link_path, ignore_errors=True)
        else:
            link_path.unlink(missing_ok=True)
    try:
        os.symlink(worktree_path, link_path, target_is_directory=True)
    except OSError:
        shutil.copytree(worktree_path, link_path, dirs_exist_ok=True)
    return mapper_root


def _append_loc_output(path: Path, record: dict[str, Any]) -> None:
    with _OUTPUT_FILE_LOCK:
        append_jsonl(path, record)


def _build_instances(
    records: list[dict[str, Any]],
    repo_root: Path,
    *,
    skip_missing: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    missing_repos: list[str] = []
    instances: list[dict[str, Any]] = []
    for record in records:
        instance_id = record.get("instance_id")
        repo_slug = record.get("repo")
        if not instance_id or not repo_slug:
            continue
        repo_dir = build_repo_dir_name(repo_slug)
        repo_path = build_repo_path(repo_root, repo_slug).resolve()
        if not repo_path.exists():
            if skip_missing:
                continue
            missing_repos.append(repo_slug)
            continue
        instance = {
            "instance_id": instance_id,
            "repo_slug": repo_slug,
            "repo_dir": repo_dir,
            "repo_path": str(repo_path),
            "base_commit": record.get("base_commit") or "HEAD",
            "problem_statement": record.get("problem_statement") or "",
        }
        instances.append(instance)
    return instances, missing_repos


def _get_index_dir(config: dict[str, Any], repo_dir: str, commit: str) -> Path:
    index_root = Path(os.path.expanduser(config["index_root"])).resolve()
    embedder_id = sanitize_id(f"{config['embedding_provider']}_{config['embedding_model']}")
    return index_root / repo_dir / commit[:8] / embedder_id


def run_ir(
    *,
    dataset_path: Path,
    repos_root: Path,
    output_root: Path,
    worktrees_root: Path,
    slice_spec: str,
    filter_spec: str,
    shuffle: bool,
    shuffle_seed: int,
    skip_missing: bool,
    output_model_name: str,
    method: str,
    output_dir: str,
    tool_config_path: Path,
    indexes_root: str | None,
    model_root: str | None,
    topk_blocks: int,
    topk_files: int,
    topk_modules: int,
    topk_entities: int,
    filters: str,
    mapper_type: str,
    graph_index_dir: str,
    force_rebuild: bool,
    redo_existing: bool,
    keep_worktrees: bool,
) -> None:
    dataset_path = dataset_path.resolve()
    repos_root = repos_root.resolve()
    output_root = output_root.resolve()
    worktrees_root = (worktrees_root / "ir").resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    if not dataset_path.exists():
        raise ValueError(f"Dataset not found: {dataset_path}")
    if not repos_root.exists():
        raise ValueError(f"Repo root not found: {repos_root}")
    validate_output_model_name(output_model_name)

    tool_config_path = get_config_path(tool_config_path)
    config = yaml.safe_load(tool_config_path.read_text())
    if indexes_root:
        config["index_root"] = str(indexes_root)
    if model_root:
        config["embedding_model"] = str(model_root)
    tool = CodeSearchTool(config)

    if output_dir:
        output_dir_path = Path(output_dir)
    else:
        output_dir_path = _default_output_dir(output_model_name, method)
    output_dir_path.mkdir(parents=True, exist_ok=True)
    add_file_handler(output_dir_path / "minisweagent.log")

    loc_output_path = _default_loc_output(output_root, output_model_name, method)
    logger.info("Loc outputs will be saved to %s", loc_output_path)

    records = load_jsonl(dataset_path)
    bench_data = {item.get("instance_id"): item for item in records if item.get("instance_id")}
    instances, missing_repos = _build_instances(records, repos_root, skip_missing=skip_missing)
    if missing_repos and not skip_missing:
        missing_preview = ", ".join(missing_repos[:10])
        raise ValueError(
            f"Missing {len(missing_repos)} repos (first 10: {missing_preview}). Use skip_missing to ignore."
        )

    instances = filter_instances(
        instances,
        filter_spec=filter_spec,
        slice_spec=slice_spec,
        shuffle=shuffle,
        shuffle_seed=shuffle_seed,
    )
    if not redo_existing:
        existing_ids = load_existing_instance_ids(loc_output_path)
        if existing_ids:
            logger.info("Skipping %s existing instances from %s", len(existing_ids), loc_output_path)
            instances = [instance for instance in instances if instance["instance_id"] not in existing_ids]
    logger.info("Running on %s instances...", len(instances))

    progress_manager = RunBatchProgressManager(len(instances), output_dir_path / f"exit_statuses_{time.time()}.yaml")

    with Live(progress_manager.render_group, refresh_per_second=4):
        for instance in instances:
            instance_id = instance["instance_id"]
            repo_dir = instance["repo_dir"]
            repo_path = Path(instance["repo_path"])
            base_commit = instance["base_commit"]
            exit_status = "Completed"

            progress_manager.on_instance_start(instance_id)
            progress_manager.update_instance_status(instance_id, "Preparing worktree")

            try:
                worktree_path, resolved_commit = ensure_worktree(repo_path, repo_dir, base_commit, worktrees_root)

                if force_rebuild:
                    index_dir = _get_index_dir(config, repo_dir, resolved_commit)
                    shutil.rmtree(index_dir, ignore_errors=True)

                tool_context = {
                    "repo_path": str(worktree_path),
                    "repo_dir": repo_dir,
                    "repo_slug": instance["repo_slug"],
                    "base_commit": resolved_commit,
                    "instance_id": instance_id,
                }

                progress_manager.update_instance_status(instance_id, "Searching")
                tool_result = tool.run(
                    {"query": instance["problem_statement"], "topk": topk_blocks, "filters": filters or None},
                    tool_context,
                )
                if not tool_result.success:
                    raise RuntimeError(tool_result.error or tool_result.output)

                progress_manager.update_instance_status(instance_id, "Mapping")
                results = tool_result.data.get("results", [])
                file_scores: dict[str, float] = {}
                blocks = []
                for item in results:
                    path = clean_file_path(item["path"], repo_dir)
                    file_scores[path] = file_scores.get(path, 0.0) + float(item.get("score", 0.0))
                    line_span = item.get("line_span") or {}
                    start_line = max(0, int(line_span.get("start", 1)) - 1)
                    end_line = max(0, int(line_span.get("end", 1)) - 1)
                    blocks.append(
                        {
                            "file_path": path,
                            "start_line": start_line,
                            "end_line": end_line,
                            "span_ids": item.get("span_ids", []),
                        }
                    )

                ranked_files = sorted(file_scores.items(), key=lambda x: x[1], reverse=True)
                found_files = [path for path, _ in ranked_files[:topk_files]]

                if mapper_type == "graph":
                    if not graph_index_dir:
                        raise ValueError("graph_index_dir is required for graph mapper")
                    mapper = GraphBasedMapper(graph_index_dir)
                else:
                    mapper_root = prepare_mapper_root(worktrees_root, instance_id, repo_dir, worktree_path)
                    mapper = ASTBasedMapper(str(mapper_root))

                if mapper_type == "graph":
                    if not any(block.get("span_ids") for block in blocks):
                        raise ValueError("Graph mapper requires span_ids; sliding chunks do not provide them.")

                found_modules, found_entities = mapper.map_blocks_to_entities(
                    blocks=blocks,
                    instance_id=instance_id,
                    top_k_modules=topk_modules,
                    top_k_entities=topk_entities,
                )

                output_record = {
                    "instance_id": instance_id,
                    "found_files": found_files,
                    "found_modules": found_modules,
                    "found_entities": found_entities,
                    "raw_output_loc": [],
                    "meta_data": build_meta(bench_data.get(instance_id)),
                    "stats": {"api_calls": 0, "cost_usd": 0.0, "billing_mode": "none"},
                }
                _append_loc_output(loc_output_path, output_record)
            except Exception as exc:
                exit_status = type(exc).__name__
                logger.error("Error processing instance %s: %s", instance_id, exc, exc_info=True)
                output_record = {
                    "instance_id": instance_id,
                    "found_files": [],
                    "found_modules": [],
                    "found_entities": [],
                    "raw_output_loc": [],
                    "error": str(exc),
                    "meta_data": build_meta(bench_data.get(instance_id)),
                    "stats": {"api_calls": 0, "cost_usd": 0.0, "billing_mode": "none"},
                }
                _append_loc_output(loc_output_path, output_record)
            finally:
                progress_manager.on_instance_end(instance_id, exit_status)

    if not keep_worktrees and worktrees_root.exists():
        shutil.rmtree(worktrees_root, ignore_errors=True)

    progress_manager.print_report()
