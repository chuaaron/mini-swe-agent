"""Mapper base interfaces."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple


class BlockMapper(ABC):
    """Map code blocks to modules/entities."""

    @abstractmethod
    def map_blocks_to_entities(
        self,
        blocks: List[Dict[str, Any]],
        instance_id: str,
        top_k_modules: int = 10,
        top_k_entities: int = 50,
    ) -> Tuple[List[str], List[str]]:
        """Return (found_modules, found_entities)."""
        raise NotImplementedError
