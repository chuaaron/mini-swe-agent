"""file_radar_search tool implementation (file-level radar, no snippets)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minisweagent.tools.base import ToolResult
from minisweagent.tools.code_search.chunkers import Chunk, SlidingChunker
from minisweagent.tools.file_radar_search.radar_nav import (
    build_focused_tree,
    build_reverse_graph,
    extract_call_graph,
    find_cross_file_deps,
    format_call_relations,
)
from minisweagent.tools.list_symbols import ListSymbolsTool

_INDEX_VERSION = "radar_v1"

_EXT_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".cpp": "cpp",
    ".c": "c",
    ".hpp": "cpp",
    ".h": "c",
}

_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "with",
}


def _parse_int(value: Any, *, name: str, default: int, min_value: int, max_value: int) -> int:
    parsed = value if value is not None else default
    if isinstance(parsed, str):
        try:
            parsed = int(parsed)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
    if not isinstance(parsed, int):
        raise ValueError(f"{name} must be an integer")
    if parsed < min_value or parsed > max_value:
        raise ValueError(f"{name} must be between {min_value} and {max_value}")
    return parsed


@dataclass
class FileRadarSearchArgs:
    query: str
    queries: list[str]
    query_display: str
    queries_provided: bool
    topk_files: int = 15
    topk_blocks: int = 80
    filters: str | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "FileRadarSearchArgs":
        raw_query = raw.get("query")
        raw_queries = raw.get("queries")
        queries_provided = raw_queries is not None

        query_candidates: list[str] = []
        if isinstance(raw_queries, list | tuple):
            for item in raw_queries:
                text = str(item).strip()
                if text:
                    query_candidates.append(text)
        elif raw_queries is not None:
            text = str(raw_queries).strip()
            if text:
                query_candidates.append(text)

        if raw_query is not None:
            text = str(raw_query).strip()
            if text:
                query_candidates.append(text)

        if not query_candidates:
            raise ValueError("query or queries cannot be empty")

        queries: list[str] = []
        seen_queries: set[str] = set()
        for query in query_candidates:
            if query in seen_queries:
                continue
            seen_queries.add(query)
            queries.append(query)
        if len(queries) > 8:
            raise ValueError("queries must contain at most 8 items")
        query_display = queries[0] if len(queries) == 1 else " | ".join(queries)

        topk_files = _parse_int(
            raw.get("topk-files", raw.get("topk_files")),
            name="topk-files",
            default=15,
            min_value=1,
            max_value=100,
        )
        topk_blocks = _parse_int(
            raw.get("topk-blocks", raw.get("topk_blocks")),
            name="topk-blocks",
            default=80,
            min_value=10,
            max_value=500,
        )
        filters = raw.get("filters")
        return cls(
            query=queries[0],
            queries=queries,
            query_display=query_display,
            queries_provided=queries_provided,
            topk_files=topk_files,
            topk_blocks=topk_blocks,
            filters=str(filters) if filters else None,
        )


@dataclass
class FileRadarSearchConfig:
    chunker: str = "sliding"
    chunk_size: int = 800
    overlap: int = 200
    embedding_provider: str = "local"
    embedding_model: str = ""
    embedding_batch_size: int = 64
    embedding_max_length: int = 4096
    embedding_device: str = "cpu"
    trust_remote_code: bool = False
    index_root: str = "~/.cache/mini-swe-agent/indexes"
    max_file_size: int = 512 * 1024
    aggregation: str = "hybrid"
    # strict: include repo_fingerprint(path-sensitive) in compatibility check
    # static: ignore repo_fingerprint and focus on repo slug/commit/config compatibility
    index_validation_mode: str = "strict"
    # auto: rebuild index on miss/incompatible
    # read_only: require compatible prebuilt index, never rebuild
    index_build_policy: str = "read_only"
    # Auto attach compact skeleton for top-N radar candidates.
    auto_skeleton_enabled: bool = True
    auto_skeleton_topn: int = 3
    auto_skeleton_budget_chars: int = 4000
    auto_skeleton_max_imports_per_file: int = 6
    auto_skeleton_max_symbols_per_file: int = 14
    auto_skeleton_include_signature: bool = False
    auto_skeleton_query_aware: bool = True
    auto_query_expansion_enabled: bool = True
    auto_query_expansion_max_queries: int = 3
    # Candidate list presentation:
    # - ranked: keep retrieval order + score
    # - blind_alpha: hide score/rank; sort by path alphabetically
    # - clustered: group by directory (directory order by hidden best score)
    display_mode: str = "ranked"

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "FileRadarSearchConfig":
        return cls(**config)


class LocalEmbedder:
    def __init__(
        self,
        model_name: str,
        *,
        batch_size: int,
        max_length: int,
        device: str,
        trust_remote_code: bool,
    ):
        self.model_name = model_name
        self.batch_size = batch_size
        self.max_length = max_length
        self.device = device
        self.trust_remote_code = trust_remote_code
        self._torch = None
        self._tokenizer = None
        self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError("file_radar_search requires torch + transformers for local embeddings") from exc

        self._torch = torch
        device = self.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device

        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_name,
            trust_remote_code=self.trust_remote_code,
        )
        self._model = AutoModel.from_pretrained(
            self.model_name,
            trust_remote_code=self.trust_remote_code,
        ).to(device)
        self._model.eval()

    def embed(self, texts: list[str]):
        self._ensure_model()
        torch = self._torch
        tokenizer = self._tokenizer
        model = self._model
        device = self.device

        max_length = self.max_length

        class TextDataset(torch.utils.data.Dataset):
            def __init__(self, items: list[str]):
                self.items = items

            def __len__(self):
                return len(self.items)

            def __getitem__(self, idx: int):
                encoded = tokenizer(
                    self.items[idx],
                    truncation=True,
                    max_length=max_length,
                    padding="max_length",
                    return_tensors="pt",
                )
                return encoded["input_ids"].squeeze(0), encoded["attention_mask"].squeeze(0)

        dataset = TextDataset(texts)
        loader = torch.utils.data.DataLoader(dataset, batch_size=self.batch_size, shuffle=False, num_workers=0)

        outputs = []
        with torch.no_grad():
            for input_ids, attn_mask in loader:
                input_ids = input_ids.to(device)
                attn_mask = attn_mask.to(device)
                model_out = model(input_ids=input_ids, attention_mask=attn_mask)
                token_embeddings = model_out[0]
                sent_emb = token_embeddings[:, 0]
                sent_emb = torch.nn.functional.normalize(sent_emb, p=2, dim=1)
                outputs.append(sent_emb.cpu())
        return torch.cat(outputs, dim=0)


@dataclass
class FilterSpec:
    languages: set[str]
    paths: list[str]


def parse_filters(filters: str | None) -> FilterSpec:
    if not filters:
        return FilterSpec(languages=set(), paths=[])
    languages: set[str] = set()
    paths: list[str] = []
    for token in filters.split():
        if ":" not in token:
            continue
        key, value = token.split(":", 1)
        if not value:
            continue
        if key == "lang":
            languages.add(value.lower())
        elif key == "path":
            paths.append(value)
    return FilterSpec(languages=languages, paths=paths)


def matches_filters(meta: dict[str, Any], spec: FilterSpec) -> bool:
    language = (meta.get("language") or "").lower()
    if not language:
        file_path = str(meta.get("file_path") or "")
        language = _EXT_LANGUAGE.get(Path(file_path).suffix.lower(), "")
    if spec.languages:
        if language not in spec.languages:
            return False
    if spec.paths:
        path = meta.get("file_path", "")
        if not all(part in path for part in spec.paths):
            return False
    return True


def sanitize_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)


class FileRadarIndex:
    def __init__(self, embeddings, metadata: list[dict[str, Any]], meta: dict[str, Any]):
        self.embeddings = embeddings
        self.metadata = metadata
        self.meta = meta

    def search_blocks(self, query_vec, *, topk: int, filters: FilterSpec):
        torch = __import__("torch")
        if not self.metadata:
            return []
        indices = [idx for idx, meta in enumerate(self.metadata) if matches_filters(meta, filters)]
        if not indices:
            return []
        emb = self.embeddings[indices]
        scores = torch.matmul(emb, query_vec)
        k = min(topk, scores.numel())
        values, top_idx = torch.topk(scores, k)
        results = []
        for score, local_idx in zip(values.tolist(), top_idx.tolist()):
            meta = self.metadata[indices[local_idx]]
            results.append((float(score), meta))
        return results


class FileRadarSearchTool:
    name = "file_radar_search"
    description = "Semantic file-level radar search without returning code snippets"

    def __init__(self, config: dict[str, Any]):
        self.config = FileRadarSearchConfig.from_dict(config)
        if not self.config.embedding_model:
            raise ValueError("embedding_model must be set for file_radar_search")
        if self.config.chunker != "sliding":
            raise ValueError(f"Unsupported chunker: {self.config.chunker}")
        if self.config.aggregation not in {"hybrid", "max", "sum"}:
            raise ValueError("aggregation must be one of: hybrid, max, sum")
        if self.config.index_validation_mode not in {"strict", "static"}:
            raise ValueError("index_validation_mode must be one of: strict, static")
        if self.config.index_build_policy not in {"auto", "read_only"}:
            raise ValueError("index_build_policy must be one of: auto, read_only")
        if not 0 <= int(self.config.auto_skeleton_topn) <= 20:
            raise ValueError("auto_skeleton_topn must be between 0 and 20")
        if not 0 <= int(self.config.auto_skeleton_budget_chars) <= 20000:
            raise ValueError("auto_skeleton_budget_chars must be between 0 and 20000")
        if not 0 <= int(self.config.auto_skeleton_max_imports_per_file) <= 200:
            raise ValueError("auto_skeleton_max_imports_per_file must be between 0 and 200")
        if not 0 <= int(self.config.auto_skeleton_max_symbols_per_file) <= 500:
            raise ValueError("auto_skeleton_max_symbols_per_file must be between 0 and 500")
        if not 1 <= int(self.config.auto_query_expansion_max_queries) <= 8:
            raise ValueError("auto_query_expansion_max_queries must be between 1 and 8")
        if str(self.config.display_mode or "").strip().lower() not in {"ranked", "blind_alpha", "clustered"}:
            raise ValueError("display_mode must be one of: ranked, blind_alpha, clustered")
        self.index_root = Path(os.path.expanduser(self.config.index_root)).resolve()
        self.chunker = SlidingChunker(self.config.chunk_size, self.config.overlap)
        self.embedder = self._build_embedder()
        self.list_symbols_tool = ListSymbolsTool({"max_file_size": self.config.max_file_size})
        self._index_cache: dict[tuple[Path, str], FileRadarIndex] = {}
        self._lock = threading.Lock()

    def _auto_expand_query(self, query: str, *, max_queries: int) -> list[str]:
        normalized = " ".join(str(query).split())
        if not normalized:
            return []

        variants: list[str] = []
        seen_lower: set[str] = set()

        def _add(candidate: str) -> None:
            text = " ".join(candidate.split())
            if not text:
                return
            lowered = text.lower()
            if lowered in seen_lower:
                return
            seen_lower.add(lowered)
            variants.append(text)

        _add(normalized)
        if max_queries <= 1:
            return variants[:1]

        raw_tokens = re.findall(r"[A-Za-z0-9_./:-]+", normalized)
        code_like: list[str] = []
        seen_code: set[str] = set()
        for token in raw_tokens:
            cleaned = token.strip(".,;:()[]{}<>\"'")
            if len(cleaned) < 3:
                continue
            is_code_like = (
                "/" in cleaned
                or "." in cleaned
                or "_" in cleaned
                or "-" in cleaned
                or any(ch.isupper() for ch in cleaned[1:])
            )
            if not is_code_like:
                continue
            lowered = cleaned.lower()
            if lowered in seen_code:
                continue
            seen_code.add(lowered)
            code_like.append(cleaned)
        if code_like:
            _add(" ".join(code_like[:10]))

        if len(variants) < max_queries:
            words = re.findall(r"[A-Za-z_][A-Za-z0-9_]+", normalized)
            concept_terms: list[str] = []
            seen_terms: set[str] = set()
            for word in words:
                lowered = word.lower()
                if lowered in _QUERY_STOPWORDS:
                    continue
                if lowered in seen_terms:
                    continue
                seen_terms.add(lowered)
                concept_terms.append(word)
            if concept_terms:
                _add(" ".join(concept_terms[:12]))

        if len(variants) < max_queries and code_like:
            split_terms: list[str] = []
            seen_split: set[str] = set()
            for token in code_like:
                for part in re.split(r"[./:_-]+", token):
                    if len(part) < 3:
                        continue
                    lowered = part.lower()
                    if lowered in _QUERY_STOPWORDS:
                        continue
                    if lowered in seen_split:
                        continue
                    seen_split.add(lowered)
                    split_terms.append(part)
            if split_terms:
                _add(" ".join(split_terms[:12]))

        return variants[:max_queries]

    def _effective_queries(self, parsed: FileRadarSearchArgs) -> tuple[list[str], bool]:
        if len(parsed.queries) > 1:
            return parsed.queries, False
        if parsed.queries_provided:
            return parsed.queries, False
        if not self.config.auto_query_expansion_enabled:
            return parsed.queries, False
        expanded = self._auto_expand_query(
            parsed.queries[0],
            max_queries=max(1, int(self.config.auto_query_expansion_max_queries)),
        )
        if not expanded:
            return parsed.queries, False
        return expanded, len(expanded) > len(parsed.queries)

    def _build_embedder(self):
        provider = self.config.embedding_provider
        if provider != "local":
            raise ValueError(f"Unsupported embedding_provider: {provider}")
        return LocalEmbedder(
            self.config.embedding_model,
            batch_size=self.config.embedding_batch_size,
            max_length=self.config.embedding_max_length,
            device=self.config.embedding_device,
            trust_remote_code=self.config.trust_remote_code,
        )

    def run(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        try:
            with self._lock:
                parsed = FileRadarSearchArgs.from_raw(args)
                repo_path = Path(context["repo_path"])
                repo_dir = str(context.get("repo_dir") or "")
                repo_slug = str(context.get("repo_slug") or repo_dir)
                base_commit = str(context.get("base_commit", "HEAD"))
                repo_fingerprint = self._repo_fingerprint(repo_path)

                index, index_debug = self._get_or_build_index(
                    repo_path=repo_path,
                    repo_dir=repo_dir,
                    repo_slug=repo_slug,
                    commit=base_commit,
                    repo_fingerprint=repo_fingerprint,
                )
                filters = parse_filters(parsed.filters)
                effective_queries, query_expanded = self._effective_queries(parsed)
                query_vectors = self.embedder.embed(effective_queries)
                ranked_by_query: list[list[dict[str, Any]]] = []
                for query_vec in query_vectors:
                    blocks = index.search_blocks(query_vec, topk=parsed.topk_blocks, filters=filters)
                    ranked = self._rank_files(blocks, repo_path, repo_dir)
                    ranked_by_query.append(ranked)
                fused_ranked = self._fuse_ranked_files(ranked_by_query, query_count=len(effective_queries))
                repo_root = repo_path.resolve()
                existing_only = [item for item in fused_ranked if self._candidate_exists(repo_root, item["path"])]
                structured = existing_only[: parsed.topk_files]
                auto_skeleton_query = " ".join(effective_queries)
                auto_skeleton = self._build_auto_skeleton(query=auto_skeleton_query, repo_root=repo_root, results=structured)
                query_display = effective_queries[0] if len(effective_queries) == 1 else " | ".join(effective_queries)
                display_mode = self._normalized_display_mode()
                formatted = self._format_results(
                    query_display,
                    structured,
                    auto_skeleton=auto_skeleton,
                    display_mode=display_mode,
                )

                data = {
                    "query": parsed.query,
                    "input_queries": parsed.queries,
                    "queries": effective_queries,
                    "query_count": len(effective_queries),
                    "query_expanded": query_expanded,
                    "fusion_mode": "single_query" if len(effective_queries) == 1 else "multi_query_rrf_support",
                    "display_mode": display_mode,
                    "topk_files": parsed.topk_files,
                    "topk_blocks": parsed.topk_blocks,
                    "returned": len(structured),
                    "returned_before_exists_filter": len(fused_ranked),
                    "results": structured,
                    "metadata": index.meta,
                    "repo_slug": repo_slug,
                    "base_commit": base_commit,
                    "index_status": index_debug.get("index_status"),
                    "index_compat_reason": index_debug.get("compat_reason"),
                    "index_dir": index_debug.get("index_dir"),
                    "index_validation_mode": self.config.index_validation_mode,
                    "index_build_policy": self.config.index_build_policy,
                    "auto_skeleton_enabled": bool(auto_skeleton.get("enabled")),
                    "auto_skeleton_topn": int(auto_skeleton.get("topn", 0)),
                    "auto_skeleton_budget_chars": int(auto_skeleton.get("budget_chars", 0)),
                    "auto_skeleton_truncated": bool(auto_skeleton.get("truncated", False)),
                    "auto_skeleton_files": auto_skeleton.get("files", []),
                }
                return ToolResult(success=True, data=data, output=formatted, returncode=0)
        except ValueError:
            raise
        except Exception as exc:
            return ToolResult(success=False, data={}, output=str(exc), error=str(exc), returncode=1)

    def _normalize_result_path(self, raw_path: str, repo_path: Path, repo_dir: str) -> str:
        if not raw_path:
            return raw_path
        path = Path(raw_path)
        if not path.is_absolute():
            return path.as_posix()
        try:
            return path.relative_to(repo_path).as_posix()
        except ValueError:
            pass
        parts = path.parts
        if repo_dir in parts:
            idx = parts.index(repo_dir)
            rel_parts = parts[idx + 1 :]
            if rel_parts:
                return Path(*rel_parts).as_posix()
        return path.as_posix()

    def _aggregate_score(self, scores: list[float]) -> float:
        if not scores:
            return 0.0
        if self.config.aggregation == "max":
            return max(scores)
        if self.config.aggregation == "sum":
            return sum(scores)
        sorted_scores = sorted(scores, reverse=True)
        top3 = sorted_scores[:3]
        return 0.7 * sorted_scores[0] + 0.3 * (sum(top3) / len(top3))

    def _rank_files(self, blocks: list[tuple[float, dict[str, Any]]], repo_path: Path, repo_dir: str) -> list[dict[str, Any]]:
        by_file: dict[str, dict[str, Any]] = {}
        for score, meta in blocks:
            file_path = self._normalize_result_path(meta.get("file_path", ""), repo_path, repo_dir)
            if not file_path:
                continue
            entry = by_file.setdefault(file_path, {"scores": [], "language": meta.get("language")})
            entry["scores"].append(float(score))
            if not entry.get("language") and meta.get("language"):
                entry["language"] = meta.get("language")

        ranked: list[dict[str, Any]] = []
        for file_path, data in by_file.items():
            scores = data["scores"]
            ranked.append(
                {
                    "path": file_path,
                    "score": self._aggregate_score(scores),
                    "evidence_count": len(scores),
                    "language": data.get("language"),
                }
            )
        ranked.sort(key=lambda item: (item["score"], item["evidence_count"]), reverse=True)
        return ranked

    def _fuse_ranked_files(self, ranked_by_query: list[list[dict[str, Any]]], *, query_count: int) -> list[dict[str, Any]]:
        if not ranked_by_query:
            return []
        if query_count <= 1:
            return ranked_by_query[0]

        merged: dict[str, dict[str, Any]] = {}
        for ranked in ranked_by_query:
            seen_in_this_query: set[str] = set()
            for rank, item in enumerate(ranked, start=1):
                path = str(item.get("path") or "")
                if not path:
                    continue
                entry = merged.setdefault(
                    path,
                    {
                        "path": path,
                        "language": item.get("language"),
                        "evidence_count": 0,
                        "dense_score_sum": 0.0,
                        "dense_score_count": 0,
                        "rrf_score": 0.0,
                        "support_count": 0,
                    },
                )
                entry["evidence_count"] += int(item.get("evidence_count", 0) or 0)
                entry["dense_score_sum"] += float(item.get("score", 0.0) or 0.0)
                entry["dense_score_count"] += 1
                entry["rrf_score"] += 1.0 / (60.0 + rank)
                if path not in seen_in_this_query:
                    entry["support_count"] += 1
                    seen_in_this_query.add(path)
                if not entry.get("language") and item.get("language"):
                    entry["language"] = item.get("language")

        fused: list[dict[str, Any]] = []
        for entry in merged.values():
            support_count = int(entry["support_count"])
            support_ratio = support_count / float(query_count)
            dense_mean = (
                float(entry["dense_score_sum"]) / float(entry["dense_score_count"])
                if int(entry["dense_score_count"]) > 0
                else 0.0
            )
            dense_mean = max(0.0, min(1.0, dense_mean))
            fusion_score = 0.6 * support_ratio + 0.4 * dense_mean
            fused.append(
                {
                    "path": entry["path"],
                    "score": fusion_score,
                    "evidence_count": int(entry["evidence_count"]),
                    "language": entry.get("language"),
                    "support_count": support_count,
                    "query_count": query_count,
                    "rrf_score": float(entry["rrf_score"]),
                    "dense_mean_score": dense_mean,
                }
            )
        fused.sort(
            key=lambda item: (
                int(item.get("support_count", 0)),
                float(item.get("score", 0.0)),
                float(item.get("rrf_score", 0.0)),
                int(item.get("evidence_count", 0)),
            ),
            reverse=True,
        )
        return fused

    def _normalized_display_mode(self) -> str:
        mode = str(self.config.display_mode or "ranked").strip().lower()
        return mode if mode in {"ranked", "blind_alpha", "clustered"} else "ranked"

    def _result_dir(self, path: str) -> str:
        parent = Path(path).parent.as_posix()
        return "." if parent in {"", "."} else parent

    def _clustered_display_order(self, results: list[dict[str, Any]]) -> tuple[list[tuple[str, list[dict[str, Any]]]], list[str]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in results:
            path = str(item.get("path") or "")
            if not path:
                continue
            grouped.setdefault(self._result_dir(path), []).append(item)

        for items in grouped.values():
            items.sort(key=lambda entry: str(entry.get("path") or "").lower())

        def _dir_key(directory: str) -> tuple[float, str]:
            items = grouped.get(directory, [])
            best_score = max(float(item.get("score", 0.0) or 0.0) for item in items) if items else 0.0
            return (-best_score, directory.lower())

        ordered_dirs = sorted(grouped.keys(), key=_dir_key)
        grouped_ordered = [(directory, grouped[directory]) for directory in ordered_dirs]
        flattened_paths = [str(item.get("path") or "") for _, items in grouped_ordered for item in items if item.get("path")]
        return grouped_ordered, flattened_paths

    def _render_candidate_entry(
        self,
        item: dict[str, Any],
        *,
        index: int | None = None,
        include_score: bool,
        bullet_prefix: str = "",
    ) -> list[str]:
        path = str(item.get("path") or "")
        evidence_count = int(item.get("evidence_count", 0) or 0)
        if index is None:
            if include_score:
                score = float(item.get("score", 0.0) or 0.0)
                first = f"{bullet_prefix}- {path} (score: {score:.2f}, evidence: {evidence_count})"
            else:
                first = f"{bullet_prefix}- {path} (evidence: {evidence_count})"
        else:
            if include_score:
                score = float(item.get("score", 0.0) or 0.0)
                first = f"{bullet_prefix}{index}. {path} (score: {score:.2f}, evidence: {evidence_count})"
            else:
                first = f"{bullet_prefix}{index}. {path} (evidence: {evidence_count})"
        lines = [first]
        support_count = item.get("support_count")
        query_count = item.get("query_count")
        if isinstance(support_count, int) and isinstance(query_count, int) and query_count > 1:
            lines.append(f"{bullet_prefix}  support: {support_count}/{query_count} queries")
        return lines

    def _order_auto_skeleton_files(self, files: list[dict[str, Any]], *, display_paths: list[str]) -> list[dict[str, Any]]:
        by_path: dict[str, dict[str, Any]] = {}
        for item in files:
            path = str(item.get("path") or "")
            if path:
                by_path[path] = item
        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in display_paths:
            if path in by_path and path not in seen:
                ordered.append(by_path[path])
                seen.add(path)
        for item in files:
            path = str(item.get("path") or "")
            if not path or path in seen:
                continue
            ordered.append(item)
            seen.add(path)
        return ordered

    def _format_results(
        self,
        query: str,
        results: list[dict[str, Any]],
        *,
        auto_skeleton: dict[str, Any],
        display_mode: str | None = None,
    ) -> str:
        mode = (display_mode or self._normalized_display_mode()).strip().lower()
        if mode not in {"ranked", "blind_alpha", "clustered"}:
            mode = "ranked"

        lines = [f'Found {len(results)} candidate files for "{query}":', ""]
        display_paths: list[str] = [str(item.get("path") or "") for item in results if item.get("path")]
        if mode == "ranked":
            for idx, item in enumerate(results, start=1):
                lines.extend(self._render_candidate_entry(item, index=idx, include_score=True))
        elif mode == "blind_alpha":
            alpha_items = sorted(results, key=lambda item: str(item.get("path") or "").lower())
            display_paths = [str(item.get("path") or "") for item in alpha_items if item.get("path")]
            for item in alpha_items:
                lines.extend(self._render_candidate_entry(item, include_score=False))
        else:
            clusters, clustered_paths = self._clustered_display_order(results)
            display_paths = clustered_paths
            for directory, items in clusters:
                dir_label = "./" if directory == "." else f"{directory}/"
                lines.append(f"[DIR] {dir_label}")
                for item in items:
                    lines.extend(self._render_candidate_entry(item, include_score=False, bullet_prefix="  "))
                lines.append("")

        directory_tree = auto_skeleton.get("directory_tree", "")
        cross_deps = auto_skeleton.get("cross_file_deps", {})
        if directory_tree and mode != "clustered":
            lines.extend(["", "-- Directory Context --", directory_tree])
        if cross_deps:
            lines.extend(["", "-- Dependencies --"])
            dep_items = sorted(
                cross_deps.items(),
                key=lambda item: (-len(item[1]), str(item[0]).lower()),
            )
            max_dep_sources = 12
            max_dep_targets = 6
            for src, dsts in dep_items[:max_dep_sources]:
                shown = list(dsts)[:max_dep_targets]
                suffix = f", ... (+{len(dsts) - len(shown)})" if len(dsts) > len(shown) else ""
                lines.append(f"  {src} -> imports -> {', '.join(shown)}{suffix}")
            if len(dep_items) > max_dep_sources:
                lines.append(f"  ... ({len(dep_items) - max_dep_sources} more dependency sources)")

        files = auto_skeleton.get("files", [])
        if isinstance(files, list) and files:
            files = self._order_auto_skeleton_files(files, display_paths=display_paths)
        if auto_skeleton.get("enabled") and files:
            if mode == "ranked":
                lines.extend(["", f"Auto skeleton (Top-{len(files)}, balanced folded, no code body):"])
            else:
                lines.extend(["", f"Auto skeleton ({len(files)} candidates, balanced folded, no code body):"])
            for item in files:
                if mode == "ranked":
                    lines.append(f"[{item['rank']}] {item['path']}")
                else:
                    lines.append(f"* {item['path']}")
                cg = item.get("call_graph", {})
                rg = item.get("reverse_graph", {})
                lines.append("[ANCHORS]")
                anchor_items = item.get("anchors_items", [])
                anchor_names = item.get("anchor_names", [])
                if isinstance(anchor_items, list) and anchor_items:
                    for i, text in enumerate(anchor_items):
                        lines.append(f"    - {text}")
                        if i < len(anchor_names) and anchor_names[i]:
                            rel = format_call_relations(anchor_names[i], cg, rg)
                            if rel:
                                lines.append(f"      {rel}")
                else:
                    anchors_preview = item.get("anchors_preview", "")
                    lines.append(f"    - {anchors_preview or '-'}")

                lines.append("[GLIMPSE]")
                glimpse_items = item.get("context_glimpse_items", [])
                glimpse_names = item.get("context_glimpse_names", [])
                if isinstance(glimpse_items, list) and glimpse_items:
                    for i, text in enumerate(glimpse_items):
                        lines.append(f"    - {text}")
                        if i < len(glimpse_names) and glimpse_names[i]:
                            rel = format_call_relations(glimpse_names[i], cg, rg)
                            if rel:
                                lines.append(f"      {rel}")
                else:
                    context_glimpse_preview = item.get("context_glimpse_preview", "")
                    lines.append(f"    - {context_glimpse_preview or '-'}")
                lines.append(
                    "[FOLDED] "
                    f"symbols={int(item.get('folded_symbols_count', 0))}, "
                    f"imports={int(item.get('folded_imports_count', 0))} "
                    f"(use `@tool list_symbols --file \"{item['path']}\"` to expand)"
                )
                primary_anchor = item.get("primary_anchor", {})
                if isinstance(primary_anchor, dict) and primary_anchor.get("start") and primary_anchor.get("end"):
                    start = int(primary_anchor["start"])
                    end = int(primary_anchor["end"])
                    lines.append(f"=> NEXT: sed -n '{start},{end}p' {item['path']}")
                else:
                    lines.append(f"=> NEXT: @tool list_symbols --file \"{item['path']}\"")
                if item.get("error"):
                    lines.append(f"skeleton_error: {item['error']}")
                lines.append("")

            lines.extend(
                [
                    "Next-Step Playbook:",
                    "1) Anchor First: If you see [ANCHORS], read that exact range first with `sed -n 'Lstart,Lendp' <path>`.",
                    "2) Expand When Needed: If anchors are '-' or still ambiguous, call `@tool list_symbols --file <path>` and then read one chosen range with `sed`.",
                    (
                        f"3) Re-query If Needed: If Top-{len(files)} still looks wrong, refine the query and call `file_radar_search` again."
                        if mode == "ranked"
                        else "3) Re-query If Needed: If these candidates still look wrong, refine the query and call `file_radar_search` again."
                    ),
                    "",
                    "Tip: Final submission is safer after the target file has been inspected via bash read or list_symbols expansion.",
                ]
            )
        return "\n".join(lines).strip()

    def _build_auto_skeleton(
        self,
        *,
        query: str,
        repo_root: Path,
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        topn = int(self.config.auto_skeleton_topn)
        budget_chars = int(self.config.auto_skeleton_budget_chars)
        if not self.config.auto_skeleton_enabled or topn <= 0 or budget_chars <= 0:
            return {
                "enabled": bool(self.config.auto_skeleton_enabled),
                "topn": topn,
                "budget_chars": budget_chars,
                "files": [],
                "truncated": False,
            }

        selected = results[:topn]
        if not selected:
            return {
                "enabled": True,
                "topn": topn,
                "budget_chars": budget_chars,
                "files": [],
                "truncated": False,
            }

        query_tokens = self._query_tokens(query) if self.config.auto_skeleton_query_aware else set()
        files: list[dict[str, Any]] = []
        any_truncated = False

        import_limit = max(0, int(self.config.auto_skeleton_max_imports_per_file))
        symbol_limit = max(1, int(self.config.auto_skeleton_max_symbols_per_file))
        raw_import_limit = min(1000, max(200, import_limit * 10)) if import_limit > 0 else 0
        raw_symbol_limit = min(2000, max(200, symbol_limit * 10))
        anchor_limit = min(2, symbol_limit)
        for idx, candidate in enumerate(selected, start=1):
            path = str(candidate.get("path") or "").strip()
            base = {
                "rank": idx,
                "path": path,
                "score": float(candidate.get("score", 0.0) or 0.0),
                "evidence_count": int(candidate.get("evidence_count", 0) or 0),
                "budget_chars": int(budget_chars),
                "anchors_preview": "",
                "anchors_items": [],
                "context_glimpse_preview": "",
                "context_glimpse_items": [],
                "query_hits_preview": "",
                "primary_anchor": {},
                "anchor_count": 0,
                "context_glimpse_count": 0,
                "folded_imports_count": 0,
                "folded_symbols_count": 0,
                "import_count": 0,
                "symbol_count": 0,
                "error": "",
            }
            if not path:
                base["error"] = "empty_path"
                files.append(base)
                continue

            skeleton = self.list_symbols_tool.run(
                {
                    "file": path,
                    "include-signature": bool(self.config.auto_skeleton_include_signature),
                    "max-imports": raw_import_limit,
                    "max-symbols": raw_symbol_limit,
                },
                {"repo_path": str(repo_root), "allowed_files": [path]},
            )
            if not skeleton.success:
                base["error"] = str(skeleton.error or skeleton.output)
                files.append(base)
                continue

            data = skeleton.data if isinstance(skeleton.data, dict) else {}
            imports = [str(item.get("text") or "").strip() for item in data.get("imports", []) if item.get("text")]
            symbols = [item for item in data.get("symbols", []) if isinstance(item, dict) and item.get("name")]
            base["import_count"] = len(imports)
            base["symbol_count"] = len(symbols)

            call_graph: dict[str, list[str]] = {}
            full_path = repo_root / path
            if full_path.exists() and full_path.suffix == ".py":
                try:
                    call_graph = extract_call_graph(full_path.read_text(encoding="utf-8", errors="replace"))
                except (OSError, SyntaxError):
                    pass
            base["call_graph"] = call_graph
            base["reverse_graph"] = build_reverse_graph(call_graph)

            if query_tokens:
                symbols.sort(key=lambda item: self._symbol_rank_key(item, query_tokens), reverse=True)

            if query_tokens:
                matched_symbols = [symbol for symbol in symbols if self._is_symbol_match(symbol, query_tokens)]
            else:
                matched_symbols = []

            anchors = matched_symbols[:anchor_limit]
            anchor_texts = [self._format_symbol_preview(symbol, include_doc=False) for symbol in anchors]
            base["anchors_preview"] = ", ".join(anchor_texts)
            base["anchors_items"] = anchor_texts
            base["query_hits_preview"] = base["anchors_preview"]
            base["anchor_count"] = len(anchors)
            base["anchor_names"] = [str(s.get("name", "")) for s in anchors]
            if anchors:
                base["primary_anchor"] = {
                    "name": str(anchors[0].get("name") or ""),
                    "kind": str(anchors[0].get("kind") or ""),
                    "start": int(anchors[0].get("start", 0) or 0),
                    "end": int(anchors[0].get("end", 0) or 0),
                }
            # Elastic folding:
            # - keep query anchors
            # - return a tiny representative glimpse of non-anchor symbols to preserve semantic "smell"
            if idx == 1 and not anchors:
                context_limit = min(5, symbol_limit)
            else:
                context_limit = min(3, symbol_limit)
            glimpses = self._select_context_glimpses(
                symbols=symbols,
                anchors=anchors,
                query_tokens=query_tokens,
                limit=context_limit,
            )
            glimpse_texts = [self._format_symbol_preview(symbol, include_doc=True) for symbol in glimpses]
            base["context_glimpse_preview"] = ", ".join(glimpse_texts)
            base["context_glimpse_items"] = glimpse_texts
            base["context_glimpse_count"] = len(glimpses)
            base["context_glimpse_names"] = [str(s.get("name", "")) for s in glimpses]
            base["folded_imports_count"] = len(imports)
            base["folded_symbols_count"] = max(0, len(symbols) - len(anchors) - len(glimpses))
            files.append(base)

        file_paths = [f["path"] for f in files if f.get("path")]
        all_candidate_paths = [
            str(item.get("path") or "").strip()
            for item in results
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
        cross_file_deps = find_cross_file_deps(repo_root, all_candidate_paths) if len(all_candidate_paths) > 1 else {}
        directory_tree = build_focused_tree(file_paths) if file_paths else ""

        return {
            "enabled": True,
            "topn": topn,
            "budget_chars": budget_chars,
            "files": files,
            "truncated": any_truncated,
            "directory_tree": directory_tree,
            "cross_file_deps": cross_file_deps,
        }

    def _select_context_glimpses(
        self,
        *,
        symbols: list[dict[str, Any]],
        anchors: list[dict[str, Any]],
        query_tokens: set[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        anchor_keys = {
            (
                str(symbol.get("name") or ""),
                int(symbol.get("start", 0) or 0),
                int(symbol.get("end", 0) or 0),
                str(symbol.get("kind") or ""),
            )
            for symbol in anchors
        }
        remaining = []
        for symbol in symbols:
            key = (
                str(symbol.get("name") or ""),
                int(symbol.get("start", 0) or 0),
                int(symbol.get("end", 0) or 0),
                str(symbol.get("kind") or ""),
            )
            if key in anchor_keys:
                continue
            remaining.append(symbol)
        if not remaining:
            return []

        ranked = sorted(remaining, key=lambda item: self._symbol_rank_key(item, query_tokens), reverse=True)
        selected: list[dict[str, Any]] = []
        seen_kinds: set[str] = set()
        # First pass: diverse kinds for broader semantic cues.
        for symbol in ranked:
            kind = str(symbol.get("kind") or "symbol")
            if kind in seen_kinds:
                continue
            selected.append(symbol)
            seen_kinds.add(kind)
            if len(selected) >= limit:
                return selected
        # Second pass: fill by rank.
        selected_keys = {
            (
                str(symbol.get("name") or ""),
                int(symbol.get("start", 0) or 0),
                int(symbol.get("end", 0) or 0),
                str(symbol.get("kind") or ""),
            )
            for symbol in selected
        }
        for symbol in ranked:
            key = (
                str(symbol.get("name") or ""),
                int(symbol.get("start", 0) or 0),
                int(symbol.get("end", 0) or 0),
                str(symbol.get("kind") or ""),
            )
            if key in selected_keys:
                continue
            selected.append(symbol)
            if len(selected) >= limit:
                break
        return selected[:limit]

    def _allocate_auto_skeleton_budgets(self, n_files: int, total_budget: int) -> list[int]:
        if n_files <= 0:
            return []
        if n_files == 1:
            return [total_budget]
        if n_files == 2:
            first = int(total_budget * 0.6)
            return [first, total_budget - first]

        if n_files == 3:
            first = int(total_budget * 0.5)
            second = int(total_budget * 0.3)
            return [first, second, total_budget - first - second]

        # Keep top-3 priority, distribute the remainder uniformly.
        first = int(total_budget * 0.5)
        second = int(total_budget * 0.3)
        third = int(total_budget * 0.15)
        remaining = max(0, total_budget - first - second - third)
        tail_count = n_files - 3
        per_tail = remaining // tail_count if tail_count else 0
        budgets = [first, second, third] + [per_tail] * tail_count
        diff = total_budget - sum(budgets)
        if diff:
            budgets[-1] += diff
        return budgets

    def _query_tokens(self, query: str) -> set[str]:
        return {token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", query)}

    def _symbol_rank_key(self, symbol: dict[str, Any], query_tokens: set[str]) -> tuple[int, int, int]:
        name = str(symbol.get("name") or "")
        lowered_name = name.lower()
        overlap = sum(1 for token in query_tokens if token in lowered_name)
        kind = str(symbol.get("kind") or "")
        kind_priority = {"class": 4, "function": 3, "method": 2}.get(kind, 1)
        span = max(0, int(symbol.get("end", 0) or 0) - int(symbol.get("start", 0) or 0))
        return overlap, kind_priority, span

    def _is_symbol_match(self, symbol: dict[str, Any], query_tokens: set[str]) -> bool:
        if not query_tokens:
            return False
        name = str(symbol.get("name") or "").lower()
        return any(token in name for token in query_tokens)

    def _format_symbol_preview(self, symbol: dict[str, Any], *, include_doc: bool = False) -> str:
        name = self._clean_preview_text(str(symbol.get("name") or ""))
        kind = self._clean_preview_text(str(symbol.get("kind") or "symbol"))
        start = int(symbol.get("start", 0) or 0)
        end = int(symbol.get("end", 0) or 0)
        base = f"{name}({kind})[L{start}-L{end}]"
        if self.config.auto_skeleton_include_signature:
            signature = self._clean_preview_text(str(symbol.get("signature") or ""))
            if signature:
                base = f"{base}:{signature}"
        if include_doc:
            doc = self._clean_preview_text(str(symbol.get("doc_first_sentence") or ""))
            if doc:
                base = f"{base}: {doc}"
        max_len = 180 if include_doc else 140
        if len(base) > max_len:
            return base[: max_len - 3] + "..."
        return base

    def _join_with_budget(self, items: list[str], budget_chars: int) -> tuple[str, int]:
        if budget_chars <= 0 or not items:
            return "", 0
        selected: list[str] = []
        current = 0
        for item in items:
            if not item:
                continue
            extra = len(item) + (2 if selected else 0)
            if current + extra > budget_chars:
                break
            selected.append(item)
            current += extra
        return ", ".join(selected), len(selected)

    def _clean_preview_text(self, text: str) -> str:
        return " ".join(text.split())

    def _candidate_exists(self, repo_root: Path, relative_path: str) -> bool:
        if not relative_path:
            return False
        candidate = (repo_root / relative_path).resolve()
        try:
            candidate.relative_to(repo_root)
        except ValueError:
            return False
        return candidate.is_file()

    def _repo_fingerprint(self, repo_path: Path) -> str:
        resolved = str(repo_path.resolve())
        return hashlib.sha1(resolved.encode("utf-8")).hexdigest()

    def _load_meta(self, meta_path: Path) -> dict[str, Any]:
        if not meta_path.exists():
            return {}
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _check_meta_compatibility(
        self,
        meta: dict[str, Any],
        *,
        repo_dir: str,
        repo_slug: str,
        commit: str,
        repo_fingerprint: str,
    ) -> tuple[bool, str]:
        if not meta:
            if self.config.index_validation_mode == "static":
                return True, "legacy_no_meta"
            return False, "meta_missing"
        if self.config.index_validation_mode == "strict" and str(meta.get("index_version") or "") != _INDEX_VERSION:
            return False, "index_version_mismatch"
        meta_repo_dir = str(meta.get("repo_dir") or "")
        if meta_repo_dir and meta_repo_dir != repo_dir:
            return False, "repo_dir_mismatch"
        meta_repo_slug = str(meta.get("repo_slug") or "")
        if meta_repo_slug and meta_repo_slug != repo_slug:
            return False, "repo_slug_mismatch"
        if self.config.index_validation_mode == "strict":
            if str(meta.get("repo_fingerprint") or "") != repo_fingerprint:
                return False, "repo_fingerprint_mismatch"
            base_commit = str(meta.get("base_commit") or "")
            if not base_commit:
                return False, "base_commit_missing"
            if base_commit != commit:
                return False, "base_commit_mismatch"
        meta_provider = str(meta.get("embedding_provider") or "")
        if meta_provider and meta_provider != self.config.embedding_provider:
            return False, "embedding_provider_mismatch"
        meta_model = str(meta.get("embedding_model") or "")
        if meta_model and meta_model != self.config.embedding_model:
            return False, "embedding_model_mismatch"
        meta_chunker = str(meta.get("chunker") or "")
        if meta_chunker and meta_chunker != self.config.chunker:
            return False, "chunker_mismatch"
        if "chunk_size" in meta or "overlap" in meta:
            try:
                chunk_size = int(meta.get("chunk_size"))
                overlap = int(meta.get("overlap"))
            except (TypeError, ValueError):
                return False, "chunk_params_invalid"
            if chunk_size != self.config.chunk_size or overlap != self.config.overlap:
                return False, "chunk_params_mismatch"
        if self.config.index_validation_mode == "strict" and str(meta.get("aggregation") or "") != self.config.aggregation:
            return False, "aggregation_mismatch"
        return True, "ok"

    def _read_only_error(self, *, index_dir: Path, reason: str, repo_slug: str, commit: str) -> RuntimeError:
        message = (
            "file_radar_search index reuse required "
            "(index_build_policy=read_only), but no compatible prebuilt index is available.\n"
            f"reason={reason}\n"
            f"repo_slug={repo_slug}\n"
            f"base_commit={commit}\n"
            f"index_dir={index_dir}\n"
            "Set index_build_policy=auto to allow rebuilding, or prebuild a compatible index."
        )
        return RuntimeError(message)

    def _get_or_build_index(
        self,
        *,
        repo_path: Path,
        repo_dir: str,
        repo_slug: str,
        commit: str,
        repo_fingerprint: str,
    ) -> tuple[FileRadarIndex, dict[str, Any]]:
        embedder_id = sanitize_id(f"{self.config.embedding_provider}_{self.config.embedding_model}")
        safe_repo = sanitize_id(repo_slug or repo_dir)
        safe_commit = sanitize_id(commit)
        roots: list[Path] = [self.index_root]
        dense_child = self.index_root / "dense_index_llamaindex_code"
        if dense_child.exists():
            roots.insert(0, dense_child)
        resolved_roots: list[Path] = []
        seen_roots: set[Path] = set()
        for root in roots:
            resolved = root.resolve()
            if resolved in seen_roots:
                continue
            seen_roots.add(resolved)
            resolved_roots.append(resolved)

        primary_root = resolved_roots[0] if resolved_roots else self.index_root
        index_dir = primary_root / _INDEX_VERSION / safe_repo / safe_commit / embedder_id
        candidates: list[Path] = []
        for root in resolved_roots:
            candidates.extend(
                [
                    root / _INDEX_VERSION / safe_repo / safe_commit / embedder_id,
                    root / _INDEX_VERSION / safe_repo / embedder_id,
                    root / safe_repo / safe_commit / embedder_id,
                    root / safe_repo,
                ]
            )
            if repo_dir and sanitize_id(repo_dir) != safe_repo:
                repo_dir_safe = sanitize_id(repo_dir)
                candidates.extend(
                    [
                        root / _INDEX_VERSION / repo_dir_safe / safe_commit / embedder_id,
                        root / _INDEX_VERSION / repo_dir_safe / embedder_id,
                        root / repo_dir_safe / safe_commit / embedder_id,
                        root / repo_dir_safe,
                    ]
                )
        candidate_dirs: list[Path] = []
        seen_dirs: set[Path] = set()
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen_dirs:
                continue
            seen_dirs.add(resolved)
            candidate_dirs.append(resolved)
        diagnostics: dict[str, Any] = {
            "index_dir": str(index_dir),
            "index_status": "",
            "compat_reason": "",
        }
        for candidate in candidate_dirs:
            cache_key = (candidate, commit)
            if cache_key in self._index_cache:
                diagnostics["index_status"] = "cache_hit"
                diagnostics["compat_reason"] = "cached"
                diagnostics["index_dir"] = str(candidate)
                return self._index_cache[cache_key], diagnostics

        mismatch_reasons: list[tuple[Path, str]] = []
        for candidate in candidate_dirs:
            embeddings_path = candidate / "embeddings.pt"
            metadata_path = candidate / "metadata.jsonl"
            meta_path = candidate / "meta.json"
            if not (embeddings_path.exists() and metadata_path.exists()):
                continue
            meta = self._load_meta(meta_path)
            compatible, reason = self._check_meta_compatibility(
                meta,
                repo_dir=repo_dir,
                repo_slug=repo_slug,
                commit=commit,
                repo_fingerprint=repo_fingerprint,
            )
            diagnostics["compat_reason"] = reason
            if compatible:
                index = self._load_index(
                    embeddings_path,
                    metadata_path,
                    meta_path,
                    repo_dir,
                    repo_slug,
                    commit,
                    repo_fingerprint,
                    meta=meta,
                )
                cache_key = (candidate, commit)
                self._index_cache[cache_key] = index
                diagnostics["index_status"] = "disk_hit"
                diagnostics["index_dir"] = str(candidate)
                return index, diagnostics
            mismatch_reasons.append((candidate, reason))

        if self.config.index_build_policy == "read_only":
            if mismatch_reasons:
                candidate, reason = mismatch_reasons[0]
                raise self._read_only_error(index_dir=candidate, reason=reason, repo_slug=repo_slug, commit=commit)
            diagnostics["compat_reason"] = "index_missing"
            raise self._read_only_error(
                index_dir=index_dir,
                reason=diagnostics["compat_reason"],
                repo_slug=repo_slug,
                commit=commit,
            )

        embeddings_path = index_dir / "embeddings.pt"
        metadata_path = index_dir / "metadata.jsonl"
        meta_path = index_dir / "meta.json"
        index_dir.mkdir(parents=True, exist_ok=True)
        index = self._build_index(
            repo_path=repo_path,
            repo_dir=repo_dir,
            repo_slug=repo_slug,
            commit=commit,
            repo_fingerprint=repo_fingerprint,
            embeddings_path=embeddings_path,
            metadata_path=metadata_path,
            meta_path=meta_path,
        )
        self._index_cache[(index_dir, commit)] = index
        diagnostics["index_status"] = "rebuilt"
        diagnostics["compat_reason"] = mismatch_reasons[0][1] if mismatch_reasons else "index_missing"
        return index, diagnostics

    def _build_index(
        self,
        repo_path: Path,
        repo_dir: str,
        repo_slug: str,
        commit: str,
        repo_fingerprint: str,
        embeddings_path: Path,
        metadata_path: Path,
        meta_path: Path,
    ) -> FileRadarIndex:
        chunks = self._collect_chunks(repo_path)
        if not chunks:
            torch = __import__("torch")
            embeddings = torch.empty((0, 0))
            metadata = []
        else:
            texts = [chunk.text for chunk in chunks]
            embeddings = self.embedder.embed(texts)
            metadata = [self._chunk_to_meta(chunk) for chunk in chunks]

        self._save_index(
            embeddings=embeddings,
            metadata=metadata,
            embeddings_path=embeddings_path,
            metadata_path=metadata_path,
            meta_path=meta_path,
            repo_dir=repo_dir,
            repo_slug=repo_slug,
            commit=commit,
            repo_fingerprint=repo_fingerprint,
        )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return FileRadarIndex(embeddings, metadata, meta)

    def _collect_chunks(self, repo_path: Path) -> list[Chunk]:
        chunks: list[Chunk] = []
        for path in self._iter_repo_files(repo_path):
            language = _EXT_LANGUAGE.get(path.suffix.lower())
            if language is None:
                continue
            text = self._read_file(path)
            if text is None:
                continue
            rel_path = path.relative_to(repo_path).as_posix()
            chunks.extend(self.chunker.chunk_file(rel_path, text, language))
        return chunks

    def _iter_repo_files(self, repo_path: Path) -> list[Path]:
        git_dir = repo_path / ".git"
        if git_dir.exists():
            try:
                result = subprocess.run(
                    ["git", "-C", str(repo_path), "ls-files"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                files = [repo_path / line for line in result.stdout.splitlines() if line.strip()]
                return [path for path in files if path.is_file()]
            except subprocess.SubprocessError:
                pass
        return [path for path in repo_path.rglob("*") if path.is_file()]

    def _read_file(self, path: Path) -> str | None:
        try:
            if path.stat().st_size > self.config.max_file_size:
                return None
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def _chunk_to_meta(self, chunk: Chunk) -> dict[str, Any]:
        return {
            "file_path": chunk.path,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "language": chunk.language,
        }

    def _save_index(
        self,
        embeddings,
        metadata: list[dict[str, Any]],
        embeddings_path: Path,
        metadata_path: Path,
        meta_path: Path,
        repo_dir: str,
        repo_slug: str,
        commit: str,
        repo_fingerprint: str,
    ) -> None:
        torch = __import__("torch")
        torch.save(embeddings, embeddings_path)
        with metadata_path.open("w", encoding="utf-8") as handle:
            for item in metadata:
                handle.write(json.dumps(item, ensure_ascii=True) + "\n")
        meta = {
            "index_version": _INDEX_VERSION,
            "repo_dir": repo_dir,
            "repo_slug": repo_slug,
            "base_commit": commit,
            "repo_fingerprint": repo_fingerprint,
            "embedding_provider": self.config.embedding_provider,
            "embedding_model": self.config.embedding_model,
            "chunker": self.config.chunker,
            "chunk_size": self.config.chunk_size,
            "overlap": self.config.overlap,
            "aggregation": self.config.aggregation,
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def _load_index(
        self,
        embeddings_path: Path,
        metadata_path: Path,
        meta_path: Path,
        repo_dir: str,
        repo_slug: str,
        commit: str,
        repo_fingerprint: str,
        *,
        meta: dict[str, Any] | None = None,
    ) -> FileRadarIndex:
        torch = __import__("torch")
        embeddings = torch.load(embeddings_path, map_location="cpu")
        metadata = []
        with metadata_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                metadata.append(json.loads(line))
        if meta is None:
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            else:
                meta = {
                    "index_version": _INDEX_VERSION,
                    "repo_dir": repo_dir,
                    "repo_slug": repo_slug,
                    "base_commit": commit,
                    "repo_fingerprint": repo_fingerprint,
                    "embedding_provider": self.config.embedding_provider,
                    "embedding_model": self.config.embedding_model,
                    "chunker": self.config.chunker,
                    "chunk_size": self.config.chunk_size,
                    "overlap": self.config.overlap,
                    "aggregation": self.config.aggregation,
                }
        if not meta:
            meta = {
                "index_version": _INDEX_VERSION,
                "repo_dir": repo_dir,
                "repo_slug": repo_slug,
                "base_commit": commit,
                "repo_fingerprint": repo_fingerprint,
                "embedding_provider": self.config.embedding_provider,
                "embedding_model": self.config.embedding_model,
                "chunker": self.config.chunker,
                "chunk_size": self.config.chunk_size,
                "overlap": self.config.overlap,
                "aggregation": self.config.aggregation,
            }
        return FileRadarIndex(embeddings, metadata, meta)
