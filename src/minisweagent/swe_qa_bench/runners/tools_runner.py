#!/usr/bin/env python3

"""Run mini-SWE-agent on SWE-QA-Bench with bash + code_search tools."""

from __future__ import annotations

import concurrent.futures
import copy
import random
import threading
import time
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined, Template
from rich.live import Live

from minisweagent import package_dir
from minisweagent.agents.tool_agent import ToolAgent
from minisweagent.config import get_config_path
from minisweagent.environments import get_environment
from minisweagent.models import get_model
from minisweagent.run.extra.utils.batch_progress import RunBatchProgressManager
from minisweagent.run.utils.save import save_traj
from minisweagent.swe_qa_bench.utils import (
    FileReadTracker,
    TrackingToolRegistry,
    append_jsonl,
    build_answer_stats,
    extract_json_payload,
    load_jsonl,
    merge_relative_code_list,
    validate_output_model_name,
)
from minisweagent.tools.code_search import CodeSearchTool
from minisweagent.utils.log import add_file_handler, logger

_OUTPUT_FILE_LOCK = threading.Lock()


class ProgressTrackingToolAgent(ToolAgent):
    def __init__(self, *args, progress_manager: RunBatchProgressManager, instance_id: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.progress_manager = progress_manager
        self.instance_id = instance_id

    def step(self) -> dict:
        tokens = getattr(self.model, "total_tokens", 0)
        self.progress_manager.update_instance_status(
            self.instance_id, f"Step {self.model.n_calls + 1:3d} ({tokens} toks)"
        )
        return super().step()

    def execute_bash(self, action: dict) -> dict:
        output = super().execute_bash(action)
        tracker: FileReadTracker | None = getattr(self, "_file_tracker", None)
        if tracker is not None:
            tracker.ingest(action.get("command", ""), output.get("output", ""))
        return output


class ToolsRunner:
    def __init__(
        self,
        *,
        dataset_root: Path,
        repos_root: Path,
        output_root: Path,
        repos: list[str],
        slice_spec: str,
        shuffle: bool,
        shuffle_seed: int,
        workers: int,
        config_path: Path,
        tool_config_path: Path,
        model: str | None,
        model_class: str | None,
        environment_class: str | None,
        image: str | None,
        output_model_name: str,
        method: str,
        output_dir: str,
        redo_existing: bool,
        indexes_root: str | None,
        model_root: str | None,
        pricing: dict[str, Any] | None,
        billing: dict[str, Any] | None,
    ) -> None:
        self.dataset_root = dataset_root
        self.repos_root = repos_root
        self.output_root = output_root
        self.repos = repos
        self.slice_spec = slice_spec
        self.shuffle = shuffle
        self.shuffle_seed = shuffle_seed
        self.workers = workers
        self.config_path = config_path
        self.tool_config_path = tool_config_path
        self.model = model
        self.model_class = model_class
        self.environment_class = environment_class
        self.image = image
        self.output_model_name = output_model_name
        self.method = method
        self.output_dir = output_dir
        self.redo_existing = redo_existing
        self.indexes_root = indexes_root
        self.model_root = model_root
        self.pricing = pricing
        self.billing = billing

    def run(self) -> None:
        run_tools(
            dataset_root=self.dataset_root,
            repos_root=self.repos_root,
            output_root=self.output_root,
            repos=",".join(self.repos),
            slice_spec=self.slice_spec,
            shuffle=self.shuffle,
            shuffle_seed=self.shuffle_seed,
            workers=self.workers,
            config_path=self.config_path,
            tool_config_path=self.tool_config_path,
            model=self.model,
            model_class=self.model_class,
            environment_class=self.environment_class,
            image=self.image,
            output_model_name=self.output_model_name,
            method=self.method,
            output_dir=self.output_dir,
            redo_existing=self.redo_existing,
            indexes_root=self.indexes_root,
            model_root=self.model_root,
            pricing=self.pricing,
            billing=self.billing,
        )


def _collect_repos(questions_dir: Path, repos_csv: str) -> list[str]:
    if repos_csv:
        return [item.strip() for item in repos_csv.split(",") if item.strip()]
    return sorted(path.stem for path in questions_dir.glob("*.jsonl"))


def _load_existing_questions(path: Path) -> set[str]:
    if not path.exists():
        return set()
    questions = set()
    for record in load_jsonl(path):
        question = record.get("question")
        if question:
            questions.add(question)
    return questions


def _build_instances(
    questions_dir: Path,
    repos_root: Path,
    repos: list[str],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    missing_question_files: list[str] = []
    missing_repos: list[str] = []
    instances: list[dict[str, Any]] = []
    for repo in repos:
        question_path = questions_dir / f"{repo}.jsonl"
        repo_path = repos_root / repo
        if not question_path.exists():
            missing_question_files.append(repo)
            continue
        if not repo_path.exists():
            missing_repos.append(repo)
            continue
        records = load_jsonl(question_path)
        for idx, record in enumerate(records):
            question = (record.get("question") or "").strip()
            if not question:
                continue
            instance_id = f"{repo}-{idx}"
            instance = {
                "instance_id": instance_id,
                "repo": repo,
                "repo_dir": repo,
                "repo_path": str(repo_path.resolve()),
                "repo_mount_path": f"/repos/{repo}",
                "workdir": f"/repos/{repo}",
                "question": question,
                "question_index": idx,
                "base_commit": "HEAD",
            }
            instances.append(instance)
    return instances, missing_question_files, missing_repos


def _filter_instances(
    instances: list[dict[str, Any]],
    *,
    slice_spec: str,
    shuffle: bool,
    shuffle_seed: int,
) -> list[dict[str, Any]]:
    if shuffle:
        instances = sorted(instances.copy(), key=lambda x: x["instance_id"])
        random.seed(shuffle_seed)
        random.shuffle(instances)
    if slice_spec:
        values = [int(x) if x else None for x in slice_spec.split(":")]
        instances = instances[slice(*values)]
    return instances


def _default_output_dir(output_model_name: str, method: str) -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    root_dir = package_dir.parents[1] / "swe_qa_bench"
    return root_dir / "outputs" / output_model_name / method / timestamp


def _get_answer_path(output_root: Path, output_model_name: str, method: str, repo: str) -> Path:
    return output_root / "answers" / output_model_name / method / f"{repo}.jsonl"


def _get_environment(config: dict[str, Any], instance: dict[str, Any], repos_root: Path):
    env_config = copy.deepcopy(config.get("environment", {}))
    env_config["environment_class"] = env_config.get("environment_class", "docker")
    if env_config["environment_class"] != "docker":
        raise ValueError("SWE-QA-Bench runner supports docker only.")

    image = env_config.get("image")
    if image is None:
        raise ValueError("Docker image must be set for SWE-QA-Bench.")
    env_config["image"] = image

    run_args = list(env_config.get("run_args", ["--rm"]))
    if "--rm" not in run_args:
        run_args.insert(0, "--rm")

    mount_arg = f"{repos_root}:/repos:ro"
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


def _cleanup_environment(env: Any) -> None:
    if env is None:
        return
    if hasattr(env, "stop"):
        env.stop()
    elif hasattr(env, "cleanup"):
        env.cleanup()


def _parse_answer(result: str) -> str:
    payload, _ = extract_json_payload(result)
    if payload and isinstance(payload.get("answer"), str):
        return payload.get("answer", "").strip()
    return result.strip()


def _append_answer_record(path: Path, record: dict[str, Any]) -> None:
    with _OUTPUT_FILE_LOCK:
        append_jsonl(path, record)


def process_instance(
    instance: dict[str, Any],
    output_dir: Path,
    config: dict[str, Any],
    tool: CodeSearchTool,
    progress_manager: RunBatchProgressManager,
    dataset_root: Path,
    output_root: Path,
    output_model_name: str,
    method: str,
    repos_root: Path,
) -> None:
    instance_id = instance["instance_id"]
    instance_dir = output_dir / instance_id
    instance_dir.mkdir(parents=True, exist_ok=True)
    (instance_dir / f"{instance_id}.traj.json").unlink(missing_ok=True)

    model = get_model(config=config.get("model", {}))
    question = instance["question"]
    answer_path = _get_answer_path(output_root, output_model_name, method, instance["repo"])

    progress_manager.on_instance_start(instance_id)
    progress_manager.update_instance_status(instance_id, "Starting container")

    agent = None
    env = None
    exit_status = "Unknown"
    result = ""

    tracker = FileReadTracker(
        repo_path=Path(instance["repo_path"]),
        repo_mount_path=instance["repo_mount_path"],
        workdir=instance["workdir"],
    )

    tool_registry = TrackingToolRegistry(
        repo_path=Path(instance["repo_path"]),
        repo_mount_path=instance["repo_mount_path"],
        workdir=instance["workdir"],
    )
    tool_registry.register(tool)

    try:
        env = _get_environment(config, instance, repos_root)
        agent = ProgressTrackingToolAgent(
            model=model,
            env=env,
            tool_registry=tool_registry,
            progress_manager=progress_manager,
            instance_id=instance_id,
            **config.get("agent", {}),
        )
        agent._file_tracker = tracker
        exit_status, result = agent.run(
            task=question,
            repo=instance["repo"],
            repo_dir=instance["repo_dir"],
            repo_path=instance["repo_path"],
            base_commit=instance.get("base_commit", "HEAD"),
            workdir=instance["workdir"],
            repo_mount_path=instance["repo_mount_path"],
        )
    except Exception as exc:
        exit_status = f"{type(exc).__name__}"
        result = str(exc)
        logger.error(f"Error processing {instance_id}: {exc}", exc_info=True)
    finally:
        _cleanup_environment(env)

    answer = _parse_answer(result) if exit_status == "Submitted" else ""
    relative_code_list = merge_relative_code_list(tool_registry.tool_candidates, tracker.paths)
    stats = build_answer_stats(model)

    record = {
        "question": question,
        "answer": answer,
        "final_answer": answer,
        "relative_code_list": relative_code_list,
        "stats": stats,
    }
    _append_answer_record(answer_path, record)
    logger.info("Answer appended to: %s", answer_path)

    extra_info = {
        "repo": instance["repo"],
        "question": question,
        "relative_code_list": relative_code_list,
        "tool_candidates": tool_registry.tool_candidates,
    }
    save_traj(
        agent,
        instance_dir / f"{instance_id}.traj.json",
        print_fct=logger.info,
        exit_status=exit_status,
        result=result,
        extra_info=extra_info,
    )

    progress_manager.on_instance_end(instance_id, exit_status)


def run_tools(
    dataset_root: Path,
    repos_root: Path,
    output_root: Path,
    repos: str,
    slice_spec: str,
    shuffle: bool,
    shuffle_seed: int,
    workers: int,
    config_path: Path,
    tool_config_path: Path,
    model: str | None,
    model_class: str | None,
    environment_class: str | None,
    image: str | None,
    output_model_name: str,
    method: str,
    output_dir: str,
    redo_existing: bool,
    indexes_root: str | None,
    model_root: str | None,
    pricing: dict[str, Any] | None,
    billing: dict[str, Any] | None,
) -> None:
    dataset_root = dataset_root.resolve()
    repos_root = repos_root.resolve()
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    if not dataset_root.exists():
        raise ValueError(f"Dataset root not found: {dataset_root}")
    if not repos_root.exists():
        raise ValueError(f"Repos root not found: {repos_root}")
    validate_output_model_name(output_model_name)

    config_path = get_config_path(config_path)
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
    if billing is not None:
        config.setdefault("model", {})["billing"] = billing

    tool_config_path = get_config_path(tool_config_path)
    tool_config = yaml.safe_load(tool_config_path.read_text())
    if indexes_root:
        tool_config["index_root"] = str(indexes_root)
    if model_root:
        tool_config["embedding_model"] = str(model_root)
    tool = CodeSearchTool(tool_config)

    default_output_dir = _default_output_dir(output_model_name, method)
    if output_dir:
        output_dir_path = Path(output_dir)
    else:
        output_dir_path = default_output_dir
    output_dir_path.mkdir(parents=True, exist_ok=True)
    add_file_handler(output_dir_path / "minisweagent.log")
    logger.info(f"Results will be saved to {output_dir_path}")

    questions_dir = dataset_root / "questions"
    repo_list = _collect_repos(questions_dir, repos)
    instances, missing_questions, missing_repos = _build_instances(questions_dir, repos_root, repo_list)
    if missing_questions:
        missing_preview = ", ".join(missing_questions[:10])
        raise ValueError(f"Missing question files (first 10): {missing_preview}")
    if missing_repos:
        missing_preview = ", ".join(missing_repos[:10])
        raise ValueError(f"Missing repos (first 10): {missing_preview}")

    if not redo_existing:
        existing_by_repo = {
            repo: _load_existing_questions(_get_answer_path(output_root, output_model_name, method, repo))
            for repo in repo_list
        }
        instances = [
            inst for inst in instances if inst["question"] not in existing_by_repo.get(inst["repo"], set())
        ]

    instances = _filter_instances(
        instances,
        slice_spec=slice_spec,
        shuffle=shuffle,
        shuffle_seed=shuffle_seed,
    )
    logger.info(f"Running on {len(instances)} instances...")

    progress_manager = RunBatchProgressManager(len(instances), output_dir_path / f"exit_statuses_{time.time()}.yaml")

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
                    output_dir_path,
                    config,
                    tool,
                    progress_manager,
                    dataset_root,
                    output_root,
                    output_model_name,
                    method,
                    repos_root,
                ): instance["instance_id"]
                for instance in instances
            }
            process_futures(futures)

    progress_manager.print_report()
