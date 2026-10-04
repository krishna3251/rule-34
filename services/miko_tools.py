from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import discord
from discord.ext import commands
from discord.ext.commands.view import StringView

logger = logging.getLogger("discord_bot")


@dataclass(slots=True)
class MikoToolContext:
    bot: commands.Bot
    message: discord.Message
    miko_chat: Any | None
    allow_actions: bool = False


@dataclass(slots=True)
class MikoToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[
        [MikoToolContext, dict[str, Any]],
        Awaitable[dict[str, Any]],
    ]


class MikoToolRegistry:
    """Guarded local tools for Miko's agent loop.

    Groq decides when a tool is useful. The application remains the authority
    on what can actually execute, which keeps natural-language automation from
    becoming a permission bypass with better marketing.
    """

    BLOCKED_COMMANDS = frozenset(
        {
            "eval",
            "exec",
            "shell",
            "bash",
            "python",
            "shutdown",
            "stop",
            "exit",
            "restart",
            "reload",
            "sync",
            "sudo",
            "token",
            "settoken",
            "dev",
            "developer",
        }
    )

    MASS_ACTION_TERMS = (
        "everyone",
        "all members",
        "all users",
        "massban",
        "mass kick",
        "mass-kick",
        "mass ban",
        "mass-ban",
        "banall",
        "kickall",
        "bulk ban",
        "bulk kick",
        "wipe everyone",
    )

    def __init__(self) -> None:
        self._tools: dict[str, MikoToolSpec] = {}
        self.register(
            MikoToolSpec(
                name="list_bot_commands",
                description=(
                    "List the bot's available user-facing Discord commands, "
                    "aliases and short help text. Use this when the user asks "
                    "what Miko can do, asks for a command, or you are unsure "
                    "which command maps to the request."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Optional keyword to filter commands, such as "
                                "'music', 'moderation', 'game', or 'miko'."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 60,
                        },
                    },
                    "additionalProperties": False,
                },
                handler=self._list_bot_commands,
            )
        )
        self.register(
            MikoToolSpec(
                name="get_channel_info",
                description=(
                    "Inspect the current Discord channel and whether it is "
                    "age-restricted. Read-only."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                handler=self._get_channel_info,
            )
        )
        self.register(
            MikoToolSpec(
                name="get_server_overview",
                description=(
                    "Inspect basic information about the current Discord "
                    "server, including member, channel and role counts. Read-only."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                handler=self._get_server_overview,
            )
        )
        self.register(
            MikoToolSpec(
                name="run_bot_command",
                description=(
                    "Execute one existing user-facing Discord prefix command "
                    "using the requesting user's real Discord permissions. "
                    "Use only when the user clearly asked Miko to perform "
                    "that action, not when they merely ask how to do it. "
                    "Never use this to run owner/developer/system commands. "
                    "Do not invent command names or arguments; inspect the "
                    "command list first when uncertain."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "command_name": {
                            "type": "string",
                            "description": (
                                "Command name or alias without its prefix."
                            ),
                        },
                        "arguments": {
                            "type": "string",
                            "description": (
                                "Exact command arguments as a normal prefix "
                                "command user would type after the command name."
                            ),
                        },
                    },
                    "required": ["command_name"],
                    "additionalProperties": False,
                },
                handler=self._run_bot_command,
            )
        )

    def register(self, spec: MikoToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Duplicate Miko tool: {spec.name}")
        self._tools[spec.name] = spec

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self._tools.values()
        ]

    def names(self) -> list[str]:
        return list(self._tools)

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        context: MikoToolContext,
    ) -> dict[str, Any]:
        spec = self._tools.get(name)
        if spec is None:
            return {"success": False, "error": f"Unknown Miko tool: {name}"}

        if not isinstance(arguments, dict):
            return {"success": False, "error": "Tool arguments must be an object"}

        missing = [
            item
            for item in spec.parameters.get("required", [])
            if item not in arguments
        ]
        if missing:
            return {
                "success": False,
                "error": f"Missing required arguments: {', '.join(missing)}",
            }

        try:
            return await spec.handler(context, arguments)
        except discord.Forbidden:
            return {
                "success": False,
                "error": "Discord denied the action because of permissions or hierarchy.",
            }
        except discord.NotFound:
            return {
                "success": False,
                "error": "The requested Discord object no longer exists.",
            }
        except discord.HTTPException as exc:
            return {
                "success": False,
                "error": f"Discord API error: {exc.status}.",
            }
        except Exception:
            logger.exception("Miko tool %s failed", name)
            return {"success": False, "error": "Internal Miko tool failure."}

    async def _list_bot_commands(
        self,
        context: MikoToolContext,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        query = str(arguments.get("query") or "").casefold().strip()
        limit = max(1, min(int(arguments.get("limit", 40)), 60))
        commands_found: list[dict[str, Any]] = []

        for command in sorted(
            context.bot.walk_commands(),
            key=lambda item: item.qualified_name.casefold(),
        ):
            if command.hidden:
                continue

            root = command.qualified_name.split(" ", 1)[0].casefold()
            if root in self.BLOCKED_COMMANDS:
                continue
            if root.startswith("dev") or root.startswith("owner"):
                continue
            if command.name in {"mikoask", "askmiko"}:
                # Prevent Miko's agent from recursively invoking another AI call.
                continue

            aliases = ", ".join(command.aliases[:8])
            help_text = (command.help or command.description or "").strip()
            haystack = " ".join(
                (
                    command.qualified_name,
                    aliases,
                    help_text,
                    getattr(command.cog, "qualified_name", "") or "",
                )
            ).casefold()

            if query and query not in haystack:
                continue

            commands_found.append(
                {
                    "name": command.qualified_name,
                    "aliases": list(command.aliases[:8]),
                    "help": help_text[:240],
                    "category": getattr(command.cog, "qualified_name", None),
                }
            )

            if len(commands_found) >= limit:
                break

        slash_commands: list[dict[str, Any]] = []
        if not query or "slash" in query or "/" in query:
            for command in sorted(
                context.bot.tree.walk_commands(),
                key=lambda item: getattr(item, "qualified_name", item.name).casefold(),
            ):
                name = getattr(command, "qualified_name", command.name)
                description = str(getattr(command, "description", "") or "").strip()
                haystack = f"/{name} {description}".casefold()
                if query and query not in haystack:
                    continue
                slash_commands.append(
                    {
                        "name": f"/{name}",
                        "help": description[:240],
                        "category": getattr(command, "parent", None).name
                        if getattr(command, "parent", None)
                        else None,
                    }
                )
                if len(slash_commands) >= limit:
                    break

        return {
            "success": True,
            "count": len(commands_found) + len(slash_commands),
            "prefix_commands": commands_found,
            "slash_commands": slash_commands,
        }

    async def _get_channel_info(
        self,
        context: MikoToolContext,
        _: dict[str, Any],
    ) -> dict[str, Any]:
        channel = context.message.channel
        is_nsfw = bool(getattr(channel, "is_nsfw", lambda: False)())
        return {
            "success": True,
            "channel_id": getattr(channel, "id", None),
            "channel_name": getattr(channel, "name", str(channel)),
            "channel_type": str(getattr(channel, "type", "")),
            "is_age_restricted": is_nsfw,
            "guild_id": getattr(context.message.guild, "id", None),
        }

    async def _get_server_overview(
        self,
        context: MikoToolContext,
        _: dict[str, Any],
    ) -> dict[str, Any]:
        guild = context.message.guild
        if guild is None:
            return {
                "success": True,
                "guild": None,
                "note": "This conversation is outside a Discord server.",
            }

        return {
            "success": True,
            "guild": {
                "id": guild.id,
                "name": guild.name,
                "owner_id": guild.owner_id,
                "member_count": guild.member_count,
                "channel_count": len(guild.channels),
                "role_count": len(guild.roles),
                "text_channels": len(guild.text_channels),
                "voice_channels": len(guild.voice_channels),
                "created_at": guild.created_at.isoformat(),
            },
        }

    async def _run_bot_command(
        self,
        context: MikoToolContext,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not context.allow_actions:
            return {
                "success": False,
                "error": (
                    "Action tools are only available when the user explicitly "
                    "summoned Miko for the request."
                ),
            }

        command_name = str(arguments.get("command_name") or "").strip()
        command_key = command_name.casefold().lstrip("~!/").strip()
        command_args = str(arguments.get("arguments") or "").strip()

        if not command_key:
            return {"success": False, "error": "Command name is required."}

        root = command_key.split(" ", 1)[0]
        if root in self.BLOCKED_COMMANDS or root.startswith("dev"):
            return {
                "success": False,
                "error": f"Command '{command_name}' is protected from AI execution.",
            }

        if any(term in command_args.casefold() for term in self.MASS_ACTION_TERMS):
            return {
                "success": False,
                "error": "Bulk destructive actions are not allowed through Miko's natural-language command executor.",
            }

        command = context.bot.get_command(command_key)
        if command is None:
            return {
                "success": False,
                "error": (
                    f"Unknown command '{command_name}'. "
                    "Use list_bot_commands to inspect available commands."
                ),
            }

        if command.hidden:
            return {"success": False, "error": "Hidden commands are not AI-executable."}

        command_root = command.qualified_name.split(" ", 1)[0].casefold()
        if (
            command_root in self.BLOCKED_COMMANDS
            or command_root.startswith("dev")
            or command_root.startswith("owner")
        ):
            return {
                "success": False,
                "error": "Developer or owner-only commands are protected.",
            }

        try:
            ctx = await context.bot.get_context(context.message)
            ctx.command = command
            ctx.invoked_with = command.name
            ctx.view = StringView(command_args)

            await command.invoke(ctx)
        except commands.CheckFailure as exc:
            return {
                "success": False,
                "error": f"Discord command permission/check failed: {type(exc).__name__}.",
            }
        except commands.UserInputError as exc:
            return {
                "success": False,
                "error": f"Command arguments were invalid: {type(exc).__name__}.",
            }
        except commands.CommandError as exc:
            logger.warning(
                "Miko command execution failed | command=%s error=%s",
                command.qualified_name,
                exc,
            )
            return {
                "success": False,
                "error": f"Discord command failed: {type(exc).__name__}.",
            }
        except Exception:
            logger.exception(
                "Unexpected Miko command execution error | command=%s",
                command.qualified_name,
            )
            return {"success": False, "error": "Command execution failed unexpectedly."}

        return {
            "success": True,
            "executed": command.qualified_name,
            "arguments": command_args,
            "note": (
                "The original Discord command handler may have sent its own "
                "user-facing response."
            ),
        }
