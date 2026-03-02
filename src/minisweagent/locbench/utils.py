"""Shared utilities for LocBench runners."""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Any, Iterable

JSON_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_FALLBACK_FILE_RE = re.compile(r"(?:(?:^|\\b)([A-Za-z0-9_./-]+\\.[A-Za-z0-9_]+))")
_FUNCTION_INDEX_CACHE: dict[str, dict[str, list[dict[str, str]]]] = {}


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            insert_cost = curr[j - 1] + 1
            delete_cost = prev[j] + 1
            replace_cost = prev[j - 1] + (0 if ca == cb else 1)
            curr.append(min(insert_cost, delete_cost, replace_cost))
        prev = curr
    return prev[-1]


def _normalize_function_items(value: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if not value:
        return items
    raw_items = value if isinstance(value, list) else [value]
    for raw in raw_items:
        if isinstance(raw, dict):
            function = str(raw.get("function") or raw.get("name") or raw.get("func") or "").strip()
            file_hint = str(raw.get("file_hint") or raw.get("file") or raw.get("path") or "").strip()
        else:
            function = str(raw).strip()
            file_hint = ""
        if not function:
            continue
        items.append({"function": function, "file_hint": file_hint})
    return items


def _index_record(index: dict[str, list[dict[str, str]]], key: str, record: dict[str, str]) -> None:
    cleaned = key.strip()
    if not cleaned:
        return
    bucket = index.setdefault(cleaned, [])
    for existing in bucket:
        if existing.get("file") == record.get("file") and existing.get("qualname") == record.get("qualname"):
            return
    bucket.append(record)


def _candidate_id(record: dict[str, str]) -> tuple[str, str]:
    return str(record.get("file") or ""), str(record.get("qualname") or "")


def _gather_function_candidates(index: dict[str, list[dict[str, str]]], func: str) -> list[dict[str, str]]:
    query = str(func or "").strip()
    if not query:
        return []
    keys: list[str] = [query]
    if "." in query:
        parts = [part for part in query.split(".") if part]
        if len(parts) >= 2:
            keys.append(".".join(parts[-2:]))
        keys.append(parts[-1])
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for key in keys:
        for record in index.get(key, []):
            rec_id = _candidate_id(record)
            if rec_id in seen:
                continue
            seen.add(rec_id)
            deduped.append(record)
    return deduped


def _function_match_rank(query_func: str, record: dict[str, str]) -> tuple[int, int]:
    query = str(query_func or "").strip()
    qualname = str(record.get("qualname") or "").strip()
    name = str(record.get("name") or "").strip()
    if not query:
        return 4, 0
    if query == qualname:
        return 0, 0
    if qualname and qualname.endswith(f".{query}"):
        return 1, max(0, len(qualname) - len(query))
    if "." in query and "." in qualname:
        query_tail2 = ".".join(query.split(".")[-2:])
        qual_tail2 = ".".join(qualname.split(".")[-2:])
        if query_tail2 == qual_tail2:
            return 2, 0
    if query.rsplit(".", 1)[-1] == name:
        # Query used only leaf name or mismatched class prefix.
        return 3, 0 if "." in query else 1
    return 4, 0


def _build_function_index(repo_root: str) -> dict[str, list[dict[str, str]]]:
    index: dict[str, list[dict[str, str]]] = {}
    root = Path(repo_root)
    for path in root.rglob("*.py"):
        rel_path = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = __import__("ast").parse(source, filename=str(path))
        except SyntaxError:
            continue

        class_stack: list[str] = []
        func_stack: list[str] = []

        class Visitor(__import__("ast").NodeVisitor):
            def visit_ClassDef(self, node):  # type: ignore[override]
                cls_name = getattr(node, "name", "")
                if cls_name:
                    qual_parts = class_stack + [cls_name]
                    qualname = ".".join(qual_parts) if len(qual_parts) > 1 else cls_name
                    record = {
                        "file": rel_path,
                        "name": cls_name,
                        "qualname": qualname,
                    }
                    _index_record(index, cls_name, record)
                    _index_record(index, qualname, record)
                class_stack.append(node.name)
                self.generic_visit(node)
                class_stack.pop()

            def visit_FunctionDef(self, node):  # type: ignore[override]
                self._visit_function(node)

            def visit_AsyncFunctionDef(self, node):  # type: ignore[override]
                self._visit_function(node)

            def _visit_function(self, node):
                func_name = getattr(node, "name", "")
                if not func_name:
                    return
                qual_parts = class_stack + func_stack + [func_name]
                qualname = ".".join(qual_parts) if qual_parts else func_name
                record = {
                    "file": rel_path,
                    "name": func_name,
                    "qualname": qualname,
                }
                _index_record(index, func_name, record)
                _index_record(index, qualname, record)
                if "." in qualname:
                    _index_record(index, ".".join(qualname.split(".")[-2:]), record)
                func_stack.append(func_name)
                self.generic_visit(node)
                func_stack.pop()

        try:
            Visitor().visit(tree)
        except RecursionError:
            continue
    return index


def _get_function_index(repo_root: str) -> dict[str, list[dict[str, str]]]:
    cache_key = os.path.abspath(repo_root)
    if cache_key not in _FUNCTION_INDEX_CACHE:
        _FUNCTION_INDEX_CACHE[cache_key] = _build_function_index(cache_key)
    return _FUNCTION_INDEX_CACHE[cache_key]


def _select_best_match(candidates: list[dict[str, str]], file_hint: str, query_func: str) -> list[dict[str, str]]:
    if not file_hint:
        return candidates
    hint = file_hint.lower()
    scored: list[tuple[tuple[int, int, int], dict[str, str]]] = []
    for record in candidates:
        func_rank = _function_match_rank(query_func, record)
        file_path = record["file"].lower()
        basename = Path(file_path).name
        if hint in file_path:
            path_rank = 0
        else:
            path_rank = min(_levenshtein(hint, file_path), _levenshtein(hint, basename))
        scored.append(((func_rank[0], func_rank[1], path_rank), record))
    scored.sort(key=lambda item: item[0])
    if not scored:
        return []
    best = scored[0][0]
    return [record for score, record in scored if score == best]


def map_functions_to_entities(
    repo_root: str,
    functions: list[dict[str, str]],
    *,
    top_k: int = 10,
) -> tuple[list[str], list[str], list[str]]:
    index = _get_function_index(repo_root)
    found_entities: list[str] = []
    found_files: list[str] = []
    seen_entities: set[str] = set()
    seen_files: set[str] = set()

    for item in functions:
        func = item.get("function", "").strip()
        file_hint = item.get("file_hint", "").strip()
        if not func:
            continue
        candidates = _gather_function_candidates(index, func)
        if candidates:
            if file_hint:
                selected = _select_best_match(candidates, file_hint, func)
            elif len(candidates) == 1:
                selected = candidates
            else:
                selected = sorted(candidates, key=lambda rec: _function_match_rank(func, rec))[:top_k]
            for record in selected[:top_k]:
                entity_id = f"{record['file']}:{record['qualname']}"
                if entity_id not in seen_entities:
                    found_entities.append(entity_id)
                    seen_entities.add(entity_id)
                if record["file"] not in seen_files:
                    found_files.append(record["file"])
                    seen_files.add(record["file"])
        elif file_hint:
            if file_hint not in seen_files:
                found_files.append(file_hint)
                seen_files.add(file_hint)
            entity_id = f"{file_hint}:{func}"
            if entity_id not in seen_entities:
                found_entities.append(entity_id)
                seen_entities.add(entity_id)

    found_modules = entities_to_modules(found_entities)
    return found_files, found_entities, found_modules


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
    repo_root: str | None = None,
) -> dict[str, Any]:
    payload, raw_response = extract_json_payload(result)
    if payload is None:
        payload = {}
        raw_response = result.strip() if result else ""

    found_files = normalize_list(payload.get("found_files") or payload.get("files"))
    found_entities = normalize_list(payload.get("found_entities") or payload.get("entities"))
    found_modules = normalize_list(payload.get("found_modules") or payload.get("modules"))
    has_functions_key = isinstance(payload, dict) and "functions" in payload
    function_items = _normalize_function_items(payload.get("functions")) if has_functions_key else []

    if repo_root and function_items:
        found_files, found_entities, found_modules = map_functions_to_entities(
            repo_root, function_items
        )

    if not found_files and found_entities:
        found_files = normalize_list([item.split(":", 1)[0] for item in found_entities if ":" in item])
    if not found_modules and found_entities:
        found_modules = entities_to_modules(found_entities)

    submitted_function_count: int | None = None
    submitted_unique_function_count: int | None = None
    submitted_file_hint_count: int | None = None
    submitted_qualified_function_count: int | None = None
    submitted_qualified_function_ratio: float | None = None
    if has_functions_key:
        submitted_names = [item.get("function", "").strip() for item in function_items if item.get("function")]
        submitted_hints = [item.get("file_hint", "").strip() for item in function_items if item.get("file_hint")]
        submitted_function_count = len(function_items)
        submitted_unique_function_count = len(set(submitted_names))
        submitted_file_hint_count = len(set(submitted_hints))
        submitted_qualified_function_count = sum(1 for name in submitted_names if "." in name)
        submitted_qualified_function_ratio = (
            submitted_qualified_function_count / len(submitted_names) if submitted_names else 0.0
        )

    output = {
        "instance_id": instance_id,
        "found_files": found_files,
        "found_modules": found_modules,
        "found_entities": found_entities,
        "submission_has_functions_key": has_functions_key,
        "submitted_function_count": submitted_function_count,
        "submitted_unique_function_count": submitted_unique_function_count,
        "submitted_file_hint_count": submitted_file_hint_count,
        "submitted_qualified_function_count": submitted_qualified_function_count,
        "submitted_qualified_function_ratio": submitted_qualified_function_ratio,
        "raw_output_loc": [raw_response] if raw_response else [],
        "meta_data": build_meta(record),
    }
    if stats:
        output["stats"] = stats
    return output


