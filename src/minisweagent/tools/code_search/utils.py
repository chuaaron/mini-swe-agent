"""Shared helper utilities for code_search tooling."""

from __future__ import annotations

import re
from typing import List


def instance_id_to_repo_name(instance_id: str) -> str:
    """Convert instance_id to repo dir name (UXARRAY__uxarray-1117 -> UXARRAY_uxarray)."""
    repo_part = re.sub(r"-\d+$", "", instance_id)
    return repo_part.replace("__", "_")


def dedupe_append(target: List[str], item: str, limit: int) -> None:
    """Append item if not present and within limit."""
    if item not in target and len(target) < limit:
        target.append(item)


def clean_file_path(file_path: str, repo_name: str) -> str:
    """Normalize file path to match repo-relative format."""
    repo_pattern = repo_name.replace("_", "[_/]")
    match = re.search(rf"{repo_pattern}/(.+)$", file_path, re.IGNORECASE)
    if match:
        return match.group(1)
    if "//" in file_path:
        return file_path.split("//")[-1]
    return file_path
