"""Graph-based mapper (requires graph index + networkx)."""

from __future__ import annotations

import logging
import os
import pickle
from typing import Any, Dict, List, Tuple

from minisweagent.tools.code_search.mapping.base import BlockMapper
from minisweagent.tools.code_search.utils import clean_file_path, dedupe_append, instance_id_to_repo_name

logger = logging.getLogger(__name__)

NODE_TYPE_FUNCTION = "function"
NODE_TYPE_CLASS = "class"


class RepoEntitySearcher:
    """Minimal entity searcher wrapper for graph index."""

    def __init__(self, graph):
        self.graph = graph

    def has_node(self, node_id: str) -> bool:
        return node_id in self.graph

    def get_node_data(self, node_ids: list[str]):
        return [self.graph.nodes[node_id] | {"node_id": node_id} for node_id in node_ids]


def load_graph_index(instance_id: str, graph_index_dir: str):
    try:
        import networkx  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Graph mapper requires networkx to load graph indexes") from exc
    repo_name = instance_id_to_repo_name(instance_id)
    graph_file = os.path.join(graph_index_dir, f"{repo_name}.pkl")
    if not os.path.exists(graph_file):
        logger.warning("Graph index not found: %s", graph_file)
        return None
    with open(graph_file, "rb") as handle:
        return pickle.load(handle)


def get_entity_searcher(instance_id: str, graph_index_dir: str):
    graph = load_graph_index(instance_id, graph_index_dir)
    if graph is None:
        return None
    return RepoEntitySearcher(graph)


def _module_id(entity_id: str) -> str:
    file_path, name = entity_id.split(":", 1)
    if "." in name:
        name = name.split(".")[0]
    return f"{file_path}:{name}"


class GraphBasedMapper(BlockMapper):
    """Graph index-based mapper."""

    def __init__(self, graph_index_dir: str):
        self.graph_index_dir = graph_index_dir
        self._searcher_cache: dict[str, RepoEntitySearcher] = {}

    def _get_searcher(self, instance_id: str):
        if instance_id not in self._searcher_cache:
            searcher = get_entity_searcher(instance_id, self.graph_index_dir)
            self._searcher_cache[instance_id] = searcher
        return self._searcher_cache[instance_id]

    def map_blocks_to_entities(
        self,
        blocks: List[Dict[str, Any]],
        instance_id: str,
        top_k_modules: int = 10,
        top_k_entities: int = 50,
    ) -> Tuple[List[str], List[str]]:
        repo_name = instance_id_to_repo_name(instance_id)
        searcher = self._get_searcher(instance_id)
        if searcher is None:
            return [], []

        found_modules: list[str] = []
        found_entities: list[str] = []

        for block in blocks:
            file_path = block.get("file_path")
            if not file_path:
                continue
            file_path = clean_file_path(file_path, repo_name)

            span_ids = block.get("span_ids", [])
            for span_id in span_ids:
                entity_id = f"{file_path}:{span_id}"
                if not searcher.has_node(entity_id):
                    continue
                node_data = searcher.get_node_data([entity_id])[0]
                if node_data.get("type") == NODE_TYPE_FUNCTION:
                    dedupe_append(found_entities, entity_id, top_k_entities)
                    dedupe_append(found_modules, _module_id(entity_id), top_k_modules)
                elif node_data.get("type") == NODE_TYPE_CLASS:
                    dedupe_append(found_modules, entity_id, top_k_modules)

            if len(found_modules) >= top_k_modules and len(found_entities) >= top_k_entities:
                break

        return found_modules[:top_k_modules], found_entities[:top_k_entities]
