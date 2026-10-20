from __future__ import annotations

from functools import wraps
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("discord_bot")


def synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class MikoMemory:
    """Conversation history isolated by guild and channel, with legacy quarantine."""

    MEMORY_DURATION = 45 * 60
    MAX_USER_MESSAGES = 12
    MAX_CHANNEL_MESSAGES = 24
    MAX_CONTEXT_MESSAGES = 24
    MAX_CONTENT_LENGTH = 1200

    def __init__(self, path: str | Path = "data/miko_memory.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.users: dict[str, dict[str, Any]] = {}
        self.conversations: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self.legacy = {}
        self.load()

    @synchronized
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and ("users" in raw or "conversations" in raw):
                self.users = {}
                self.legacy = raw.get("legacy_users", raw.get("users", {})) or {}
                self.conversations = raw.get("conversations", {}) or {}
                if not isinstance(self.conversations, dict):
                    self.conversations = {}
                if not isinstance(self.legacy, dict):
                    self.legacy = {}
            else:
                self.users = {}
                self.legacy = raw if isinstance(raw, dict) else {}
                self.conversations = {}
            self._prune_expired(save=False)
        except (OSError, ValueError, TypeError) as exc:
            logger.error("Could not load Miko memory: %s", exc)
            self.users = {}
            self.conversations = {}

    def save(self) -> None:
        # Atomic replacement prevents a crash from truncating stored history.
        with self._lock:
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                                 prefix=".miko-", delete=False) as handle:
                    temp_path = handle.name
                    json.dump({"version": 2, "users": {}, "legacy_users": self.legacy,
                               "conversations": self.conversations}, handle, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, self.path)
            except OSError:
                logger.exception("Could not save Miko memory")
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.unlink(temp_path)

    def _prune_expired(self, save: bool = True) -> None:
        cutoff = time.time() - self.MEMORY_DURATION

        def valid(item: dict[str, Any]) -> bool:
            try:
                return float(item.get("timestamp", 0)) >= cutoff
            except (TypeError, ValueError):
                return False

        self.users = {
            key: value for key, value in self.users.items()
            if isinstance(value, dict) and valid(value)
        }
        self.conversations = {
            key: value for key, value in self.conversations.items()
            if isinstance(value, dict) and valid(value)
        }
        for bucket in self.conversations.values():
            messages = bucket.get("messages", [])
            if not isinstance(messages, list):
                messages = []
            bucket["messages"] = [m for m in messages if isinstance(m, dict)
                                  and isinstance(m.get("time"), (int, float)) and m["time"] >= cutoff]
        if save:
            self.save()

    @staticmethod
    def conversation_key(guild_id: int | None, channel_id: int) -> str:
        return f"{guild_id or 0}:{channel_id}"

    @synchronized
    def add(
        self,
        user_id: int,
        role: str,
        content: str,
        guild_id: int | None = None,
        channel_id: int | None = None,
        name: str | None = None,
    ) -> None:
        now = time.time()
        entry = {
            "role": "assistant" if role in {"assistant", "model"} else "user",
            "content": str(content)[: self.MAX_CONTENT_LENGTH],
            "name": str(name or "").strip()[:80],
            "user_id": int(user_id),
            "time": now,
        }

        if channel_id is not None:
            key = self.conversation_key(guild_id, channel_id)
            channel_bucket = self.conversations.setdefault(
                key,
                {"messages": [], "timestamp": now},
            )
            channel_bucket["timestamp"] = now
            channel_bucket["messages"].append(entry)
            channel_bucket["messages"] = channel_bucket["messages"][-self.MAX_CHANNEL_MESSAGES :]

        self._prune_expired(save=False)
        self.save()

    @synchronized
    def history(
        self,
        user_id: int,
        guild_id: int | None = None,
        channel_id: int | None = None,
    ) -> list[dict[str, str]]:
        self._prune_expired(save=False)

        channel_items: list[dict[str, Any]] = []

        if channel_id is not None:
            bucket = self.conversations.get(self.conversation_key(guild_id, channel_id))
            if bucket:
                channel_items = list(bucket.get("messages", []))

        # A user ID is not a privacy boundary: DM and guild channels stay separate.
        combined = channel_items
        combined.sort(key=lambda item: float(item.get("time", 0) or 0))
        return combined[-self.MAX_CONTEXT_MESSAGES :]

    @synchronized
    def reset(
        self,
        user_id: int,
        guild_id: int | None = None,
        channel_id: int | None = None,
    ) -> None:
        self.users.pop(str(user_id), None)
        self.legacy.pop(str(user_id), None)

        if channel_id is not None:
            bucket = self.conversations.get(self.conversation_key(guild_id, channel_id))
            if bucket:
                bucket["messages"] = [
                    msg for msg in bucket.get("messages", [])
                    if int(msg.get("user_id", -1)) != int(user_id)
                ]
                bucket["timestamp"] = time.time()
        else:
            for bucket in self.conversations.values():
                bucket["messages"] = [
                    msg for msg in bucket.get("messages", [])
                    if int(msg.get("user_id", -1)) != int(user_id)
                ]

        self.save()

    @synchronized
    def clear_channel(self, guild_id: int | None, channel_id: int) -> None:
        self.conversations.pop(self.conversation_key(guild_id, channel_id), None)
        self.save()
