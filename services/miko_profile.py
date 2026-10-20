from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from services.miko_memory import synchronized
from pathlib import Path
from typing import Any


class MikoProfile:
    """Persistent non-sensitive user preferences with explicit reset support."""

    def __init__(self, path: str | Path = "data/miko_profile.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self.load()

    @synchronized
    def load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.data = raw
        except (OSError, ValueError, TypeError):
            self.data = {}

    @synchronized
    def save(self) -> None:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".profile-", delete=False) as handle:
                temp_path = handle.name
                json.dump(self.data, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except OSError:
            logging.getLogger("discord_bot").exception("Could not save Miko profile")
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    @synchronized
    def get(self, user_id: int, guild_id: int | None = None,
            channel_id: int | None = None) -> dict[str, Any]:
        value = self.data.get(str(user_id), {})
        # Legacy nickname/interests have no reliable privacy scope. Keep them on
        # disk for compatibility but never inject them into another conversation.
        result = {"level_cap": value["level_cap"]} if isinstance(value, dict) and "level_cap" in value else {}
        scoped = self.data.get(f"{guild_id or 0}:{channel_id}:{user_id}", {})
        if isinstance(scoped, dict):
            result.update(scoped)
        return result

    @synchronized
    def set(self, user_id: int, key: str, value: Any) -> None:
        self.data.setdefault(str(user_id), {})[key] = value
        self.save()

    @synchronized
    def forget(self, user_id: int) -> None:
        for key in list(self.data):
            if key == str(user_id) or key.endswith(f":{user_id}"):
                self.data.pop(key, None)
        self.save()
