from __future__ import annotations

import ast
import py_compile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def parse(path: str) -> ast.Module:
    source = (ROOT / path).read_text(encoding="utf-8")
    return ast.parse(source, filename=path)


def command_names(path: str, prefix: bool) -> set[str]:
    tree = parse(path)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            target = decorator.func
            if not isinstance(target, ast.Attribute):
                continue
            marker = "commands.command" if prefix else "app_commands.command"
            if f"{getattr(target.value, 'id', '')}.{target.attr}" != marker:
                continue
            for kw in decorator.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    names.add(str(kw.value.value))
    return names


class RepositorySmokeTests(unittest.TestCase):
    def test_python_sources_compile(self):
        paths = [
            path for path in ROOT.rglob("*.py")
            if "vendor" not in path.parts
            and "__pycache__" not in path.parts
            and ".git" not in path.parts
        ]
        self.assertTrue(paths)
        for path in paths:
            py_compile.compile(str(path), doraise=True)

    def test_miko_provider_stack(self):
        source = (ROOT / "services/miko_ai.py").read_text(encoding="utf-8")
        orchestrator = (ROOT / "services/miko_orchestrator.py").read_text(encoding="utf-8")
        self.assertIn("OPENROUTER_API_KEY", source)
        self.assertIn("OpenRouter", source)
        self.assertIn("using Groq backup", source)
        self.assertIn("GROQ_API_KEY", source)
        self.assertIn("openrouter:web_search", source)
        self.assertIn("def provider(self) -> str:", orchestrator)
        self.assertIn("return self.ai.provider", orchestrator)

    def test_miko_agent_tools_exist(self):
        source = (ROOT / "services/miko_tools.py").read_text(encoding="utf-8")
        for name in (
            "list_bot_commands",
            "get_channel_info",
            "get_server_overview",
            "run_bot_command",
        ):
            self.assertIn(f'name="{name}"', source)

    def test_miko_guardrails_remain(self):
        gate = (ROOT / "services/miko_gate.py").read_text(encoding="utf-8")
        personality = (ROOT / "services/miko_personality.py").read_text(encoding="utf-8")
        self.assertIn("EXPLICIT_REQUEST_PATTERN", gate)
        self.assertIn("Never describe sexual acts", personality)
        self.assertIn("HIGH-INTELLIGENCE", personality)

    def test_miko_emotion_map_is_complete(self):
        emotion = (ROOT / "services/miko_emotion.py").read_text(encoding="utf-8")
        for emotion_id in range(1, 25):
            self.assertRegex(
                emotion,
                rf"{emotion_id}:\s+",
                msg=f"missing emotion ID {emotion_id}",
            )
        self.assertIn('Path(image_dir)', emotion)
        self.assertIn('f"{emotion_id:02d}.webp"', emotion)

    def test_miko_result_carries_emotion(self):
        source = (ROOT / "services/miko_orchestrator.py").read_text(encoding="utf-8")
        chat = (ROOT / "cogs/miko_chat.py").read_text(encoding="utf-8")
        self.assertIn("emotion_id: int = 1", source)
        self.assertIn("emotion_id=emotion.id", source)
        self.assertIn("miko_{emotion.id:02d}.webp", chat)
        self.assertIn("discord.File", chat)

    def test_miko_command_bridges(self):
        search_prefix = command_names("cogs/search.py", prefix=True)
        personality_prefix = command_names("cogs/personality.py", prefix=True)
        r34_prefix = command_names("cogs/r34.py", prefix=True)
        verification_prefix = command_names("cogs/verification.py", prefix=True)

        self.assertIn("search", search_prefix)
        self.assertIn("mood", personality_prefix)
        self.assertTrue({"r34", "r34random", "trending", "fav"} <= r34_prefix)
        self.assertIn("verification_status", verification_prefix)

    def test_requested_slash_command_coverage(self):
        expected = {
            "cogs/Detection_Cog.py": {
                "cfcheck", "cfdetail", "cfstatus", "cftest", "cfhelp",
            },
            "cogs/instructor.py": {
                "r34search", "r34mirrors", "r34debug", "r34debugraw", "r34debughelp",
            },
            "cogs/miko_chat.py": {
                "mikosetchat", "mikounsetchat", "mikosetlevel", "mikodisabled",
                "mikoenabled", "mikoquiet", "mikochatinfo", "resetmiko",
                "forgetmiko", "stopflirting", "allowflirting", "askmiko",
            },
            "cogs/personality.py": {"mood"},
            "cogs/r34.py": {
                "r34", "r34random", "r34girl", "r34anime", "r34milf",
                "r34hentai", "r34furry", "trending", "fav", "r34help",
                "nsfw", "random",
            },
            "cogs/search.py": {"search"},
            "cogs/verification.py": {
                "verify", "verification_stats", "force_verify",
                "verification_status",
            },
        }

        for path, names in expected.items():
            slash = command_names(path, prefix=False)
            missing = sorted(names - slash)
            self.assertFalse(missing, f"{path} missing slash commands: {missing}")

    def test_default_prefix_is_not_lost(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('when_mentioned_or("miko!", "~")', source)
        self.assertIn("custom_values + default_values", source)


if __name__ == "__main__":
    unittest.main()
