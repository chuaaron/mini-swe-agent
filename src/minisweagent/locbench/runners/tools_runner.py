#!/usr/bin/env python3

"""Run mini-SWE-agent on LocBench instances with tool support."""

from __future__ import annotations

import concurrent.futures
import copy
import random
import shlex
import shutil
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined, Template
from rich.live import Live

from minisweagent.agents.tool_agent import FormatError, Submitted, ToolAgent, ToolExecutionError, ToolFormatError
from minisweagent.config import get_config_path
from minisweagent.environments import get_environment
from minisweagent.environments.repo_mounts import build_repo_mount_args
from minisweagent.models import get_model
from minisweagent.run.extra.utils.batch_progress import RunBatchProgressManager
from minisweagent.run.extra.utils.run_summary import write_run_summary
from minisweagent.run.utils.save import save_traj
from minisweagent.locbench.config_loader import project_root
from minisweagent.locbench.utils import (
    build_answer_stats,
    compute_locbench_metrics,
    build_loc_output,
    build_repo_dir_name,
    build_repo_path,
    build_fallback_loc_result,
    extract_json_payload,
    filter_instances,
    load_existing_instance_ids,
    load_jsonl,
    sanitize_component,
    validate_output_model_name,
    append_jsonl,
)
from minisweagent.tools.code_search import CodeSearchTool
from minisweagent.tools.file_radar_search import FileRadarSearchTool
from minisweagent.tools.registry import ToolRegistry, ToolRegistryError
from minisweagent.utils.log import add_file_handler, logger

_OUTPUT_FILE_LOCK = threading.Lock()
_WORKTREE_LOCK = threading.Lock()


def _get_last_assistant_content(agent: ToolAgent | None) -> str:
    if agent is None:
        return ""
    for message in reversed(agent.messages):
        if message.get("role") == "assistant":
            return message.get("content", "") or ""
    return ""


