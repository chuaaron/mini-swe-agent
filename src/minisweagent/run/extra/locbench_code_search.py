#!/usr/bin/env python3

"""Run code_search only (IR-style) evaluation on LocBench instances."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.live import Live

from minisweagent import package_dir
from minisweagent.config import builtin_config_dir, get_config_path
from minisweagent.run.extra.utils.batch_progress import RunBatchProgressManager
from minisweagent.tools.code_search import CodeSearchTool
from minisweagent.tools.code_search.mapping import ASTBasedMapper, GraphBasedMapper
from minisweagent.tools.code_search.tool import sanitize_id
from minisweagent.tools.code_search.utils import clean_file_path
from minisweagent.utils.log import add_file_handler, logger

_HELP_TEXT = "Run code_search-only evaluation on LocBench (IR-only baseline)."

app = typer.Typer(rich_markup_mode="rich", add_completion=False)

_OUTPUT_FILE_LOCK = threading.Lock()
_WORKTREE_LOCK = threading.Lock()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_num, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_num} of {path}") from exc
    return data


def sanitize_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


def build_repo_dir_name(repo_slug: str) -> str:
    return repo_slug.replace("/", "_")


def filter_instances(
    instances: list[dict[str, Any]],
    *,
    filter_spec: str,
    slice_spec: str = "",
    shuffle: bool = False,
    shuffle_seed: int = 42,
) -> list[dict[str, Any]]:
    if shuffle:
        instances = sorted(instances.copy(), key=lambda x: x["instance_id"])
        rng = __import__("random")
        rng.seed(shuffle_seed)
        rng.shuffle(instances)
    before_filter = len(instances)
    if filter_spec:
        instances = [instance for instance in instances if re.search(filter_spec, instance["instance_id"])]
    if (after_filter := len(instances)) != before_filter:
        logger.info(f"Instance filter: {before_filter} -> {after_filter} instances")
    if slice_spec:
        values = [int(x) if x else None for x in slice_spec.split(":")]
        instances = instances[slice(*values)]
        if (after_slice := len(instances)) != before_filter:
            logger.info(f"Instance slice: {before_filter} -> {after_slice} instances")
    return instances


def load_existing_instance_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    existing = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            instance_id = record.get("instance_id")
            if instance_id:
                existing.add(instance_id)
    return existing


def append_loc_output(path: Path, record: dict[str, Any]) -> None:
    with _OUTPUT_FILE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def build_meta(record: dict[str, Any] | None) -> dict[str, Any]:
    if not record:
        return {}
    meta = {}
    for key in ("repo", "base_commit", "problem_statement", "patch", "test_patch"):
        if key in record:
            meta[key] = record[key]
    return meta


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


def default_loc_output(model_name: str, provider: str) -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    root_dir = package_dir.parents[1] / "locbench"
    provider_dir = sanitize_component(provider)
    model_dir = sanitize_component(model_name)
    return root_dir / "loc_output" / "code_search" / provider_dir / model_dir / f"loc_outputs_{timestamp}.jsonl"


def build_instances(
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
        repo_path = (repo_root / repo_dir).resolve()
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


def get_index_dir(config: dict[str, Any], repo_dir: str, commit: str) -> Path:
    index_root = Path(os.path.expanduser(config["index_root"])).resolve()
    embedder_id = sanitize_id(f"{config['embedding_provider']}_{config['embedding_model']}")
    return index_root / repo_dir / commit[:8] / embedder_id


# fmt: off
@app.command(help=_HELP_TEXT)
def main(
    dataset: Path = typer.Option(..., "--dataset", help="Path to Loc-Bench JSONL", rich_help_panel="Data selection"),
    repos_root: Path = typer.Option(..., "--repos-root", help="Root dir for local repo mirrors", rich_help_panel="Data selection"),
    slice_spec: str = typer.Option("", "--slice", help="Slice specification (e.g., '0:5')", rich_help_panel="Data selection"),
    filter_spec: str = typer.Option("", "--filter", help="Filter instance IDs by regex", rich_help_panel="Data selection"),
    shuffle: bool = typer.Option(False, "--shuffle", help="Shuffle instances", rich_help_panel="Data selection"),
    shuffle_seed: int = typer.Option(42, "--shuffle-seed", help="Random seed for shuffling", rich_help_panel="Data selection"),
    skip_missing: bool = typer.Option(False, "--skip-missing", help="Skip instances with missing repos", rich_help_panel="Data selection"),
    loc_output: str = typer.Option("", "--loc-output", help="Output loc_outputs.jsonl path", rich_help_panel="Basic"),
    output: str = typer.Option("", "-o", "--output", help="Output directory for logs", rich_help_panel="Basic"),
    config_spec: Path = typer.Option(builtin_config_dir / "extra" / "code_search.yaml", "-c", "--config", help="Path to code_search config", rich_help_panel="Basic"),
    topk_blocks: int = typer.Option(50, "--topk-blocks", help="Top blocks to retrieve", rich_help_panel="Retrieval"),
    topk_files: int = typer.Option(10, "--topk-files", help="Top files to output", rich_help_panel="Retrieval"),
    topk_modules: int = typer.Option(10, "--topk-modules", help="Top modules to output", rich_help_panel="Retrieval"),
    topk_entities: int = typer.Option(50, "--topk-entities", help="Top entities to output", rich_help_panel="Retrieval"),
    filters: str = typer.Option("", "--filters", help="Optional filters (e.g., 'lang:python path:src/')", rich_help_panel="Retrieval"),
    mapper_type: str = typer.Option("ast", "--mapper-type", help="Mapper type: ast or graph", rich_help_panel="Retrieval"),
    graph_index_dir: str = typer.Option("", "--graph-index-dir", help="Graph index directory (graph mapper)", rich_help_panel="Retrieval"),
    force_rebuild: bool = typer.Option(False, "--force-rebuild", help="Force rebuild index", rich_help_panel="Advanced"),
    redo_existing: bool = typer.Option(False, "--redo-existing", help="Redo existing instances", rich_help_panel="Advanced"),
) -> None:
    # fmt: on
    dataset_path = Path(dataset)
    repo_root = Path(repos_root).resolve()
    if not dataset_path.exists():
        raise typer.BadParameter(f"Dataset not found: {dataset_path}")
    if not repo_root.exists():
        raise typer.BadParameter(f"Repo root not found: {repo_root}")

    config_path = get_config_path(config_spec)
    config = yaml.safe_load(config_path.read_text())
    tool = CodeSearchTool(config)

    if output:
        output_dir = Path(output)
    else:
        output_dir = package_dir.parents[1] / "locbench" / "outputs" / "code_search"
    output_dir.mkdir(parents=True, exist_ok=True)
    add_file_handler(output_dir / "minisweagent.log")

    if loc_output:
        loc_output_path = Path(loc_output)
    else:
        loc_output_path = default_loc_output(tool.config.embedding_model, tool.config.embedding_provider)
    logger.info(f"Loc outputs will be saved to {loc_output_path}")

    records = load_jsonl(dataset_path)
    bench_data = {item.get("instance_id"): item for item in records if item.get("instance_id")}
    instances, missing_repos = build_instances(records, repo_root, skip_missing=skip_missing)
    if missing_repos and not skip_missing:
        missing_preview = ", ".join(missing_repos[:10])
        raise typer.BadParameter(
            f"Missing {len(missing_repos)} repos (first 10: {missing_preview}). "
            "Use --skip-missing to ignore."
        )

    instances = filter_instances(
        instances, filter_spec=filter_spec, slice_spec=slice_spec, shuffle=shuffle, shuffle_seed=shuffle_seed
    )
    if not redo_existing:
        existing_ids = load_existing_instance_ids(loc_output_path)
        if existing_ids:
            logger.info(f"Skipping {len(existing_ids)} existing instances from {loc_output_path}")
            instances = [instance for instance in instances if instance["instance_id"] not in existing_ids]
    logger.info(f"Running on {len(instances)} instances...")

    worktree_root = package_dir.parents[1] / "locbench" / "tool_worktrees"
    progress_manager = RunBatchProgressManager(len(instances), output_dir / f"exit_statuses_{time.time()}.yaml")

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
                worktree_path, resolved_commit = ensure_worktree(repo_path, repo_dir, base_commit, worktree_root)

                if force_rebuild:
                    index_dir = get_index_dir(config, repo_dir, resolved_commit)
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
                    mapper_root = prepare_mapper_root(worktree_root, instance_id, repo_dir, worktree_path)
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
                }
                append_loc_output(loc_output_path, output_record)
            except Exception as exc:
                exit_status = type(exc).__name__
                logger.error(f"Error processing instance {instance_id}: {exc}", exc_info=True)
                output_record = {
                    "instance_id": instance_id,
                    "found_files": [],
                    "found_modules": [],
                    "found_entities": [],
                    "raw_output_loc": [],
                    "error": str(exc),
                    "meta_data": build_meta(bench_data.get(instance_id)),
                }
                append_loc_output(loc_output_path, output_record)
            finally:
                progress_manager.on_instance_end(instance_id, exit_status)


if __name__ == "__main__":
    app()
