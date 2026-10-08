from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ImageTurn:
    image_id: str
    image_type: str
    emotion: str


class MikoImageHistory:
    """Small in-memory anti-repetition history scoped to conversations."""

    def __init__(self, max_items: int = 6) -> None:
        self._items: dict[str, deque[ImageTurn]] = {}
        self.max_items = max(2, max_items)

    def recent(self, key: str) -> tuple[ImageTurn, ...]:
        return tuple(self._items.get(key, ()))

    def add(self, key: str, image_id: str, image_type: str, emotion: str) -> None:
        queue = self._items.setdefault(key, deque(maxlen=self.max_items))
        queue.append(ImageTurn(image_id, image_type, emotion))

    def last(self, key: str) -> ImageTurn | None:
        queue = self._items.get(key)
        return queue[-1] if queue else None