def _parse_gt_functions(values: Any) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    entities: set[str] = set()
    raw_values = values if isinstance(values, list) else [values]
    for func in raw_values:
        if not isinstance(func, str):
            continue
        cleaned = func.strip()
        if ":" not in cleaned:
            continue
        file_path, _name = cleaned.rsplit(":", 1)
        if not file_path:
            continue
        files.add(file_path)
        entities.add(cleaned)
    return files, entities


def _build_gt_components(record: dict[str, Any] | None) -> dict[str, set[str]]:
    if not record:
        return {
            "all_files": set(),
            "all_entities": set(),
            "edit_files": set(),
            "edit_entities": set(),
            "added_files": set(),
            "added_entities": set(),
        }
    edit_files, edit_entities = _parse_gt_functions(record.get("edit_functions", []))
    added_files, added_entities = _parse_gt_functions(record.get("added_functions", []))
    return {
        "all_files": set(edit_files) | set(added_files),
        "all_entities": set(edit_entities) | set(added_entities),
        "edit_files": edit_files,
        "edit_entities": edit_entities,
        "added_files": added_files,
        "added_entities": added_entities,
    }


def _build_gt_sets(record: dict[str, Any] | None) -> tuple[set[str], set[str]]:
    components = _build_gt_components(record)
    return components["all_files"], components["all_entities"]


