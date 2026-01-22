"""Shared utilities for SWE-QA-Bench runners."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any, Iterable

from minisweagent.tools.registry import ToolRegistry

JSON_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)

_SHELL_SEPARATORS = {"|", "||", "&&", ";"}
_FILE_READ_COMMANDS = {"cat", "sed", "head", "tail", "rg", "grep"}

_CMD_ARG_FLAGS = {
    "rg": {
        "-e",
        "--regexp",
        "-f",
        "--file",
        "-g",
        "--glob",
        "--type",
        "--type-add",
        "--type-not",
        "--type-clear",
        "--path-separator",
        "--encoding",
    },
    "grep": {"-e", "-f", "--regexp", "--file"},
    "sed": {"-e", "-f", "--expression", "--file", "-i", "--in-place"},
    "head": {"-n", "--lines", "-c", "--bytes"},
    "tail": {"-n", "--lines", "-c", "--bytes"},
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_num, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_num} of {path}") from exc
    return records


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


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


def _iter_json_substrings(text: str) -> Iterable[str]:
    starts = [idx for idx, ch in enumerate(text) if ch == "{"]
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


def validate_output_model_name(name: str) -> None:
    if not name:
        raise ValueError("output_model_name must be set")
    if "/" in name or "\\" in name:
        raise ValueError("output_model_name cannot contain path separators")


def build_answer_stats(model: Any) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    if hasattr(model, "get_billing_stats"):
        try:
            billing_stats = model.get_billing_stats()
        except Exception:
            billing_stats = {}
        if isinstance(billing_stats, dict):
            stats.update(billing_stats)
    stats.setdefault("api_calls", getattr(model, "n_calls", 0))
    stats.setdefault("cost_usd", getattr(model, "cost", 0.0))
    model_config = getattr(model, "config", None)
    model_name = getattr(model_config, "model_name", None) if model_config is not None else None
    if model_name:
        stats.setdefault("model_name", model_name)
    stats.setdefault("model_class", model.__class__.__name__)
    return stats


def prepare_local_instances(instances: list[dict[str, Any]], worktree_root: Path) -> None:
    worktree_root = worktree_root.resolve()
    worktree_root.mkdir(parents=True, exist_ok=True)
    for instance in instances:
        repo_path = str(instance["repo_path"])
        workdir = (worktree_root / instance["instance_id"]).resolve()
        instance["repo_mount_path"] = repo_path
        instance["repo_mount_path_q"] = shlex.quote(repo_path)
        instance["workdir"] = str(workdir)
        instance["workdir_q"] = shlex.quote(str(workdir))
        instance["workdir_parent"] = str(worktree_root)
        instance["workdir_parent_q"] = shlex.quote(str(worktree_root))


def split_shell_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _SHELL_SEPARATORS:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _normalize_candidate_path(
    raw_path: str,
    repo_path: Path,
    repo_mount_path: str,
    workdir: str,
) -> str:
    if not raw_path:
        return ""
    raw_path = raw_path.strip()
    if not raw_path or raw_path == "-":
        return ""

    path = Path(raw_path)
    rel: Path | None = None
    if path.is_absolute():
        try:
            rel = path.relative_to(repo_mount_path)
        except ValueError:
            try:
                rel = path.relative_to(workdir)
            except ValueError:
                return ""
    else:
        rel = path

    try:
        candidate = (repo_path / rel).resolve()
        candidate.relative_to(repo_path.resolve())
    except (OSError, ValueError):
        return ""
    if not candidate.is_file():
        return ""
    return candidate.relative_to(repo_path).as_posix()


def _collect_file_args(tokens: list[str], cmd: str) -> list[str]:
    skip_flags = _CMD_ARG_FLAGS.get(cmd, set())
    candidates: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in skip_flags:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        candidates.append(token)
    return candidates


def extract_paths_from_command(
    command: str,
    repo_path: Path,
    repo_mount_path: str,
    workdir: str,
) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []
    segments = split_shell_segments(tokens)
    paths: list[str] = []
    seen = set()
    for segment in segments:
        if not segment:
            continue
        cmd = segment[0]
        if cmd not in _FILE_READ_COMMANDS:
            continue
        for candidate in _collect_file_args(segment[1:], cmd):
            rel_path = _normalize_candidate_path(candidate, repo_path, repo_mount_path, workdir)
            if rel_path and rel_path not in seen:
                seen.add(rel_path)
                paths.append(rel_path)
    return paths


def extract_paths_from_output(
    output: str,
    repo_path: Path,
    repo_mount_path: str,
    workdir: str,
) -> list[str]:
    paths: list[str] = []
    seen = set()
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        path_candidate = ""
        if ":" in line:
            path_candidate = line.split(":", 1)[0]
        else:
            path_candidate = line
        rel_path = _normalize_candidate_path(path_candidate, repo_path, repo_mount_path, workdir)
        if rel_path and rel_path not in seen:
            seen.add(rel_path)
            paths.append(rel_path)
    return paths


class FileReadTracker:
    def __init__(self, repo_path: Path, repo_mount_path: str, workdir: str):
        self.repo_path = repo_path
        self.repo_mount_path = repo_mount_path
        self.workdir = workdir
        self._paths: list[str] = []
        self._seen: set[str] = set()

    def ingest(self, command: str, output: str) -> None:
        cmd_paths = extract_paths_from_command(command, self.repo_path, self.repo_mount_path, self.workdir)
        out_paths: list[str] = []
        if _command_includes_rg(command):
            out_paths = extract_paths_from_output(output, self.repo_path, self.repo_mount_path, self.workdir)
        for path in cmd_paths + out_paths:
            if path in self._seen:
                continue
            self._seen.add(path)
            self._paths.append(path)

    @property
    def paths(self) -> list[str]:
        return list(self._paths)


def merge_relative_code_list(
    tool_candidates: list[str],
    files_read: list[str],
    *,
    limit: int = 50,
) -> list[str]:
    merged: list[str] = []
    seen = set()
    for item in tool_candidates + files_read:
        if not item or item in seen:
            continue
        seen.add(item)
        merged.append(item)
    if len(merged) > limit:
        merged = merged[:limit]
        merged.append("<<TRUNCATED>>")
    return merged


class TrackingToolRegistry(ToolRegistry):
    def __init__(self, repo_path: Path, repo_mount_path: str, workdir: str):
        super().__init__()
        self.repo_path = repo_path
        self.repo_mount_path = repo_mount_path
        self.workdir = workdir
        self.tool_candidates: list[str] = []
        self._seen: set[str] = set()

    def execute(self, command: str, *, context: dict[str, Any]):
        result = super().execute(command, context=context)
        if command.startswith("@tool code_search") and result.success:
            for item in result.data.get("results", []):
                raw_path = item.get("path") or ""
                rel_path = _normalize_candidate_path(raw_path, self.repo_path, self.repo_mount_path, self.workdir)
                if rel_path and rel_path not in self._seen:
                    self._seen.add(rel_path)
                    self.tool_candidates.append(rel_path)
        return result


def _command_includes_rg(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    for segment in split_shell_segments(tokens):
        if segment and segment[0] in {"rg", "grep"}:
            return True
    return False
