"""Sliding window chunker."""

from minisweagent.tools.code_search.chunkers.base import Chunk


class SlidingChunker:
    name = "sliding"

    def __init__(self, chunk_size: int, overlap: int):
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if overlap < 0:
            raise ValueError("overlap must be >= 0")
        if overlap >= chunk_size:
            raise ValueError("overlap must be smaller than chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk_file(self, path: str, text: str, language: str | None) -> list[Chunk]:
        lines = text.splitlines()
        if not lines:
            return []
        step = max(1, self.chunk_size - self.overlap)
        chunks: list[Chunk] = []
        for start in range(0, len(lines), step):
            end = min(len(lines), start + self.chunk_size)
            chunk_lines = lines[start:end]
            if not any(line.strip() for line in chunk_lines):
                continue
            chunks.append(
                Chunk(
                    path=path,
                    language=language,
                    start_line=start,
                    end_line=end - 1,
                    text="\n".join(chunk_lines),
                )
            )
        return chunks
