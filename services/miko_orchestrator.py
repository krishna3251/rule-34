from __future__ import annotations

"""Backward-compatible import for the unified Miko chat engine."""

from services.miko_chat_engine import MikoChatEngine, MikoResult


class MikoOrchestrator(MikoChatEngine):
    """Compatibility alias.

    MikoChatEngine is now the single conversational runtime. This class stays
    so existing extensions/imports do not break during the migration.
    """

    pass


__all__ = ["MikoOrchestrator", "MikoChatEngine", "MikoResult"]
