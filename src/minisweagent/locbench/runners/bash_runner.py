#!/usr/bin/env python3

"""Run mini-SWE-agent on LocBench instances (bash-only)."""

from __future__ import annotations

import concurrent.futures
import copy
import shlex
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined, Template
from rich.live import Live

from minisweagent.agents.default import DefaultAgent
from minisweagent.config import get_config_path
from minisweagent.environments import get_environment
from minisweagent.models import get_model
from minisweagent.run.extra.utils.batch_progress import RunBatchProgressManager
from minisweagent.run.utils.save import save_traj
from minisweagent.locbench.config_loader import project_root
from minisweagent.locbench.utils import (
    build_answer_stats,
    build_loc_output,
    build_repo_dir_name,
    build_repo_path,
    filter_instances,
    load_existing_instance_ids,
    load_jsonl,
    prepare_local_instances,
    sanitize_component,
    validate_output_model_name,
    append_jsonl,
)
from minisweagent.utils.log import add_file_handler, logger

_OUTPUT_FILE_LOCK = threading.Lock()


class ProgressTrackingAgent(DefaultAgent):
    def __init__(self, *args, progress_manager: RunBatchProgressManager, instance_id: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.progress_manager = progress_manager
        self.instance_id = instance_id

    def step(self) -> dict:
        self.progress_manager.update_instance_status(
            self.instance_id, f"Step {self.model.n_calls + 1:3d} (${self.model.cost:.2f})"
        )
        return super().step()


class BashRunner:
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
        workers: int,
        config_path: Path,
        model: str | None,
        model_class: str | None,
        environment_class: str | None,
        image: str | None,
        output_model_name: str,
        method: str,
        output_dir: str,
        redo_existing: bool,
        pricing: dict[str, Any] | None,
        billing: dict[str, Any] | None,
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
        self.workers = workers
        self.config_path = config_path
        self.model = model
        self.model_class = model_class
        self.environment_class = environment_class
        self.image = image
        self.output_model_name = output_model_name
        self.method = method
        self.output_dir = output_dir
        self.redo_existing = redo_existing
        self.pricing = pricing
        self.billing = billing

    def run(self) -> None:
        run_bash(
            dataset_path=self.dataset_path,
            repos_root=self.repos_root,
            output_root=self.output_root,
            worktrees_root=self.worktrees_root,
            slice_spec=self.slice_spec,
            filter_spec=self.filter_spec,
            shuffle=self.shuffle,
            shuffle_seed=self.shuffle_seed,
            skip_missing=self.skip_missing,
            workers=self.workers,
            config_path=self.config_path,
            model=self.model,
            model_class=self.model_class,
            environment_class=self.environment_class,
            image=self.image,
            output_model_name=self.output_model_name,
            method=self.method,
            output_dir=self.output_dir,
            redo_existing=self.redo_existing,
            pricing=self.pricing,
            billing=self.billing,
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


def _get_locbench_environment(config: dict[str, Any], instance: dict[str, Any], repo_root: Path) -> Any:
    env_config = copy.deepcopy(config.get("environment", {}))
    env_class = env_config.get("environment_class", "docker")
    env_config["environment_class"] = env_class

    if env_class == "docker":
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
    elif env_class != "local":
        raise ValueError(f"LocBench runner only supports docker or local (got: {env_class}).")

    env = get_environment(env_config)
    if startup_command := config.get("run", {}).get("env_startup_command"):
        startup_command = Template(startup_command, undefined=StrictUndefined).render(**instance)
        out = env.execute(startup_command)
        if out["returncode"] != 0:
            raise RuntimeError(f"Error executing startup command: {out}")
    if instance.get("workdir"):
        env.config.cwd = instance["workdir"]
    return env


def _cleanup_environment(env: Any) -> None:
    if env is None:
        return
    if hasattr(env, "stop"):
        env.stop()
        return
    if hasattr(env, "cleanup"):
        env.cleanup()


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
        repo_path = build_repo_path(repo_root, repo_slug).resolve()
        if not repo_path.exists():
            if skip_missing:
                continue
            missing_repos.append(repo_slug)
            continue

        base_commit = record.get("base_commit") or "HEAD"
        repo_dir = build_repo_dir_name(repo_slug)
        repo_mount_path = f"/repos/{repo_dir}"
        workdir = f"/work/{instance_id}"

        instance = {
            "instance_id": instance_id,
            "repo_slug": repo_slug,
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


def _process_instance(
    instance: dict[str, Any],
    output_dir: Path,
    loc_output_path: Path,
    config: dict[str, Any],
    progress_manager: RunBatchProgressManager,
    bench_data: dict[str, Any],
    repo_root: Path,
) -> None:
    instance_id = instance["instance_id"]
    instance_dir = output_dir / instance_id
    instance_dir.mkdir(parents=True, exist_ok=True)
    (instance_dir / f"{instance_id}.traj.json").unlink(missing_ok=True)

    model = get_model(config=config.get("model", {}))
    task = instance.get("problem_statement", "")

    progress_manager.on_instance_start(instance_id)

    agent = None
    extra_info = None
    env = None
    exit_status = "Unknown"
    result = ""
    stats: dict[str, Any] | None = None

    try:
        env = _get_locbench_environment(config, instance, repo_root)
        progress_manager.update_instance_status(instance_id, "Running agent")
        agent = ProgressTrackingAgent(
            model,
            env,
            progress_manager=progress_manager,
            instance_id=instance_id,
            **config.get("agent", {}),
        )
        exit_status, result = agent.run(task, **instance)
        stats = build_answer_stats(model)
    except Exception as exc:
        logger.error("Error processing instance %s: %s", instance_id, exc, exc_info=True)
        exit_status, result = type(exc).__name__, str(exc)
        extra_info = {"traceback": traceback.format_exc()}
        stats = build_answer_stats(model) if model else None
    finally:
        _cleanup_environment(env)
        save_traj(
            agent,
            instance_dir / f"{instance_id}.traj.json",
            exit_status=exit_status,
            result=result,
            extra_info=extra_info,
            instance_id=instance_id,
            print_fct=logger.info,
        )
        output_record = build_loc_output(result, instance_id, bench_data.get(instance_id), stats=stats)
        _append_loc_output(loc_output_path, output_record)
        progress_manager.on_instance_end(instance_id, exit_status)


def run_bash(
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
    workers: int,
    config_path: Path,
    model: str | None,
    model_class: str | None,
    environment_class: str | None,
    image: str | None,
    output_model_name: str,
    method: str,
    output_dir: str,
    redo_existing: bool,
    pricing: dict[str, Any] | None,
    billing: dict[str, Any] | None,
) -> None:
    dataset_path = dataset_path.resolve()
    repos_root = repos_root.resolve()
    output_root = output_root.resolve()
    worktrees_root = worktrees_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    if not dataset_path.exists():
        raise ValueError(f"Dataset not found: {dataset_path}")
    if not repos_root.exists():
        raise ValueError(f"Repo root not found: {repos_root}")
    validate_output_model_name(output_model_name)

    config_path = get_config_path(config_path)
    logger.info("Loading agent config from '%s'", config_path)
    config = yaml.safe_load(config_path.read_text())
    if environment_class is not None:
        config.setdefault("environment", {})["environment_class"] = environment_class
    if image is not None:
        config.setdefault("environment", {})["image"] = image
    if model is not None:
        config.setdefault("model", {})["model_name"] = model
    if model_class is not None:
        config.setdefault("model", {})["model_class"] = model_class
    if pricing is not None:
        config.setdefault("model", {})["pricing"] = pricing
    if billing is not None:
        config.setdefault("model", {})["billing"] = billing

    env_class = config.get("environment", {}).get("environment_class", "docker")
    if env_class == "local":
        config.setdefault("run", {})["env_startup_command"] = (
            "mkdir -p {{ workdir_parent_q }} && "
            "rm -rf {{ workdir_q }} && "
            "git -c safe.directory=* clone --no-hardlinks {{ repo_mount_path_q }} {{ workdir_q }} && "
            "cd {{ workdir_q }} && "
            "git checkout -q {{ base_commit_q }}"
        )

    default_output_dir = _default_output_dir(output_model_name, method)
    output_dir_path = Path(output_dir) if output_dir else default_output_dir
    output_dir_path.mkdir(parents=True, exist_ok=True)
    add_file_handler(output_dir_path / "minisweagent.log")
    logger.info("Results will be saved to %s", output_dir_path)

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

    if env_class == "local":
        local_root = worktrees_root / "bash"
        prepare_local_instances(instances, local_root)

    logger.info("Running on %s instances...", len(instances))

    progress_manager = RunBatchProgressManager(len(instances), output_dir_path / f"exit_statuses_{time.time()}.yaml")

    def process_futures(futures: dict[concurrent.futures.Future, str]):
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except concurrent.futures.CancelledError:
                pass
            except Exception as exc:
                instance_id = futures[future]
                logger.error("Error in future for instance %s: %s", instance_id, exc, exc_info=True)
                progress_manager.on_uncaught_exception(instance_id, exc)

    with Live(progress_manager.render_group, refresh_per_second=4):
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _process_instance,
                    instance,
                    output_dir_path,
                    loc_output_path,
                    config,
                    progress_manager,
                    bench_data,
                    repos_root,
                ): instance["instance_id"]
                for instance in instances
            }
            process_futures(futures)

    progress_manager.print_report()
