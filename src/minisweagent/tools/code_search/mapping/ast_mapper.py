"""AST-based mapper (runtime parsing, no graph index)."""

from __future__ import annotations

import ast
import logging
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from minisweagent.tools.code_search.mapping.base import BlockMapper
from minisweagent.tools.code_search.utils import instance_id_to_repo_name

logger = logging.getLogger(__name__)


def build_line_to_entity_map(file_path: str, repo_root: str) -> Dict[int, Tuple[str | None, Optional[str]]]:
    """Build a line->(entity_id, class_id) map for a Python file (1-based line numbers)."""
    full_path = os.path.join(repo_root, file_path)
    if not os.path.exists(full_path):
        return {}

    try:
        with open(full_path, "r", encoding="utf-8") as handle:
            source_code = handle.read()
        tree = ast.parse(source_code, filename=full_path)
    except (SyntaxError, UnicodeDecodeError, FileNotFoundError) as exc:
        logger.debug("Failed to parse %s: %s", full_path, exc)
        return {}

    line_map: Dict[int, Tuple[str | None, Optional[str]]] = {}

    class EntityCollector(ast.NodeVisitor):
        def __init__(self):
            self.class_stack: list[str] = []
            self.function_stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef):
            class_name = node.name
            full_class_name = ".".join(self.class_stack + [class_name])
            class_id = f"{file_path}:{full_class_name}"

            self.class_stack.append(class_name)
            end_line = node.end_lineno if getattr(node, "end_lineno", None) else node.lineno
            for line_num in range(node.lineno, end_line + 1):
                line_map[line_num] = (None, class_id)
            self.generic_visit(node)
            self.class_stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef):
            self._visit_function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
            self._visit_function(node)

        def _visit_function(self, node: ast.AST):
            function_name = getattr(node, "name", "")
            full_function_name = ".".join(self.function_stack + [function_name])

            if self.class_stack:
                class_name = ".".join(self.class_stack)
                full_name = f"{class_name}.{function_name}"
                entity_id = f"{file_path}:{full_name}"
                class_id = f"{file_path}:{class_name}"
            else:
                entity_id = f"{file_path}:{full_function_name}"
                class_id = None

            end_line = node.end_lineno if getattr(node, "end_lineno", None) else node.lineno
            for line_num in range(node.lineno, end_line + 1):
                line_map[line_num] = (entity_id, class_id)

            self.function_stack.append(function_name)
            self.generic_visit(node)
            self.function_stack.pop()

    collector = EntityCollector()
    try:
        collector.visit(tree)
    except RecursionError as exc:
        logger.warning(
            "RecursionError when parsing AST for %s. Returning partial mapping. Error: %s", full_path, exc
        )
    return line_map


class ASTBasedMapper(BlockMapper):
    """AST-based mapper implementation."""

    def __init__(self, repos_root: str):
        self.repos_root = repos_root
        self._line_map_cache: dict[str, Dict[int, Tuple[str | None, Optional[str]]]] = {}

    def _get_line_map(self, file_path: str, repo_root: str):
        cache_key = f"{repo_root}:{file_path}"
        if cache_key not in self._line_map_cache:
            self._line_map_cache[cache_key] = build_line_to_entity_map(file_path, repo_root)
        return self._line_map_cache[cache_key]

    def map_blocks_to_entities(
        self,
        blocks: List[Dict[str, Any]],
        instance_id: str,
        top_k_modules: int = 10,
        top_k_entities: int = 50,
    ) -> Tuple[List[str], List[str]]:
        repo_name = instance_id_to_repo_name(instance_id)
        repo_root = os.path.join(self.repos_root, repo_name)

        blocks_by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for block in blocks:
            file_path = block.get("file_path")
            if file_path:
                blocks_by_file[file_path].append(block)

        found_modules: list[str] = []
        found_entities: list[str] = []
        seen_modules = set()
        seen_entities = set()

        for file_path, file_blocks in blocks_by_file.items():
            line_map = self._get_line_map(file_path, repo_root)
            if not line_map:
                continue

            for block in file_blocks:
                block_start = block.get("start_line", 0)
                block_end = block.get("end_line", 0)
                if block_start < 0 or block_end < 0:
                    continue

                ast_start = block_start + 1
                ast_end = block_end + 1

                block_entities = set()
                block_classes = set()
                for line_num in range(ast_start, ast_end + 1):
                    if line_num in line_map:
                        entity_id, class_id = line_map[line_num]
                        if entity_id and entity_id not in seen_entities:
                            block_entities.add(entity_id)
                        if class_id:
                            if class_id not in seen_modules:
                                block_classes.add(class_id)
                        elif entity_id:
                            if entity_id not in seen_modules:
                                block_classes.add(entity_id)

                for entity_id in sorted(block_entities):
                    if len(found_entities) >= top_k_entities:
                        break
                    found_entities.append(entity_id)
                    seen_entities.add(entity_id)

                for class_id in sorted(block_classes):
                    if len(found_modules) >= top_k_modules:
                        break
                    found_modules.append(class_id)
                    seen_modules.add(class_id)

                if len(found_entities) >= top_k_entities and len(found_modules) >= top_k_modules:
                    break

        return found_modules[:top_k_modules], found_entities[:top_k_entities]
