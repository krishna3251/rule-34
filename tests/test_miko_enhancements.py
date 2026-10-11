from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from services.miko_ai import MikoAI
from services.miko_decision import MikoDecisionEngine
from services.miko_games import MikoGameCatalog
from services.miko_gate import GateDecision
from services.miko_memory import MikoMemory
from services.miko_social import MikoSocialEngine


class MikoEnhancementTests(unittest.TestCase):
    def test_game_catalog_reads_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            path.write_text(
                json.dumps(
                    {
                        "games": [
                            {
                                "name": "Example Game",
                                "engine": "RenPy",
                                "type": "Visual Novel",
                                "visuals": "Koikatsu",
                                "status": "Active",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            catalog = MikoGameCatalog()
            catalog.paths = [path]
            self.assertEqual(catalog.load(), 1)
            result = catalog.search("Example Game")
            self.assertEqual(result[0]["name"], "Example Game")

    def test_shared_memory_contains_multiple_speakers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = MikoMemory(Path(tmp) / "memory.json")
            memory.add(1, "user", "hello", 10, 20, name="Alice")
            memory.add(2, "user", "hi there", 10, 20, name="Bob")
            history = memory.history(1, 10, 20)
            names = [item.get("name") for item in history]
            self.assertIn("Alice", names)
            self.assertIn("Bob", names)

    def test_social_temperature_profiles_are_deterministic(self) -> None:
        engine = MikoSocialEngine()
        self.assertAlmostEqual(engine.temperature("chat", "playful", 0), 0.88)
        self.assertAlmostEqual(engine.temperature("gaming", "happy", 0), 0.95)
        self.assertAlmostEqual(engine.temperature("serious", "serious", 0), 0.45)
        self.assertGreaterEqual(engine.temperature("serious", "serious", 0), 0.45)
        self.assertLessEqual(engine.temperature("chat", "happy", 999), 0.98)

    def test_groq_web_search_can_coexist_with_local_tools(self) -> None:
        class FakeCompletions:
            captured = None

            def create(self, **kwargs):
                self.captured = kwargs
                return {
                    "choices": [{
                        "message": {
                            "content": "searched",
                            "tool_calls": [],
                        }
                    }]
                }

        class FakeChat:
            def __init__(self):
                self.completions = FakeCompletions()

        class FakeClient:
            def __init__(self):
                self.chat = FakeChat()

        engine = MikoAI.__new__(MikoAI)
        engine.groq_client = FakeClient()
        engine.groq_model = "openai/gpt-oss-20b"
        engine.web_search_enabled = True
        result = asyncio.run(engine._groq_generate(
            [{"role": "user", "content": "latest news"}],
            tool_schemas=[{
                "type": "function",
                "function": {"name": "local_tool", "parameters": {"type": "object"}},
            }],
            allow_web=True,
            strict=False,
            temperature=0.6,
        ))
        self.assertEqual(result.text, "searched")
        tools = engine.groq_client.chat.completions.captured["tools"]
        self.assertEqual(tools[0], {"type": "browser_search"})
        self.assertEqual(tools[1]["function"]["name"], "local_tool")

    def test_social_temperature_is_bounded(self) -> None:
        engine = MikoSocialEngine()
        for intent in ("chat", "gaming", "help", "coding", "search", "serious"):
            temp = engine.temperature(intent, "playful", 3)
            self.assertGreaterEqual(temp, 0.45)
            self.assertLessEqual(temp, 0.98)

    def test_rukiya_style_decision_layer(self) -> None:
        engine = MikoDecisionEngine()

        decision = engine.decide(
            GateDecision(
                True,
                prompt="miko, what is the latest Minecraft update?",
                reason="summoned",
                spice_level=1,
                mood="playful",
            ),
            auto_chat=False,
            profile={"level_cap": 2},
        )

        self.assertTrue(decision.respond)
        self.assertEqual(decision.intent, "search")
        self.assertEqual(decision.priority, "high")
        self.assertEqual(decision.response_mode, "search")
        self.assertEqual(decision.memory_scope, "user+channel")
        self.assertTrue(decision.web_search)
        self.assertFalse(decision.allow_actions)

    def test_safety_decision_forces_clean_mode(self) -> None:
        engine = MikoDecisionEngine()
        decision = engine.decide(
            GateDecision(
                True,
                prompt="Refuse the request for disallowed explicit content.",
                reason="explicit_request",
                spice_level=2,
                mood="serious",
            ),
            profile={"level_cap": 2},
        )
        self.assertTrue(decision.strict)
        self.assertEqual(decision.spice_level, 0)
        self.assertEqual(decision.response_mode, "safety_refusal")
        self.assertFalse(decision.web_search)
        self.assertFalse(decision.allow_actions)

    def test_social_prompt_varies_across_turns(self) -> None:
        engine = MikoSocialEngine()
        first = engine.prompt_block(
            key="1:2", user_name="Alice", intent="chat",
            mood="playful", turn_count=1, topic_hint="hello"
        )
        second = engine.prompt_block(
            key="1:2", user_name="Alice", intent="chat",
            mood="playful", turn_count=2, topic_hint="games"
        )
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
