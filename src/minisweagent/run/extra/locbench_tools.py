#!/usr/bin/env python3

"""Run mini-SWE-agent on LocBench instances with tool support."""

from __future__ import annotations

import concurrent.futures
import copy
import json
import random
import re
import shlex
import shutil
import subprocess
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import typer
import yaml
from jinja2 import StrictUndefined, Template
from rich.live import Live

from minisweagent import package_dir
from minisweagent.agents.tool_agent import ToolAgent
from minisweagent.config import builtin_config_dir, get_config_path
from minisweagent.environments import get_environment
from minisweagent.models import get_model, get_model_name
from minisweagent.run.extra.utils.batch_progress import RunBatchProgressManager
from minisweagent.run.utils.save import save_traj
from minisweagent.tools.code_search import CodeSearchTool
from minisweagent.tools.registry import ToolRegistry
from minisweagent.utils.log import add_file_handler, logger

_HELP_TEXT = "Run mini-SWE-agent on LocBench instances with tool support."

app = typer.Typer(rich_markup_mode="rich", add_completion=False)

JSON_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)

_OUTPUT_FILE_LOCK = threading.Lock()
_WORKTREE_LOCK = threading.Lock()


class ProgressTrackingAgent(ToolAgent):
    """Wrapper around ToolAgent that provides progress updates."""

    def __init__(self, *args, progress_manager: RunBatchProgressManager, instance_id: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.progress_manager: RunBatchProgressManager = progress_manager
        self.instance_id = instance_id

    def step(self) -> dict:
        self.progress_manager.update_instance_status(
            self.instance_id, f"Step {self.model.n_calls + 1:3d} (${self.model.cost:.2f})"
        )
        return super().step()


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


def build_repo_dir_name(repo_slug: str) -> str:
    return repo_slug.replace("/", "_")


def build_repo_path(repo_root: Path, repo_slug: str) -> Path:
    return repo_root / build_repo_dir_name(repo_slug)


def normalize_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = [value]
    result: list[str] = []
    seen = set()
    for item in items:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def extract_json_payload(text: str) -> tuple[dict[str, Any] | None, str | None]:
    if not text:
        return None, None

    candidates: list[str] = []
    for match in JSON_CODE_BLOCK_RE.finditer(text):
        candidate = match.group(1).strip()
        if candidate:
            candidates.append(candidate)

    if not candidates:
        candidates.append(text.strip())

    for candidate in candidates:
        payload = _try_load_json(candidate)
        if payload is not None:
            return payload, candidate

    for candidate in _iter_json_substrings(text):
        payload = _try_load_json(candidate)
        if payload is not None:
            return payload, candidate

    return None, None


def _try_load_json(candidate: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _iter_json_substrings(text: str):
    starts = [i for i, ch in enumerate(text) if ch == "{"]
    for start in starts:
        depth = 0
        for idx in range(start, len(text)):
            char = text[idx]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    yield text[start : idx + 1]
                    break


def entities_to_modules(found_entities: list[str]) -> list[str]:
    modules: list[str] = []
    seen = set()
    for entity in found_entities:
        if ":" not in entity:
            continue
        file_path, name = entity.split(":", 1)
        module_name = name.split(".")[0]
        module_id = f"{file_path}:{module_name}" if module_name else file_path
        if module_id in seen:
            continue
        seen.add(module_id)
        modules.append(module_id)
    return modules


def build_meta(record: dict[str, Any] | None) -> dict[str, Any]:
    if not record:
        return {}
    meta = {}
    for key in ("repo", "base_commit", "problem_statement", "patch", "test_patch"):
        if key in record:
            meta[key] = record[key]
    return meta


def sanitize_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


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
        random.seed(shuffle_seed)
        random.shuffle(instances)
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


def build_loc_output(result: str, instance_id: str, record: dict[str, Any] | None) -> dict[str, Any]:
    payload, raw_response = extract_json_payload(result)
    if payload is None:
        payload = {}
        raw_response = result.strip() if result else ""

    found_files = normalize_list(payload.get("found_files") or payload.get("files"))
    found_entities = normalize_list(payload.get("found_entities") or payload.get("entities"))
    found_modules = normalize_list(payload.get("found_modules") or payload.get("modules"))

    if not found_files and found_entities:
        found_files = normalize_list([item.split(":", 1)[0] for item in found_entities if ":" in item])
    if not found_modules and found_entities:
        found_modules = entities_to_modules(found_entities)

    return {
        "instance_id": instance_id,
        "found_files": found_files,
        "found_modules": found_modules,
        "found_entities": found_entities,
        "raw_output_loc": [raw_response] if raw_response else [],
        "meta_data": build_meta(record),
    }


def get_locbench_environment(config: dict[str, Any], instance: dict[str, Any], repo_root: Path) -> Any:
    env_config = copy.deepcopy(config.get("environment", {}))
    env_config["environment_class"] = env_config.get("environment_class", "docker")
    if env_config["environment_class"] != "docker":
        raise ValueError("LocBench runner currently supports the docker environment only.")

    image = env_config.get("image")
    if image is None:
        raise ValueError("Docker image must be set for locbench.")
    env_config["image"] = image

    run_args = list(env_config.get("run_args", ["--rm"]))
    if "--rm" not in run_args:
        run_args.insert(0, "--rm")

    mount_arg = f"{repo_root}:/repos:ro"
    if mount_arg not in run_args:
        run_args.extend(["-v", mount_arg])
    env_config["run_args"] = run_args

    env = get_environment(env_config)
    if startup_command := config.get("run", {}).get("env_startup_command"):
        startup_command = Template(startup_command, undefined=StrictUndefined).render(**instance)
        out = env.execute(startup_command)
        if out["returncode"] != 0:
            raise RuntimeError(f"Error executing startup command: {out}")
    if instance.get("workdir"):
        env.config.cwd = instance["workdir"]
    return env


def cleanup_environment(env: Any) -> None:
    if env is None:
        return
    if hasattr(env, "stop"):
        env.stop()
        return
    if hasattr(env, "cleanup"):
        env.cleanup()


def process_instance(
    instance: dict[str, Any],
    output_dir: Path,
    loc_output_path: Path,
    config: dict[str, Any],
    tool_registry: ToolRegistry,
    progress_manager: RunBatchProgressManager,
    bench_data: dict[str, Any],
    repo_root: Path,
    worktree_root: Path,
) -> None:
    instance_id = instance["instance_id"]
    instance_dir = output_dir / instance_id
    instance_dir.mkdir(parents=True, exist_ok=True)
    (instance_dir / f"{instance_id}.traj.json").unlink(missing_ok=True)
    model = get_model(config=config.get("model", {}))
    task = instance.get("problem_statement", "")

    progress_manager.on_instance_start(instance_id)
    progress_manager.update_instance_status(instance_id, "Preparing worktree")

    agent = None
    extra_info = None
    env = None
    exit_status = "Unknown"
    result = ""

    try:
        repo_path = Path(instance["repo_path"])
        repo_dir = instance["repo_dir"]
        worktree_path, resolved_commit = ensure_worktree(
            repo_path, repo_dir, instance["base_commit"], worktree_root
        )
        instance = instance | {
            "repo_path": str(worktree_path),
            "base_commit": resolved_commit,
            "base_commit_q": shlex.quote(resolved_commit),
        }

        env = get_locbench_environment(config, instance, repo_root)
        progress_manager.update_instance_status(instance_id, "Running agent")
        agent = ProgressTrackingAgent(
            model,
            env,
            tool_registry=tool_registry,
            progress_manager=progress_manager,
            instance_id=instance_id,
            **config.get("agent", {}),
        )
        exit_status, result = agent.run(task, **instance)
    except Exception as exc:
        logger.error(f"Error processing instance {instance_id}: {exc}", exc_info=True)
        exit_status, result = type(exc).__name__, str(exc)
        extra_info = {"traceback": traceback.format_exc()}
    finally:
        cleanup_environment(env)
        save_traj(
            agent,
            instance_dir / f"{instance_id}.traj.json",
            exit_status=exit_status,
            result=result,
            extra_info=extra_info,
            instance_id=instance_id,
            print_fct=logger.info,
        )
        output_record = build_loc_output(result, instance_id, bench_data.get(instance_id))
        append_loc_output(loc_output_path, output_record)
        progress_manager.on_instance_end(instance_id, exit_status)


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
        repo_path = build_repo_path(repo_root, repo_slug).resolve()
        if not repo_path.exists():
            if skip_missing:
                continue
            missing_repos.append(repo_slug)
            continue

        base_commit = record.get("base_commit") or "HEAD"
        repo_mount_path = f"/repos/{repo_dir}"
        workdir = f"/work/{instance_id}"

        instance = {
            "instance_id": instance_id,
            "repo_slug": repo_slug,
            "repo_dir": repo_dir,
            "repo_path": str(repo_path),
            "repo_mount_path": repo_mount_path,
            "repo_mount_path_q": shlex.quote(repo_mount_path),
            "workdir": workdir,
            "workdir_q": shlex.quote(workdir),
            "base_commit": base_commit,
            "base_commit_q": shlex.quote(base_commit),
            "problem_statement": record.get("problem_statement") or "",
        }
        instances.append(instance)
    return instances, missing_repos


def default_paths(model_name: str) -> tuple[Path, Path]:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    root_dir = package_dir.parents[1] / "locbench"
    model_dir = sanitize_component(model_name)
    output_dir = root_dir / "outputs" / model_dir / timestamp
    loc_output = root_dir / "loc_output" / model_dir / f"loc_outputs_{timestamp}.jsonl"
    return output_dir, loc_output


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
    output: str = typer.Option("", "-o", "--output", help="Output directory for trajectories/logs", rich_help_panel="Basic"),
    loc_output: str = typer.Option("", "--loc-output", help="Output loc_outputs.jsonl path", rich_help_panel="Basic"),
    workers: int = typer.Option(1, "-w", "--workers", help="Number of worker threads", rich_help_panel="Basic"),
    model: str | None = typer.Option(None, "-m", "--model", help="Model to use", rich_help_panel="Basic"),
    model_class: str | None = typer.Option(None, "--model-class", help="Model class to use (e.g., 'anthropic')", rich_help_panel="Advanced"),
    config_spec: Path = typer.Option(builtin_config_dir / "extra" / "locbench_tools.yaml", "-c", "--config", help="Path to a config file", rich_help_panel="Basic"),
    tool_config_spec: Path = typer.Option(builtin_config_dir / "extra" / "code_search.yaml", "--tool-config", help="Path to code_search config", rich_help_panel="Basic"),
    environment_class: str | None = typer.Option(None, "--environment-class", help="Environment type to use", rich_help_panel="Advanced"),
    image: str | None = typer.Option(None, "--image", help="Docker image to use", rich_help_panel="Advanced"),
    redo_existing: bool = typer.Option(False, "--redo-existing", help="Redo existing instances", rich_help_panel="Data selection"),
) -> None:
    # fmt: on
    dataset_path = Path(dataset)
    repo_root = Path(repos_root).resolve()
    if not dataset_path.exists():
        raise typer.BadParameter(f"Dataset not found: {dataset_path}")
    if not repo_root.exists():
        raise typer.BadParameter(f"Repo root not found: {repo_root}")

    config_path = get_config_path(config_spec)
    logger.info(f"Loading agent config from '{config_path}'")
    config = yaml.safe_load(config_path.read_text())
    if environment_class is not None:
        config.setdefault("environment", {})["environment_class"] = environment_class
    if image is not None:
        config.setdefault("environment", {})["image"] = image
    if model is not None:
        config.setdefault("model", {})["model_name"] = model
    if model_class is not None:
        config.setdefault("model", {})["model_class"] = model_class

    tool_config_path = get_config_path(tool_config_spec)
    tool_config = yaml.safe_load(tool_config_path.read_text())
    tool = CodeSearchTool(tool_config)
    tool_registry = ToolRegistry()
    tool_registry.register(tool)

    model_name = get_model_name(model, config.get("model", {}))
    default_output_dir, default_loc_output = default_paths(model_name)
    if output:
        output_dir = Path(output)
    else:
        output_dir = default_output_dir
    if loc_output:
        loc_output_path = Path(loc_output)
    else:
        loc_output_path = default_loc_output

    output_dir.mkdir(parents=True, exist_ok=True)
    add_file_handler(output_dir / "minisweagent.log")
    logger.info(f"Results will be saved to {output_dir}")
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

    progress_manager = RunBatchProgressManager(len(instances), output_dir / f"exit_statuses_{time.time()}.yaml")
    worktree_root = package_dir.parents[1] / "locbench" / "tool_worktrees"

    def process_futures(futures: dict[concurrent.futures.Future, str]):
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except concurrent.futures.CancelledError:
                pass
            except Exception as exc:
                instance_id = futures[future]
                logger.error(f"Error in future for instance {instance_id}: {exc}", exc_info=True)
                progress_manager.on_uncaught_exception(instance_id, exc)

    with Live(progress_manager.render_group, refresh_per_second=4):
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    process_instance,
                    instance,
                    output_dir,
                    loc_output_path,
                    config,
                    tool_registry,
                    progress_manager,
                    bench_data,
                    repo_root,
                    worktree_root,
                ): instance["instance_id"]
                for instance in instances
            }
            try:
                process_futures(futures)
            except KeyboardInterrupt:
                logger.info("Cancelling all pending jobs. Press ^C again to exit immediately.")
                for future in futures:
                    if not future.running() and not future.done():
                        future.cancel()
                process_futures(futures)


if __name__ == "__main__":
    app()
