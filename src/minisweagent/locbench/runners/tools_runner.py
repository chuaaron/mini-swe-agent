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
import uuid
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined, Template
from rich.live import Live

from minisweagent.agents.tool_agent import FormatError, Submitted, ToolAgent, ToolExecutionError, ToolFormatError
from minisweagent.agents.tool_agent import ExecutionTimeoutError, LimitsExceeded
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
_PATCH_DIFF_RE = re.compile(r"^diff --git a/(.*?) b/(.*?)$", re.MULTILINE)


def _get_last_assistant_content(agent: ToolAgent | None) -> str:
    if agent is None:
        return ""
    for message in reversed(agent.messages):
        if message.get("role") == "assistant":
            return message.get("content", "") or ""
    return ""


class ProgressTrackingAgent(ToolAgent):
    _STRICT_RECOVERY_THRESHOLD = 3

    def __init__(
        self,
        *args,
        progress_manager: RunBatchProgressManager,
        instance_id: str = "",
        enforce_tool_verification: bool = False,
        disallow_tools: bool = False,
        oracle_files: list[str] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.progress_manager = progress_manager
        self.instance_id = instance_id
        self.enforce_tool_verification = enforce_tool_verification
        self.disallow_tools = disallow_tools
        self.oracle_files = sorted({item.strip() for item in (oracle_files or []) if str(item).strip()})

        self.needs_verification = False
        self.candidate_files: set[str] = set()
        self.verified_files: set[str] = set()
        self.inspected_files: set[str] = set()
        self.verification_read_commands = {"rg", "grep", "sed", "cat", "nl", "head", "tail"}
        self._read_error_markers = (
            "no such file or directory",
            "io error for operation on",
            "can't read",
            "cannot open",
            "permission denied",
            "not a regular file",
        )
        self._control_tokens = {"&&", "||", ";", "|"}

        self.radar_called_count = 0
        self.radar_tool_output_chars = 0
        self.blocked_submission_count = 0
        self.radar_index_status_counts: dict[str, int] = {}
        self.radar_last_index_status: str | None = None
        self.radar_last_index_reason: str | None = None
        self.radar_last_index_dir: str | None = None
        self._verification_interception_streak = 0
        self._strict_recovery_mode = False

        if self.oracle_files:
            self.candidate_files = set(self.oracle_files)
            self.needs_verification = self.enforce_tool_verification and bool(self.candidate_files)

    def _candidate_preview_lines(self, *, include_inspected: bool = False) -> str:
        candidates = sorted(self.candidate_files)
        preview = candidates[:5]
        preview_lines = "\n".join(f"- {path}" for path in preview) if preview else "- <none recorded>"
        if len(candidates) > len(preview):
            preview_lines += f"\n- ... ({len(candidates) - len(preview)} more)"
        if include_inspected:
            inspected = sorted(self.inspected_files)
            inspected_preview = inspected[:5]
            if inspected_preview:
                preview_lines += "\nObserved read files:\n" + "\n".join(f"- {path}" for path in inspected_preview)
                if len(inspected) > len(inspected_preview):
                    preview_lines += f"\n- ... ({len(inspected) - len(inspected_preview)} more)"
        return preview_lines

    def _strict_recovery_template(self) -> str:
        target = sorted(self.candidate_files)[0] if self.candidate_files else "path/to/candidate.py"
        target_quoted = shlex.quote(target)
        target_json = target.replace("\\", "\\\\").replace('"', '\\"')
        return (
            f"sed -n '1,80p' {target_quoted} >/dev/null && "
            "printf 'MINI_SWE_AGENT_FINAL_OUTPUT\\n"
            "{\"functions\":[{\"function\":\"...\",\"file_hint\":\""
            f"{target_json}"
            "\"}]}\\n'"
        )

    def _strict_recovery_message(self, reason: str) -> str:
        return (
            f"{reason}\n"
            "STRICT_RECOVERY_MODE: Multiple invalid submit attempts were intercepted.\n"
            "Your NEXT reply must be exactly one bash command in one code block, and it must:\n"
            "1) read a radar candidate file, and 2) print MINI_SWE_AGENT_FINAL_OUTPUT JSON.\n"
            "Single allowed template:\n"
            f"`{self._strict_recovery_template()}`\n"
            "Do not call @tool. Do not submit without a read command."
        )

    def _tools_forbidden_message(self) -> str:
        preview_lines = self._candidate_preview_lines()
        return (
            "SYSTEM_INTERCEPTION: Tools Disabled in Oracle-Sniper Mode.\n"
            "This mode forbids all @tool calls.\n"
            "This is not a JSON formatting error.\n"
            "Use bash commands only (rg/cat/sed/nl/head/tail) on the provided Oracle files.\n"
            "Oracle candidate files:\n"
            f"{preview_lines}"
        )

    def _verification_interception_message(self) -> str:
        base = (
            "SYSTEM_INTERCEPTION: Verification Required.\n"
            "You invoked file_radar_search, but have not inspected any returned candidate file with bash.\n"
            "This is not a JSON formatting error.\n"
            "Before submitting final output, run at least one successful bash read command on a candidate file.\n"
            "Allowed commands: rg, grep, sed, cat, nl, head, tail.\n"
            "Examples: `rg -n \"token\" path/to/candidate.py` or `sed -n '1,80p' path/to/candidate.py`.\n"
            "If you are near the step limit, combine verification and submission in one command.\n"
            "Example: `sed -n '1,80p' path/to/candidate.py >/dev/null && printf 'MINI_SWE_AGENT_FINAL_OUTPUT\\n{...}\\n'`.\n"
            "Candidate files from radar:\n"
            f"{self._candidate_preview_lines()}"
        )
        if self._strict_recovery_mode:
            return self._strict_recovery_message(base)
        return base

    def _submission_read_interception_message(self, uninspected_hints: list[str]) -> str:
        lines = "\n".join(f"- {item}" for item in uninspected_hints)
        base = (
            "SYSTEM_INTERCEPTION: Submission Read Verification Required.\n"
            "This is not a JSON formatting error.\n"
            "Your final JSON includes file_hint values that were not observed in bash read history.\n"
            "You must read each submitted file_hint with rg/grep/sed/cat/nl/head/tail before submitting.\n"
            "Unverified file_hint values:\n"
            f"{lines}\n"
            "Radar candidates and read history:\n"
            f"{self._candidate_preview_lines(include_inspected=True)}"
        )
        if self._strict_recovery_mode:
            return self._strict_recovery_message(base)
        return base

    def _register_interception(self) -> None:
        self.blocked_submission_count += 1
        self._verification_interception_streak += 1
        if self._verification_interception_streak >= self._STRICT_RECOVERY_THRESHOLD:
            self._strict_recovery_mode = True

    def _reset_interception_guard(self) -> None:
        self._verification_interception_streak = 0
        self._strict_recovery_mode = False

    def _verification_final_prompt_message(self) -> str:
        candidates = sorted(self.candidate_files)
        target = candidates[0] if candidates else "path/to/candidate.py"
        return (
            "FINAL STEP OVERRIDE: Verification is still required before submission.\n"
            "This message supersedes the normal final-output instruction.\n"
            "You must run exactly ONE bash command that BOTH:\n"
            "1) Reads at least one candidate file from file_radar_search\n"
            "2) Prints the final output marker and JSON payload\n"
            "Use a pattern like:\n"
            f"`sed -n '1,80p' {target} >/dev/null && printf 'MINI_SWE_AGENT_FINAL_OUTPUT\\n{{\"functions\":[...]}}\\n'`\n"
            "Candidate files from radar:\n"
            f"{self._candidate_preview_lines()}"
        )

    def step(self) -> dict:
        tokens = getattr(self.model, "total_tokens", 0)
        self.progress_manager.update_instance_status(
            self.instance_id, f"Step {self.model.n_calls + 1:3d} ({tokens} toks)"
        )
        return super().step()

    def query(self) -> dict:
        if (
            self.config.step_limit > 0
            and self.model.n_calls == self.config.step_limit - 1
            and self.config.final_prompt_template
            and not self._final_prompt_injected
        ):
            if self.enforce_tool_verification and self.needs_verification:
                self.add_message("user", self._verification_final_prompt_message())
            else:
                self.add_message("user", self.render_template(self.config.final_prompt_template))
            self._final_prompt_injected = True
        if 0 < self.config.step_limit <= self.model.n_calls or 0 < self.config.cost_limit <= self.model.cost:
            raise LimitsExceeded()
        response = self.model.query(self.messages)
        self.add_message("assistant", **response)
        return response

    def parse_action(self, response: dict) -> dict:
        action = super().parse_action(response)
        if self.disallow_tools and action.get("type") == "tool":
            raise FormatError(self._tools_forbidden_message())
        if self._strict_recovery_mode and not self._is_valid_strict_recovery_action(action):
            raise FormatError(self._verification_interception_message())
        return action

    def _is_valid_strict_recovery_action(self, action: dict) -> bool:
        if action.get("type") != "bash":
            return False
        command = str(action.get("command", "") or "")
        if "MINI_SWE_AGENT_FINAL_OUTPUT" not in command and "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" not in command:
            return False
        if not self._is_read_command(command):
            return False
        tokens = self._tokenize_command(command)
        if not tokens:
            return False
        candidate_tokens: set[str] = set()
        for path in self.candidate_files:
            cleaned = path.strip()
            if not cleaned:
                continue
            candidate_tokens.add(cleaned)
            candidate_tokens.add(f"./{cleaned}")
            candidate_tokens.add(Path(cleaned).name)
        return bool(candidate_tokens.intersection(tokens))

    def _is_read_command(self, command: str) -> bool:
        parts = self._tokenize_command(command)
        if not parts:
            return False
        return any(part in self.verification_read_commands for part in parts)

    def _tokenize_command(self, command: str) -> list[str]:
        try:
            return shlex.split(command)
        except ValueError:
            return command.split()

    def _split_command_segments(self, command: str) -> list[list[str]]:
        tokens = self._tokenize_command(command)
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
        return segments

    def _repo_root(self) -> Path | None:
        raw = self.extra_template_vars.get("repo_path")
        if not raw:
            return None
        try:
            return Path(str(raw)).resolve()
        except OSError:
            return None

    def _normalize_path_token(self, token: str, *, workdir: str) -> str | None:
        cleaned = token.strip().strip(",;)")
        if not cleaned:
            return None
        if cleaned.startswith("-"):
            return None
        if cleaned.startswith((">", "<")):
            return None
        if cleaned in {".", "..", "/dev/null"}:
            return None
        if any(marker in cleaned for marker in ("*", "?", "[", "]", "{", "}", "$(", "`")):
            return None
        if workdir and cleaned.startswith(f"{workdir}/"):
            cleaned = cleaned[len(workdir) + 1 :]
        if cleaned.startswith("./"):
            cleaned = cleaned[2:]
        if cleaned.startswith("/"):
            return None
        if not cleaned:
            return None
        return Path(cleaned).as_posix()

    def _resolve_paths_from_segment(self, segment: list[str], *, workdir: str) -> set[str]:
        repo_root = self._repo_root()
        if repo_root is None:
            return set()
        resolved: set[str] = set()
        for token in segment[1:]:
            normalized = self._normalize_path_token(token, workdir=workdir)
            if not normalized:
                continue
            candidate = (repo_root / normalized).resolve()
            try:
                candidate.relative_to(repo_root)
            except ValueError:
                continue
            if candidate.is_file():
                resolved.add(candidate.relative_to(repo_root).as_posix())
        return resolved

    def _extract_paths_from_search_output(self, output_text: str, *, workdir: str) -> set[str]:
        repo_root = self._repo_root()
        if repo_root is None:
            return set()
        resolved: set[str] = set()
        for line in output_text.splitlines():
            candidate = ""
            lowered = line.lower()
            if lowered.startswith("binary file ") and " matches" in lowered:
                candidate = line[len("binary file ") :].split(" matches", 1)[0].strip()
            elif ":" in line:
                candidate = line.split(":", 1)[0].strip()
            if not candidate:
                continue
            normalized = self._normalize_path_token(candidate, workdir=workdir)
            if not normalized:
                continue
            resolved_path = (repo_root / normalized).resolve()
            try:
                resolved_path.relative_to(repo_root)
            except ValueError:
                continue
            if resolved_path.is_file():
                resolved.add(resolved_path.relative_to(repo_root).as_posix())
        return resolved

    def _extract_submission_file_hints(self, submission_payload: str) -> list[str]:
        text = submission_payload.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, dict):
            return []
        functions = payload.get("functions")
        if not isinstance(functions, list):
            return []
        hints: list[str] = []
        seen: set[str] = set()
        workdir = str(self.extra_template_vars.get("workdir") or "").rstrip("/")
        for item in functions:
            if not isinstance(item, dict):
                continue
            raw_hint = item.get("file_hint")
            if not isinstance(raw_hint, str):
                continue
            normalized = self._normalize_path_token(raw_hint, workdir=workdir)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            hints.append(normalized)
        return hints

    def _is_hint_inspected(self, hint: str) -> bool:
        observed = set(self.inspected_files) | set(self.verified_files)
        if not observed:
            return False
        if hint in observed:
            return True
        basename = Path(hint).name
        basename_hits = [item for item in observed if Path(item).name == basename]
        if basename and len(basename_hits) == 1:
            return True
        if "/" in hint:
            return any(item.endswith(hint) for item in observed)
        return False

    def _allows_read_verification(self, *, command_name: str, return_code: int, output_text: str) -> bool:
        if command_name not in self.verification_read_commands:
            return False
        if return_code == 0:
            return True
        if return_code != 1:
            return False
        lowered = output_text.lower()
        return not any(marker in lowered for marker in self._read_error_markers)

    def _mark_verification_from_command(self, command: str, output: dict[str, Any]) -> None:
        return_code = output.get("returncode", None)
        if return_code is None:
            return
        try:
            parsed_return_code = int(return_code)
        except (TypeError, ValueError):
            return
        if not self._is_read_command(command):
            return

        segments = self._split_command_segments(command)

        basename_counts: dict[str, int] = {}
        for file_path in self.candidate_files:
            name = Path(file_path).name
            if name:
                basename_counts[name] = basename_counts.get(name, 0) + 1

        workdir = str(self.extra_template_vars.get("workdir") or "").rstrip("/")
        output_text = str(output.get("output", "") or "")
        for segment in segments:
            if not segment:
                continue
            cmd_name = Path(segment[0]).name
            if not self._allows_read_verification(
                command_name=cmd_name,
                return_code=parsed_return_code,
                output_text=output_text,
            ):
                continue
            resolved_paths = self._resolve_paths_from_segment(segment, workdir=workdir)
            if cmd_name in {"rg", "grep"}:
                resolved_paths.update(self._extract_paths_from_search_output(output_text, workdir=workdir))
            self.inspected_files.update(resolved_paths)

            if not self.needs_verification or not self.candidate_files:
                continue

            segment_tokens = {token.strip() for token in segment}
            for file_path in self.candidate_files:
                if file_path in resolved_paths:
                    self.verified_files.add(file_path)
                    continue
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
                    continue
                if cmd_name in {"rg", "grep"} and (
                    f"{rel}:" in output_text
                    or f"./{rel}:" in output_text
                    or f"binary file {rel.lower()} matches" in output_text.lower()
                ):
                    self.verified_files.add(file_path)

        if self.needs_verification and self.verified_files:
            self.needs_verification = False
            self._reset_interception_guard()

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
            if isinstance(result.data, dict):
                index_status = str(result.data.get("index_status") or "").strip()
                if index_status:
                    self.radar_index_status_counts[index_status] = self.radar_index_status_counts.get(index_status, 0) + 1
                    self.radar_last_index_status = index_status
                index_reason = str(result.data.get("index_compat_reason") or "").strip()
                if index_reason:
                    self.radar_last_index_reason = index_reason
                index_dir = str(result.data.get("index_dir") or "").strip()
                if index_dir:
                    self.radar_last_index_dir = index_dir
            candidate_files: set[str] = set()
            for item in result.data.get("results", []) if isinstance(result.data, dict) else []:
                path = item.get("path")
                if isinstance(path, str) and path.strip():
                    candidate_files.add(path.strip())
            self.candidate_files = candidate_files
            self.verified_files = set()
            self._reset_interception_guard()
            self.needs_verification = self.enforce_tool_verification and bool(candidate_files)

        return {
            "type": "tool",
            "output": result.output,
            "returncode": result.returncode,
            "action": action["raw"],
            "data": result.data,
        }

    def execute_bash(self, action: dict) -> dict:
        try:
            output = self.env.execute(action["command"])
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            timeout_output = exc.output.decode("utf-8", errors="replace") if getattr(exc, "output", None) else ""
            raise ExecutionTimeoutError(
                self.render_template(self.config.timeout_template, action=action, output=timeout_output)
            ) from exc
        self._mark_verification_from_command(action.get("command", ""), output)
        self.has_finished(output)
        return output | {"type": "bash", "action": action["command"]}

    def has_finished(self, output: dict[str, str]):
        lines = output.get("output", "").lstrip().splitlines(keepends=True)
        if not lines:
            return
        marker = lines[0].strip()
        if marker not in {"MINI_SWE_AGENT_FINAL_OUTPUT", "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}:
            return
        if self.enforce_tool_verification and self.needs_verification:
            self._register_interception()
            raise FormatError(self._verification_interception_message())
        submission_payload = "".join(lines[1:])
        if self.enforce_tool_verification and self.radar_called_count:
            hints = self._extract_submission_file_hints(submission_payload)
            uninspected_hints = [hint for hint in hints if not self._is_hint_inspected(hint)]
            if uninspected_hints:
                self._register_interception()
                raise FormatError(self._submission_read_interception_message(uninspected_hints))
        self._reset_interception_guard()
        raise Submitted(submission_payload)


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
        oracle_sniper_mode: bool,
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
        self.oracle_sniper_mode = oracle_sniper_mode
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
            oracle_sniper_mode=self.oracle_sniper_mode,
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


def _normalize_oracle_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return Path(normalized).as_posix()


def _is_test_file(path: str) -> bool:
    lowered = path.lower()
    name = lowered.split("/")[-1]
    return (
        lowered.startswith("tests/")
        or "/tests/" in lowered
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _extract_patch_files(patch_text: str) -> list[str]:
    if not patch_text:
        return []
    paths: list[str] = []
    for match in _PATCH_DIFF_RE.finditer(patch_text):
        right = _normalize_oracle_path(match.group(2))
        if not right or right == "dev/null":
            continue
        paths.append(right)
    return paths


def _extract_edit_function_files(edit_functions: Any) -> list[str]:
    if not edit_functions:
        return []
    values = edit_functions if isinstance(edit_functions, list) else [edit_functions]
    files: list[str] = []
    for item in values:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text:
            continue
        path = text.split(":", 1)[0].strip()
        if not path:
            continue
        files.append(_normalize_oracle_path(path))
    return files


def _extract_oracle_files(record: dict[str, Any]) -> tuple[list[str], bool]:
    candidates = _extract_patch_files(str(record.get("patch") or ""))
    candidates.extend(_extract_edit_function_files(record.get("edit_functions")))

    deduped: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        cleaned = _normalize_oracle_path(path)
        if not cleaned or cleaned.startswith("/") or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)

    filtered = [path for path in deduped if not _is_test_file(path)]
    if filtered:
        return filtered, False
    return deduped, bool(deduped)


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
        oracle_files, oracle_fallback_to_tests = _extract_oracle_files(record)

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
            "oracle_files": oracle_files,
            "oracle_primary_file": oracle_files[0] if oracle_files else "",
            "oracle_fallback_to_tests": oracle_fallback_to_tests,
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
    oracle_sniper_mode: bool,
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
            disallow_tools=oracle_sniper_mode,
            oracle_files=list(instance.get("oracle_files") or []) if oracle_sniper_mode else [],
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
                # Keep the extracted fallback result instead of discarding it;
                # build_loc_output will still produce found_files from it.
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
        inspected_files = sorted(getattr(agent, "inspected_files", set())) if agent else []
        radar_index_status_counts = dict(getattr(agent, "radar_index_status_counts", {})) if agent else {}
        radar_last_index_status = getattr(agent, "radar_last_index_status", None) if agent else None
        radar_last_index_reason = getattr(agent, "radar_last_index_reason", None) if agent else None
        radar_last_index_dir = getattr(agent, "radar_last_index_dir", None) if agent else None
        oracle_files = list(instance.get("oracle_files") or [])
        oracle_primary_file = str(instance.get("oracle_primary_file") or "")
        oracle_file_provided = bool(oracle_files)
        verification_satisfied: bool | None = None
        if enforce_tool_verification and agent and (radar_called or (oracle_sniper_mode and oracle_file_provided)):
            verification_satisfied = bool(not getattr(agent, "needs_verification", False))
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
            output_record["inspected_files"] = inspected_files
            output_record["radar_verification_satisfied"] = verification_satisfied
            output_record["radar_index_status_counts"] = radar_index_status_counts
            output_record["radar_last_index_status"] = radar_last_index_status
            output_record["radar_last_index_reason"] = radar_last_index_reason
            output_record["radar_last_index_dir"] = radar_last_index_dir
            output_record["oracle_sniper_mode"] = oracle_sniper_mode
            output_record["oracle_file_provided"] = oracle_file_provided
            output_record["oracle_file_count"] = len(oracle_files)
            output_record["oracle_primary_file"] = oracle_primary_file
            output_record["oracle_files"] = oracle_files
            output_record["oracle_fallback_to_tests"] = bool(instance.get("oracle_fallback_to_tests"))
            output_record["oracle_verification_satisfied"] = (
                verification_satisfied if oracle_sniper_mode and oracle_file_provided else None
            )
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
            summary_record["radar_verification_satisfied"] = verification_satisfied
            summary_record["radar_index_status_counts"] = radar_index_status_counts
            summary_record["radar_last_index_status"] = radar_last_index_status
            summary_record["radar_last_index_reason"] = radar_last_index_reason
            summary_record["radar_last_index_dir"] = radar_last_index_dir
            summary_record["oracle_sniper_mode"] = oracle_sniper_mode
            summary_record["oracle_file_provided"] = oracle_file_provided
            summary_record["oracle_file_count"] = len(oracle_files)
            summary_record["oracle_primary_file"] = oracle_primary_file
            summary_record["oracle_fallback_to_tests"] = bool(instance.get("oracle_fallback_to_tests"))
            summary_record["oracle_verification_satisfied"] = (
                verification_satisfied if oracle_sniper_mode and oracle_file_provided else None
            )
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
    oracle_sniper_mode: bool,
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

    tool_registry = ToolRegistry()
    if oracle_sniper_mode:
        logger.info("Oracle-sniper mode enabled: tool registry is intentionally empty.")
    else:
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
                    oracle_sniper_mode,
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
            "oracle_sniper_mode": oracle_sniper_mode,
            "agent_config": str(config_path),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        instance_summaries=instance_summaries,
        csv_path=output_dir_path / "run_summary.csv",
    )