class ProgressTrackingAgent(ToolAgent):
    def __init__(
        self,
        *args,
        progress_manager: RunBatchProgressManager,
        instance_id: str = "",
        enforce_tool_verification: bool = False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.progress_manager = progress_manager
        self.instance_id = instance_id
        self.enforce_tool_verification = enforce_tool_verification

        self.needs_verification = False
        self.candidate_files: set[str] = set()
        self.verified_files: set[str] = set()
        self.verification_read_commands = {"rg", "grep", "sed", "cat", "nl", "head", "tail"}
        self._control_tokens = {"&&", "||", ";", "|"}

        self.radar_called_count = 0
        self.radar_tool_output_chars = 0
        self.blocked_submission_count = 0

    def _verification_interception_message(self) -> str:
        candidates = sorted(self.candidate_files)
        preview = candidates[:5]
        preview_lines = "\n".join(f"- {path}" for path in preview) if preview else "- <none recorded>"
        if len(candidates) > len(preview):
            preview_lines += f"\n- ... ({len(candidates) - len(preview)} more)"
        return (
            "SYSTEM_INTERCEPTION: Verification Required.\n"
            "You invoked file_radar_search, but have not inspected any returned candidate file with bash.\n"
            "This is not a JSON formatting error.\n"
            "Before submitting final output, run at least one successful bash read command on a candidate file.\n"
            "Allowed commands: rg, grep, sed, cat, nl, head, tail.\n"
            "Examples: `rg -n \"token\" path/to/candidate.py` or `sed -n '1,80p' path/to/candidate.py`.\n"
            "Candidate files from radar:\n"
            f"{preview_lines}"
        )

    def step(self) -> dict:
        tokens = getattr(self.model, "total_tokens", 0)
        self.progress_manager.update_instance_status(
            self.instance_id, f"Step {self.model.n_calls + 1:3d} ({tokens} toks)"
        )
        return super().step()

    def _is_read_command(self, command: str) -> bool:
        try:
            parts = shlex.split(command)
        except ValueError:
            parts = command.split()
        if not parts:
            return False
        return any(part in self.verification_read_commands for part in parts)

    def _mark_verification_from_command(self, command: str, output: dict[str, Any]) -> None:
        if not self.needs_verification or not self.candidate_files:
            return
        return_code = output.get("returncode", None)
        if return_code is None:
            return
        if int(return_code) != 0:
            return
        if not self._is_read_command(command):
            return

        try:
            tokens = shlex.split(command)
        except ValueError:
            return

        segments: list[list[str]] = []
        current: list[str] = []
        for token in tokens:
            if token in self._control_tokens:
                if current:
                    segments.append(current)
                    current = []
                continue
            current.append(token)
        if current:
            segments.append(current)

        basename_counts: dict[str, int] = {}
        for file_path in self.candidate_files:
            name = Path(file_path).name
            if name:
                basename_counts[name] = basename_counts.get(name, 0) + 1

        workdir = str(self.extra_template_vars.get("workdir") or "").rstrip("/")
        for segment in segments:
            if not segment:
                continue
            cmd_name = Path(segment[0]).name
            if cmd_name not in self.verification_read_commands:
                continue
            segment_tokens = {token.strip() for token in segment}
            for file_path in self.candidate_files:
                rel = file_path.strip()
                if not rel:
                    continue
                candidate_tokens = {rel, f"./{rel}"}
                if workdir:
                    candidate_tokens.add(f"{workdir}/{rel}")
                basename = Path(rel).name
                if basename and basename_counts.get(basename, 0) == 1:
                    candidate_tokens.add(basename)
                if candidate_tokens & segment_tokens:
                    self.verified_files.add(file_path)

        if self.verified_files:
            self.needs_verification = False

    def execute_tool(self, action: dict) -> dict:
        try:
            result = self.tool_registry.execute(action["raw"], context=self.extra_template_vars)
        except ToolRegistryError as exc:
            available = self.tool_registry.available_tools()
            raise ToolFormatError(
                self.render_template(
                    self.config.tool_format_error_template,
                    command=action["raw"],
                    available_tools=available,
                )
            ) from exc
        if not result.success:
            raise ToolExecutionError(
                self.render_template(
                    self.config.tool_error_template,
                    tool_name=action["raw"].split()[1] if action["raw"].split() else "unknown",
                    error=result.error or result.output,
                )
            )

        command = action.get("raw", "")
        if command.startswith("@tool file_radar_search"):
            self.radar_called_count += 1
            self.radar_tool_output_chars += len(result.output or "")
            candidate_files: set[str] = set()
            for item in result.data.get("results", []) if isinstance(result.data, dict) else []:
                path = item.get("path")
                if isinstance(path, str) and path.strip():
                    candidate_files.add(path.strip())
            self.candidate_files = candidate_files
            self.verified_files = set()
            self.needs_verification = self.enforce_tool_verification and bool(candidate_files)

        return {
            "type": "tool",
            "output": result.output,
            "returncode": result.returncode,
            "action": action["raw"],
            "data": result.data,
        }

    def execute_bash(self, action: dict) -> dict:
        output = super().execute_bash(action)
        self._mark_verification_from_command(action.get("command", ""), output)
        return output

    def has_finished(self, output: dict[str, str]):
        lines = output.get("output", "").lstrip().splitlines(keepends=True)
        if not lines:
            return
        marker = lines[0].strip()
        if marker not in {"MINI_SWE_AGENT_FINAL_OUTPUT", "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}:
            return
        if self.enforce_tool_verification and self.needs_verification:
            self.blocked_submission_count += 1
            raise FormatError(self._verification_interception_message())
        raise Submitted("".join(lines[1:]))


class ToolsRunner:
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
        embedding_device: str | None,
        keep_worktrees: bool,
        worktrees_mode: str,
        tools_prompt: str,
        tool_backend: str,
        enforce_tool_verification: bool,
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
        self.embedding_device = embedding_device
        self.keep_worktrees = keep_worktrees
        self.worktrees_mode = worktrees_mode
        self.tools_prompt = tools_prompt
        self.tool_backend = tool_backend
        self.enforce_tool_verification = enforce_tool_verification
        self.pricing = pricing
        self.billing = billing

    def run(self) -> None:
        run_tools(
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
            embedding_device=self.embedding_device,
            keep_worktrees=self.keep_worktrees,
            worktrees_mode=self.worktrees_mode,
            tools_prompt=self.tools_prompt,
            tool_backend=self.tool_backend,
            enforce_tool_verification=self.enforce_tool_verification,
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


def _run_git_raw(repo_path: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "-c", "safe.directory=*", "-C", str(repo_path), *args]
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def _run_git(repo_path: Path, args: list[str]) -> str:
    result = _run_git_raw(repo_path, args, check=True)
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


def _prune_worktrees(repo_path: Path) -> None:
    _run_git_raw(repo_path, ["worktree", "prune"], check=False)


def _build_worktree_path(worktree_root: Path, repo_dir: str, commit: str, mode: str) -> Path:
    if mode == "ephemeral":
        unique_id = uuid.uuid4().hex[:8]
        return worktree_root / f"{repo_dir}@{commit[:8]}_{unique_id}"
    return worktree_root / f"{repo_dir}@{commit[:8]}"


def _worktree_add_with_retry(repo_path: Path, worktree_path: Path, commit: str, *, retries: int = 5) -> None:
    delay = 0.5
    for attempt in range(retries):
        try:
            _run_git_raw(repo_path, ["worktree", "add", "--detach", str(worktree_path), commit], check=True)
            return
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr or ""
            stderr_lower = stderr.lower()
            if "already registered worktree" in stderr:
                _remove_worktree(repo_path, worktree_path)
                _prune_worktrees(repo_path)
            elif "index.lock" in stderr_lower:
                pass
            else:
                raise
            if attempt == retries - 1:
                raise
            time.sleep(delay + random.uniform(0, 0.5))
            delay *= 2


def ensure_worktree(
    repo_path: Path,
    repo_dir: str,
    base_commit: str,
    worktree_root: Path,
    *,
    worktrees_mode: str,
) -> tuple[Path, str]:
    commit = _resolve_commit(repo_path, base_commit)
    mode = worktrees_mode.strip().lower() if worktrees_mode else "reusable"
    if mode not in {"ephemeral", "reusable"}:
        raise ValueError(f"Invalid worktrees_mode: {mode}")
    worktree_path = _build_worktree_path(worktree_root, repo_dir, commit, mode)
    with _WORKTREE_LOCK:
        _prune_worktrees(repo_path)
        if mode == "reusable":
            if worktree_path.exists():
                try:
                    head = _run_git(worktree_path, ["rev-parse", "HEAD"])
                    if head == commit:
                        return worktree_path, commit
                except subprocess.SubprocessError:
                    pass
                _remove_worktree(repo_path, worktree_path)
            else:
                _remove_worktree(repo_path, worktree_path)
        else:
            _remove_worktree(repo_path, worktree_path)
        worktree_root.mkdir(parents=True, exist_ok=True)
        _worktree_add_with_retry(repo_path, worktree_path, commit)
    return worktree_path, commit


def _get_locbench_environment(config: dict[str, Any], instance: dict[str, Any], repo_root: Path) -> Any:
    env_config = copy.deepcopy(config.get("environment", {}))
    env_config["environment_class"] = env_config.get("environment_class", "docker")
    repo_mount_mode = env_config.pop("repo_mount_mode", "single")
    if env_config["environment_class"] != "docker":
        raise ValueError("LocBench tools runner supports docker only.")

    image = env_config.get("image")
    if image is None:
        raise ValueError("Docker image must be set for locbench.")
    env_config["image"] = image

    repo_source_path = Path(instance.get("repo_source_path") or instance["repo_path"])
    env_config["run_args"] = build_repo_mount_args(
        run_args=env_config.get("run_args", ["--rm"]),
        repo_mount_mode=repo_mount_mode,
        repo_root=repo_root,
        repo_source_path=repo_source_path,
        repo_mount_path=instance["repo_mount_path"],
    )

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


def _run_teardown_command(env: Any, config: dict[str, Any], instance: dict[str, Any]) -> None:
    if env is None:
        return
    teardown_command = config.get("run", {}).get("env_teardown_command")
    if not teardown_command:
        return
    try:
        rendered = Template(teardown_command, undefined=StrictUndefined).render(**instance)
        out = env.execute(rendered)
        if out.get("returncode", 0) != 0:
            logger.warning("Teardown command failed for %s: %s", instance.get("instance_id"), out)
    except Exception as exc:
        logger.warning("Teardown command error for %s: %s", instance.get("instance_id"), exc)


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

        base_commit = record.get("base_commit") or "HEAD"
        repo_mount_path = f"/repos/{repo_dir}"
        workdir = f"/work/{instance_id}"

        instance = {
            "instance_id": instance_id,
            "repo_slug": repo_slug,
            "repo_dir": repo_dir,
            "repo_path": str(repo_path),
            "repo_source_path": str(repo_path),
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
    trajectories_dir: Path,
    loc_output_path: Path,
    config: dict[str, Any],
    tool_registry: ToolRegistry,
    progress_manager: RunBatchProgressManager,
    bench_data: dict[str, Any],
    repo_root: Path,
    worktree_root: Path,
    worktrees_mode: str,
    keep_worktrees: bool,
    tools_prompt: str,
    tool_backend: str,
    enforce_tool_verification: bool,
    summary_sink: list[dict[str, Any]],
    summary_lock: threading.Lock,
) -> None:
    instance_id = instance["instance_id"]
    trajectories_dir.mkdir(parents=True, exist_ok=True)
    traj_path = trajectories_dir / f"{instance_id}.traj.json"
    traj_path.unlink(missing_ok=True)
    model = get_model(config=config.get("model", {}))
    task = instance.get("problem_statement", "")

    progress_manager.on_instance_start(instance_id)
    progress_manager.update_instance_status(instance_id, "Preparing worktree")

    agent = None
    extra_info = None
    env = None
    exit_status = "Unknown"
    result = ""
    stats: dict[str, Any] | None = None
    worktree_path: Path | None = None
    repo_path = Path(instance["repo_path"])

    try:
        repo_dir = instance["repo_dir"]
        worktree_path, resolved_commit = ensure_worktree(
            repo_path,
            repo_dir,
            instance["base_commit"],
            worktree_root,
            worktrees_mode=worktrees_mode,
        )
        instance = instance | {
            "repo_path": str(worktree_path),
            "base_commit": resolved_commit,
            "base_commit_q": shlex.quote(resolved_commit),
        }

        env = _get_locbench_environment(config, instance, repo_root)
        progress_manager.update_instance_status(instance_id, "Running agent")
        agent = ProgressTrackingAgent(
            model,
            env,
            tool_registry=tool_registry,
            progress_manager=progress_manager,
            instance_id=instance_id,
            enforce_tool_verification=enforce_tool_verification,
            **config.get("agent", {}),
        )
        exit_status, result = agent.run(task, **instance)
        if exit_status == "LimitsExceeded":
            fallback_text = _get_last_assistant_content(agent)
            if fallback_text:
                payload, raw = extract_json_payload(fallback_text)
                result = raw if payload is not None and raw else build_fallback_loc_result(fallback_text)
            # Guardrail: prevent step/cost-limit fallback from bypassing radar verification.
            if enforce_tool_verification and agent and getattr(agent, "needs_verification", False):
                agent.blocked_submission_count += 1
                exit_status = "VerificationRequired"
                result = build_fallback_loc_result("")
        stats = build_answer_stats(model)
    except Exception as exc:
        logger.error("Error processing instance %s: %s", instance_id, exc, exc_info=True)
        exit_status, result = type(exc).__name__, str(exc)
        extra_info = {"traceback": traceback.format_exc()}
        stats = build_answer_stats(model) if model else None
    finally:
        _run_teardown_command(env, config, instance)
        _cleanup_environment(env)
        if worktree_path and not keep_worktrees:
            _remove_worktree(repo_path, worktree_path)
        save_traj(
            agent,
            traj_path,
            exit_status=exit_status,
            result=result,
            extra_info=extra_info,
            instance_id=instance_id,
            print_fct=logger.info,
        )
        billing_stats = model.get_billing_stats() if model and hasattr(model, "get_billing_stats") else {}
        radar_called = getattr(agent, "radar_called_count", 0) if agent else 0
        radar_tool_output_chars = getattr(agent, "radar_tool_output_chars", 0) if agent else 0
        blocked_submission_count = getattr(agent, "blocked_submission_count", 0) if agent else 0
        radar_verified_files = sorted(getattr(agent, "verified_files", set())) if agent else []
        radar_candidate_files = sorted(getattr(agent, "candidate_files", set())) if agent else []
        radar_verification_satisfied: bool | None = None
        if radar_called:
            radar_verification_satisfied = bool(not getattr(agent, "needs_verification", False))
        output_record = build_loc_output(
            result,
            instance_id,
            bench_data.get(instance_id),
            stats=stats,
            repo_root=instance.get("repo_source_path") or instance.get("repo_path"),
        )
        output_record["tools_prompt"] = tools_prompt
        output_record["exit_status"] = exit_status
        output_record["steps"] = getattr(model, "n_calls", 0) if model else 0
        output_record["trace_tokens"] = billing_stats.get("trace_tokens", billing_stats.get("total_tokens", 0))
        output_record["billed_tokens"] = billing_stats.get("billed_tokens", billing_stats.get("total_tokens", 0))
        if tool_backend == "file_radar_search":
            output_record["radar_called"] = bool(radar_called)
            output_record["radar_tool_calls"] = radar_called
            output_record["radar_tool_output_chars"] = radar_tool_output_chars
            output_record["blocked_submission_count"] = blocked_submission_count
            output_record["radar_candidate_files"] = radar_candidate_files
            output_record["radar_verified_files"] = radar_verified_files
            output_record["radar_verification_satisfied"] = radar_verification_satisfied
        metrics = compute_locbench_metrics(
            bench_data.get(instance_id),
            output_record.get("found_files", []),
            output_record.get("found_entities", []),
        )
        output_record.update(metrics)
        _append_loc_output(loc_output_path, output_record)
        summary_record = {
            "instance_id": instance_id,
            "exit_status": exit_status,
            "steps": getattr(model, "n_calls", 0) if model else 0,
            "trace_tokens": billing_stats.get("trace_tokens", billing_stats.get("total_tokens", 0)),
            "billed_tokens": billing_stats.get("billed_tokens", billing_stats.get("total_tokens", 0)),
            "cost_usd": billing_stats.get("cost_usd", getattr(model, "cost", 0.0)),
            "correct": metrics.get("correct"),
            "tools_prompt": tools_prompt,
        }
        if tool_backend == "file_radar_search":
            summary_record["radar_called"] = bool(radar_called)
            summary_record["radar_tool_calls"] = radar_called
            summary_record["radar_tool_output_chars"] = radar_tool_output_chars
            summary_record["blocked_submission_count"] = blocked_submission_count
            summary_record["radar_verification_satisfied"] = radar_verification_satisfied
        for key, value in metrics.items():
            if key != "correct":
                summary_record[key] = value
        with summary_lock:
            summary_sink.append(summary_record)
        progress_manager.on_instance_end(instance_id, exit_status)


def run_tools(
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
    embedding_device: str | None,
    keep_worktrees: bool,
    worktrees_mode: str,
    tools_prompt: str,
    tool_backend: str,
    enforce_tool_verification: bool,
    pricing: dict[str, Any] | None,
    billing: dict[str, Any] | None,
) -> None:
    dataset_path = dataset_path.resolve()
    repos_root = repos_root.resolve()
    output_root = output_root.resolve()
    worktrees_root = (worktrees_root / "tools").resolve()
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
    if billing is not None:
        config.setdefault("model", {})["billing"] = billing

    tool_config_path = get_config_path(tool_config_path)
    tool_config = yaml.safe_load(tool_config_path.read_text())
    if indexes_root:
        tool_config["index_root"] = str(indexes_root)
    if model_root:
        tool_config["embedding_model"] = str(model_root)
    if embedding_device:
        tool_config["embedding_device"] = embedding_device
    if tool_backend == "code_search":
        tool = CodeSearchTool(tool_config)
    elif tool_backend == "file_radar_search":
        tool = FileRadarSearchTool(tool_config)
    else:
        raise ValueError(f"Unsupported tool backend: {tool_backend}")
    tool_registry = ToolRegistry()
    tool_registry.register(tool)

    default_output_dir = _default_output_dir(output_model_name, method)
    output_dir_path = Path(output_dir) if output_dir else default_output_dir
    output_dir_path.mkdir(parents=True, exist_ok=True)
    add_file_handler(output_dir_path / "minisweagent.log")
    logger.info("Results will be saved to %s", output_dir_path)

    trajectories_dir = output_dir_path / "trajectories"
    trajectories_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Trajectories will be saved to %s", trajectories_dir)

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
    instance_summaries: list[dict[str, Any]] = []
    summary_lock = threading.Lock()

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
                    trajectories_dir,
                    loc_output_path,
                    config,
                    tool_registry,
                    progress_manager,
                    bench_data,
                    repos_root,
                    worktrees_root,
                    worktrees_mode,
                    keep_worktrees,
                    tools_prompt,
                    tool_backend,
                    enforce_tool_verification,
                    instance_summaries,
                    summary_lock,
                ): instance["instance_id"]
                for instance in instances
            }
            process_futures(futures)

    if not keep_worktrees and worktrees_root.exists():
        try:
            if not any(worktrees_root.iterdir()):
                worktrees_root.rmdir()
        except OSError:
            pass

    progress_manager.print_report()
    write_run_summary(
        output_dir_path / "run_summary.json",
        meta={
            "benchmark": "locbench",
            "model": model or config.get("model", {}).get("model_name"),
            "model_class": model_class or config.get("model", {}).get("model_class"),
            "method": method,
            "effective_method": method,
            "tools_prompt": tools_prompt,
            "tool_backend": tool_backend,
            "enforce_tool_verification": enforce_tool_verification,
            "agent_config": str(config_path),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        instance_summaries=instance_summaries,
        csv_path=output_dir_path / "run_summary.csv",
    )
