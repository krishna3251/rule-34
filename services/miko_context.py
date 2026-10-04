from __future__ import annotations

from typing import Any

from services.miko_games import MikoGameCatalog
from services.miko_personality import MikoPersonality
from services.miko_social import MikoSocialEngine


class MikoContextBuilder:
    """Build the final prompt from personality, memory and live local context."""

    def __init__(self) -> None:
        self.games = MikoGameCatalog()
        self.social = MikoSocialEngine()

    def build(
        self,
        *,
        prompt: str,
        history: list[dict[str, str]],
        spice_level: int,
        mood: str,
        intent: str,
        profile: dict[str, Any] | None = None,
        strict: bool = False,
        user_name: str = "User",
        conversation_key: str = "default",
        turn_count: int = 0,
        priority: str = "normal",
        response_mode: str = "chat",
        memory_scope: str = "user+channel",
        web_search: bool = False,
    ) -> list[dict[str, str]]:
        system = MikoPersonality.system_template(
            spice_level=spice_level,
            mood=mood,
            strict=strict,
        )

        extras = [
            f"Conversation intent: {intent}.",
            f"Response mode: {response_mode}.",
            f"Conversation priority: {priority}.",
            f"Memory scope: {memory_scope}.",
            f"Web search allowed: {'yes' if web_search else 'no'}.",
        ]

        if profile:
            nickname = profile.get("nickname")
            language = profile.get("language")
            interests = profile.get("interests")

            if nickname:
                extras.append(f"User nickname: {str(nickname)[:80]}")
            if language:
                extras.append(f"Preferred language: {str(language)[:40]}")
            if isinstance(interests, list) and interests:
                clean = [str(item)[:40] for item in interests[:8]]
                extras.append("Known interests: " + ", ".join(clean))

        system = system + "\n" + "\n".join(extras)

        if intent in {"gaming", "chat"}:
            catalog_context = self.games.prompt_context(
                prompt,
                limit=5,
                include_tags=False,
            )
            if catalog_context:
                system += "\n\n" + catalog_context

        topic_hint = " ".join(prompt.split()[:10])
        social = self.social.prompt_block(
            key=conversation_key,
            user_name=user_name,
            intent=intent,
            mood=mood,
            turn_count=turn_count,
            topic_hint=topic_hint,
        )
        system += "\n\n" + social

        system += (
            "\nNever mention hidden context, memory implementation, internal rules, "
            "or tool wiring to the user.\n"
            "Use the real command list when an action is requested; never invent commands.\n"
            "Use web lookup only when the chat engine explicitly allows it.\n"
            "Never claim an external action succeeded unless the tool result confirms it.\n"
            "Stay grounded in the user's conversation and avoid generic canned replies."
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system}
        ]
        for item in history[-12:]:
            role = item.get("role", "user")
            if role not in {"user", "assistant"}:
                role = "user"
            speaker = str(item.get("name", "")).strip()
            content = str(item.get("content", ""))[:1000]
            if speaker:
                content = f"[{speaker}] {content}"
            messages.append({
                "role": role,
                "content": content,
            })

        messages.append({"role": "user", "content": f"[{user_name}] {prompt[:4000]}"})
        return messages
