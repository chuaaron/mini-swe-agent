"""file_radar_search tool implementation (file-level radar, no snippets)."""

from __future__ import annotations

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
    topk_files: int = 15
    topk_blocks: int = 80
    filters: str | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "FileRadarSearchArgs":
        query = raw.get("query")
        if not query or not str(query).strip():
            raise ValueError("query cannot be empty")
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
            query=str(query).strip(),
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
    if spec.languages:
        if (meta.get("language") or "").lower() not in spec.languages:
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
        self.index_root = Path(os.path.expanduser(self.config.index_root)).resolve()
        self.chunker = SlidingChunker(self.config.chunk_size, self.config.overlap)
        self.embedder = self._build_embedder()
        self._index_cache: dict[Path, FileRadarIndex] = {}
        self._lock = threading.Lock()

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
                repo_dir = context["repo_dir"]
                base_commit = context.get("base_commit", "HEAD")

                index = self._get_or_build_index(repo_path, repo_dir, base_commit)
                query_vec = self.embedder.embed([parsed.query])[0]
                filters = parse_filters(parsed.filters)
                blocks = index.search_blocks(query_vec, topk=parsed.topk_blocks, filters=filters)
                structured = self._rank_files(blocks, repo_path, repo_dir)[: parsed.topk_files]
                formatted = self._format_results(parsed.query, structured)

                data = {
                    "query": parsed.query,
                    "topk_files": parsed.topk_files,
                    "topk_blocks": parsed.topk_blocks,
                    "returned": len(structured),
                    "results": structured,
                    "metadata": index.meta,
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

    def _format_results(self, query: str, results: list[dict[str, Any]]) -> str:
        lines = [f'Found {len(results)} candidate files for "{query}":', ""]
        for idx, item in enumerate(results, start=1):
            lines.append(
                f"{idx}. {item['path']} (score: {item['score']:.2f}, evidence: {item['evidence_count']})"
            )
        return "\n".join(lines).strip()

    def _get_or_build_index(self, repo_path: Path, repo_dir: str, commit: str) -> FileRadarIndex:
        embedder_id = sanitize_id(f"{self.config.embedding_provider}_{self.config.embedding_model}")
        index_dir = self.index_root / _INDEX_VERSION / repo_dir / commit[:8] / embedder_id
        if index_dir in self._index_cache:
            return self._index_cache[index_dir]

        embeddings_path = index_dir / "embeddings.pt"
        metadata_path = index_dir / "metadata.jsonl"
        meta_path = index_dir / "meta.json"
        if embeddings_path.exists() and metadata_path.exists():
            index = self._load_index(embeddings_path, metadata_path, meta_path, repo_dir, commit)
            self._index_cache[index_dir] = index
            return index

        index_dir.mkdir(parents=True, exist_ok=True)
        index = self._build_index(repo_path, repo_dir, commit, embeddings_path, metadata_path, meta_path)
        self._index_cache[index_dir] = index
        return index

    def _build_index(
        self,
        repo_path: Path,
        repo_dir: str,
        commit: str,
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

        self._save_index(embeddings, metadata, embeddings_path, metadata_path, meta_path, repo_dir, commit)
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
        commit: str,
    ) -> None:
        torch = __import__("torch")
        torch.save(embeddings, embeddings_path)
        with metadata_path.open("w", encoding="utf-8") as handle:
            for item in metadata:
                handle.write(json.dumps(item, ensure_ascii=True) + "\n")
        meta = {
            "index_version": _INDEX_VERSION,
            "repo_dir": repo_dir,
            "base_commit": commit,
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
        commit: str,
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
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            meta = {
                "index_version": _INDEX_VERSION,
                "repo_dir": repo_dir,
                "base_commit": commit,
                "embedding_provider": self.config.embedding_provider,
                "embedding_model": self.config.embedding_model,
                "chunker": self.config.chunker,
                "chunk_size": self.config.chunk_size,
                "overlap": self.config.overlap,
                "aggregation": self.config.aggregation,
            }
        return FileRadarIndex(embeddings, metadata, meta)
