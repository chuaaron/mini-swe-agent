"""Chunker interfaces for code_search."""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Chunk:
    path: str
    language: str | None
    start_line: int
    end_line: int
    text: str


class Chunker(Protocol):
    name: str

    def chunk_file(self, path: str, text: str, language: str | None) -> list[Chunk]: ...
