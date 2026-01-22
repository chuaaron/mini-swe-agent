"""Shared utilities for LocBench runners."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any, Iterable

JSON_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


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


def build_repo_dir_name(repo_slug: str) -> str:
    return repo_slug.replace("/", "_")


def build_repo_path(repo_root: Path, repo_slug: str) -> Path:
    return repo_root / build_repo_dir_name(repo_slug)


def sanitize_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


def normalize_list(value: Any) -> list[str]:
    if not value:
        return []
    items = value if isinstance(value, list) else [value]
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


def build_loc_output(
    result: str,
    instance_id: str,
    record: dict[str, Any] | None,
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
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

    output = {
        "instance_id": instance_id,
        "found_files": found_files,
        "found_modules": found_modules,
        "found_entities": found_entities,
        "raw_output_loc": [raw_response] if raw_response else [],
        "meta_data": build_meta(record),
    }
    if stats:
        output["stats"] = stats
    return output


def load_existing_instance_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    existing = set()
    for record in load_jsonl(path):
        instance_id = record.get("instance_id")
        if instance_id:
            existing.add(instance_id)
    return existing


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
        from minisweagent.utils.log import logger

        logger.info("Instance filter: %s -> %s instances", before_filter, after_filter)
    if slice_spec:
        values = [int(x) if x else None for x in slice_spec.split(":")]
        instances = instances[slice(*values)]
    return instances


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
