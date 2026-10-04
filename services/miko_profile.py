from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class MikoProfile:
    """Persistent non-sensitive user preferences with explicit reset support."""

    def __init__(self, path: str | Path = "data/miko_profile.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.data = raw
        except (OSError, ValueError, TypeError):
            self.data = {}

    def save(self) -> None:
        try:
            self.path.write_text(
                json.dumps(self.data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    def get(self, user_id: int) -> dict[str, Any]:
        value = self.data.get(str(user_id), {})
        return dict(value) if isinstance(value, dict) else {}

    def set(self, user_id: int, key: str, value: Any) -> None:
        self.data.setdefault(str(user_id), {})[key] = value
        self.save()

    def forget(self, user_id: int) -> None:
        self.data.pop(str(user_id), None)
        self.save()
