from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("discord_bot")


class MikoMemory:
    """Rukiya-style dual-scope memory: per-user profile history + shared channel thread."""

    MEMORY_DURATION = 45 * 60
    MAX_USER_MESSAGES = 12
    MAX_CHANNEL_MESSAGES = 24
    MAX_CONTENT_LENGTH = 1200

    def __init__(self, path: str | Path = "data/miko_memory.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.users: dict[str, dict[str, Any]] = {}
        self.conversations: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and ("users" in raw or "conversations" in raw):
                self.users = raw.get("users", {}) or {}
                self.conversations = raw.get("conversations", {}) or {}
            else:
                self.users = raw if isinstance(raw, dict) else {}
                self.conversations = {}
            self._prune_expired(save=False)
        except (OSError, ValueError, TypeError) as exc:
            logger.error("Could not load Miko memory: %s", exc)
            self.users = {}
            self.conversations = {}

    def save(self) -> None:
        try:
            self.path.write_text(
                json.dumps(
                    {"users": self.users, "conversations": self.conversations},
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("Could not save Miko memory: %s", exc)

    def _prune_expired(self, save: bool = True) -> None:
        cutoff = time.time() - self.MEMORY_DURATION

        def valid(item: dict[str, Any]) -> bool:
            try:
                return float(item.get("timestamp", 0)) >= cutoff
            except (TypeError, ValueError):
                return False

        self.users = {
            key: value
            for key, value in self.users.items()
            if isinstance(value, dict) and valid(value)
        }
        self.conversations = {
            key: value
            for key, value in self.conversations.items()
            if isinstance(value, dict) and valid(value)
        }
        if save:
            self.save()

    @staticmethod
    def user_key(user_id: int) -> str:
        return str(user_id)

    @staticmethod
    def conversation_key(guild_id: int | None, channel_id: int) -> str:
        return f"{guild_id or 0}:{channel_id}"

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
            "time": now,
        }

        ukey = self.user_key(user_id)
        user_bucket = self.users.setdefault(
            ukey,
            {"messages": [], "timestamp": now},
        )
        user_bucket["timestamp"] = now
        user_bucket["messages"].append(entry)
        user_bucket["messages"] = user_bucket["messages"][-self.MAX_USER_MESSAGES :]

        if channel_id is not None:
            ckey = self.conversation_key(guild_id, channel_id)
            channel_bucket = self.conversations.setdefault(
                ckey,
                {"messages": [], "timestamp": now},
            )
            channel_bucket["timestamp"] = now
            channel_bucket["messages"].append(entry)
            channel_bucket["messages"] = channel_bucket["messages"][-self.MAX_CHANNEL_MESSAGES :]

        self._prune_expired(save=False)
        self.save()

    def history(
        self,
        user_id: int,
        guild_id: int | None = None,
        channel_id: int | None = None,
    ) -> list[dict[str, str]]:
        self._prune_expired(save=False)

        if channel_id is not None:
            bucket = self.conversations.get(
                self.conversation_key(guild_id, channel_id)
            )
            if bucket and bucket.get("messages"):
                return list(bucket["messages"][-self.MAX_CHANNEL_MESSAGES :])

        bucket = self.users.get(self.user_key(user_id), {})
        return list(bucket.get("messages", [])[-self.MAX_USER_MESSAGES :])

    def reset(
        self,
        user_id: int,
        guild_id: int | None = None,
        channel_id: int | None = None,
    ) -> None:
        self.users.pop(self.user_key(user_id), None)
        if channel_id is not None:
            bucket = self.conversations.get(self.conversation_key(guild_id, channel_id))
            if bucket:
                bucket["messages"] = [
                    msg for msg in bucket.get("messages", [])
                    if str(msg.get("name", "")) != str(user_id)
                ]
                bucket["timestamp"] = time.time()
        else:
            self.save()
            return
        self.save()

    def clear_channel(self, guild_id: int | None, channel_id: int) -> None:
        self.conversations.pop(self.conversation_key(guild_id, channel_id), None)
        self.save()
