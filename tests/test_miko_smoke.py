from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def parse(path: str) -> ast.Module:
    source = (ROOT / path).read_text(encoding="utf-8")
    return ast.parse(source, filename=path)


def decorator_name(node: ast.AST) -> str | None:
    for decorator in getattr(node, "decorator_list", []):
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute):
            return f"{getattr(target.value, 'id', '')}.{target.attr}".strip(".")
        if isinstance(target, ast.Name):
            return target.id
    return None


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
            if isinstance(target, ast.Attribute):
                marker = "commands.command" if prefix else "app_commands.command"
                if f"{getattr(target.value, 'id', '')}.{target.attr}" != marker:
                    continue
                for kw in decorator.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        names.add(str(kw.value.value))
    return names


def test_python_sources_compile():
    import py_compile

    paths = [
        path for path in ROOT.rglob("*.py")
        if "vendor" not in path.parts
        and "__pycache__" not in path.parts
    ]
    assert paths
    for path in paths:
        py_compile.compile(str(path), doraise=True)


def test_miko_provider_stack():
    source = (ROOT / "services/miko_ai.py").read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY" in source
    assert "OpenRouter" in source
    assert "using Groq backup" in source
    assert "GROQ_API_KEY" in source
    assert "openrouter:web_search" in source


def test_miko_agent_tools_exist():
    source = (ROOT / "services/miko_tools.py").read_text(encoding="utf-8")
    for name in (
        "list_bot_commands",
        "get_channel_info",
        "get_server_overview",
        "run_bot_command",
    ):
        assert f'name="{name}"' in source


def test_miko_guardrails_remain():
    gate = (ROOT / "services/miko_gate.py").read_text(encoding="utf-8")
    personality = (ROOT / "services/miko_personality.py").read_text(encoding="utf-8")
    assert "EXPLICIT_REQUEST_PATTERN" in gate
    assert "Never describe sexual acts" in personality
    assert "HIGH-INTELLIGENCE" in personality


def test_miko_command_bridges():
    search_prefix = command_names("cogs/search.py", prefix=True)
    personality_prefix = command_names("cogs/personality.py", prefix=True)
    r34_prefix = command_names("cogs/r34.py", prefix=True)
    verification_prefix = command_names("cogs/verification.py", prefix=True)

    assert "search" in search_prefix
    assert "mood" in personality_prefix
    assert {"r34", "r34random", "trending", "fav"} <= r34_prefix
    assert "verification_status" in verification_prefix


def test_default_prefix_is_not_lost():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'when_mentioned_or("miko!", "~")' in source
    assert "custom_values + default_values" in source
