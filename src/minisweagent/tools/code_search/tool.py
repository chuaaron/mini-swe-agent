"""code_search tool implementation."""

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

_INDEX_VERSION = "v1"

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


@dataclass
class CodeSearchArgs:
    query: str
    topk: int = 15
    filters: str | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "CodeSearchArgs":
        query = raw.get("query")
        topk = raw.get("topk", 15)
        filters = raw.get("filters")
        if isinstance(topk, str):
            try:
                topk = int(topk)
            except ValueError as exc:
                raise ValueError("topk must be an integer") from exc
        if not isinstance(topk, int):
            raise ValueError("topk must be an integer")
        if topk < 1 or topk > 100:
            raise ValueError("topk must be between 1 and 100")
        if not query or not str(query).strip():
            raise ValueError("query cannot be empty")
        return cls(query=str(query).strip(), topk=topk, filters=str(filters) if filters else None)


@dataclass
class CodeSearchConfig:
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

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "CodeSearchConfig":
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
            raise RuntimeError("code_search requires torch + transformers for local embeddings") from exc

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


class CodeSearchIndex:
    def __init__(self, embeddings, metadata: list[dict[str, Any]], meta: dict[str, Any]):
        self.embeddings = embeddings
        self.metadata = metadata
        self.meta = meta

    def search(self, query_vec, *, topk: int, filters: FilterSpec):
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
        for rank, (score, local_idx) in enumerate(zip(values.tolist(), top_idx.tolist()), start=1):
            meta = self.metadata[indices[local_idx]]
            results.append((score, meta))
        return results


class CodeSearchTool:
    name = "code_search"
    description = "Semantic code search using embeddings"

    def __init__(self, config: dict[str, Any]):
        self.config = CodeSearchConfig.from_dict(config)
        if not self.config.embedding_model:
            raise ValueError("embedding_model must be set for code_search")
        if self.config.chunker != "sliding":
            raise ValueError(f"Unsupported chunker: {self.config.chunker}")
        self.index_root = Path(os.path.expanduser(self.config.index_root)).resolve()
        self.chunker = SlidingChunker(self.config.chunk_size, self.config.overlap)
        self.embedder = self._build_embedder()
        self._index_cache: dict[Path, CodeSearchIndex] = {}
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
                parsed = CodeSearchArgs.from_raw(args)
                repo_path = Path(context["repo_path"])
                repo_dir = context["repo_dir"]
                base_commit = context.get("base_commit", "HEAD")

                index = self._get_or_build_index(repo_path, repo_dir, base_commit)
                query_vec = self.embedder.embed([parsed.query])[0]
                filters = parse_filters(parsed.filters)
                results = index.search(query_vec, topk=parsed.topk, filters=filters)

                formatted, structured = self._format_results(results, parsed.query, repo_path, repo_dir)
                data = {
                    "query": parsed.query,
                    "topk": parsed.topk,
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

    def _format_results(
        self,
        results: list[tuple[float, dict[str, Any]]],
        query: str,
        repo_path: Path,
        repo_dir: str,
    ):
        lines = [f"Found {len(results)} relevant files for \"{query}\":\n"]
        structured = []
        for idx, (score, meta) in enumerate(results, start=1):
            line_span = {
                "start": meta["start_line"] + 1,
                "end": meta["end_line"] + 1,
            }
            symbol = meta.get("symbol") or "-"
            snippet_lines = (meta.get("snippet") or "").splitlines()
            snippet_preview = "\n".join(snippet_lines[:3])
            display_path = self._normalize_result_path(meta.get("file_path", ""), repo_path, repo_dir)
            if not display_path:
                display_path = meta.get("file_path", "")
            lines.append(f"{idx}. {display_path} (score: {score:.2f})")
            lines.append(f"   Lines {line_span['start']}-{line_span['end']} | {symbol}")
            if snippet_preview:
                lines.append("   +----------------------------------------+")
                for line in snippet_preview.splitlines():
                    lines.append(f"   | {line[:38]:<38} |")
                lines.append("   +----------------------------------------+")
            lines.append("")
            structured.append(
                {
                    "path": display_path,
                    "score": score,
                    "line_span": line_span,
                    "symbol": meta.get("symbol"),
                    "snippet": meta.get("snippet") or "",
                    "language": meta.get("language"),
                }
            )
        return "\n".join(lines).strip(), structured

    def _get_or_build_index(self, repo_path: Path, repo_dir: str, commit: str) -> CodeSearchIndex:
        embedder_id = sanitize_id(f"{self.config.embedding_provider}_{self.config.embedding_model}")
        index_dir = self.index_root / repo_dir / commit[:8] / embedder_id
        legacy_dir = self.index_root / repo_dir

        index_dirs = [index_dir, legacy_dir]
        for candidate in index_dirs:
            if candidate in self._index_cache:
                return self._index_cache[candidate]

        for candidate in index_dirs:
            embeddings_path = candidate / "embeddings.pt"
            metadata_path = candidate / "metadata.jsonl"
            meta_path = candidate / "meta.json"
            if embeddings_path.exists() and metadata_path.exists():
                index = self._load_index(embeddings_path, metadata_path, meta_path, repo_dir, commit)
                self._index_cache[candidate] = index
                return index

        index_dir.mkdir(parents=True, exist_ok=True)
        embeddings_path = index_dir / "embeddings.pt"
        metadata_path = index_dir / "metadata.jsonl"
        meta_path = index_dir / "meta.json"
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
    ) -> CodeSearchIndex:
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
        return CodeSearchIndex(embeddings, metadata, meta)

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
        snippet = "\n".join(chunk.text.splitlines()[:5])
        return {
            "file_path": chunk.path,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "language": chunk.language,
            "snippet": snippet,
            "symbol": None,
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
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def _load_index(
        self,
        embeddings_path: Path,
        metadata_path: Path,
        meta_path: Path,
        repo_dir: str,
        commit: str,
    ) -> CodeSearchIndex:
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
            }
        return CodeSearchIndex(embeddings, metadata, meta)