def _recall_at_k(gt: set[str], preds: list[str], k: int) -> float:
    if not gt:
        return 0.0
    if k <= 0:
        return 0.0
    return len(gt & set(preds[:k])) / len(gt)


def _recall_all(gt: set[str], preds: list[str]) -> float:
    if not gt:
        return 0.0
    return len(gt & set(preds)) / len(gt)


def compute_locbench_metrics(
    record: dict[str, Any] | None,
    found_files: list[str],
    found_entities: list[str],
    ks: tuple[int, ...] = (1, 3, 5, 10),
) -> dict[str, Any]:
    components = _build_gt_components(record)
    gt_files = components["all_files"]
    gt_entities = components["all_entities"]
    gt_edit_entities = components["edit_entities"]
    gt_added_entities = components["added_entities"]
    file_hit_any = len(gt_files & set(found_files)) > 0 if gt_files else False
    entity_hit_any = len(gt_entities & set(found_entities)) > 0 if gt_entities else False
    file_recall_all = _recall_all(gt_files, found_files)
    entity_recall_all = _recall_all(gt_entities, found_entities)
    metrics: dict[str, Any] = {
        "file_hit_any": file_hit_any,
        "entity_hit_any": entity_hit_any,
        # Keep explicit function-level aliases for reporting readability.
        "function_hit_any": entity_hit_any,
        "correct": bool(file_hit_any or entity_hit_any),
        "file_recall_all": file_recall_all,
        "entity_recall_all": entity_recall_all,
        "function_recall_all": entity_recall_all,
        "file_acc_all": file_recall_all >= 1.0 if gt_files else False,
        "entity_acc_all": entity_recall_all >= 1.0 if gt_entities else False,
        "function_acc_all": entity_recall_all >= 1.0 if gt_entities else False,
        "gt_file_count_all": len(gt_files),
        "gt_function_count_all": len(gt_entities),
        "gt_edit_function_count": len(gt_edit_entities),
        "gt_added_function_count": len(gt_added_entities),
        "gt_has_added_functions": bool(gt_added_entities),
        "gt_single_file": len(gt_files) == 1 if gt_files else False,
    }
    for k in ks:
        metrics[f"file_recall_at_{k}"] = _recall_at_k(gt_files, found_files, k)
        metrics[f"entity_recall_at_{k}"] = _recall_at_k(gt_entities, found_entities, k)
        metrics[f"function_recall_at_{k}"] = metrics[f"entity_recall_at_{k}"]
    edit_recall_all = _recall_all(gt_edit_entities, found_entities)
    added_recall_all = _recall_all(gt_added_entities, found_entities)
    metrics["edit_function_hit_any"] = len(gt_edit_entities & set(found_entities)) > 0 if gt_edit_entities else None
    metrics["edit_function_recall_all"] = edit_recall_all if gt_edit_entities else None
    metrics["edit_function_acc_all"] = edit_recall_all >= 1.0 if gt_edit_entities else None
    metrics["added_function_hit_any"] = len(gt_added_entities & set(found_entities)) > 0 if gt_added_entities else None
    metrics["added_function_recall_all"] = added_recall_all if gt_added_entities else None
    metrics["added_function_acc_all"] = added_recall_all >= 1.0 if gt_added_entities else None
    for k in ks:
        metrics[f"edit_function_recall_at_{k}"] = (
            _recall_at_k(gt_edit_entities, found_entities, k) if gt_edit_entities else None
        )
        metrics[f"added_function_recall_at_{k}"] = (
            _recall_at_k(gt_added_entities, found_entities, k) if gt_added_entities else None
        )
    return metrics


def extract_fallback_files(text: str) -> list[str]:
    if not text:
        return []
    candidates = _FALLBACK_FILE_RE.findall(text)
    cleaned: list[str] = []
    seen = set()
    for candidate in candidates:
        value = candidate.strip().strip(",.;:()[]{}<>\"'")
        if not value or value in seen:
            continue
        seen.add(value)
        cleaned.append(value)
    return cleaned


def build_fallback_loc_result(text: str) -> str:
    payload = {
        "found_files": extract_fallback_files(text),
        "found_entities": [],
        "found_modules": [],
    }
    return json.dumps(payload, ensure_ascii=True)


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
