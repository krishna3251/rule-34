# Security, privacy and reliability update

This update addresses the source audit of baseline `a7454f86972c012be6a14996e95c86e674793a0a`.

## Changes

| Finding | Result |
| --- | --- |
| Developer subcommands skipped their parent's owner check | Each active subcommand checks ownership. Python evaluation is disabled and contains no evaluator. |
| Slash owner/admin decorators were ineffective or only defaults | Index diagnostics use application-command owner checks. Force verification and roleplay administration enforce runtime administrator permissions. |
| Chat memory crossed DM/channel/server boundaries | History is read only from the current server/channel; resets and channel clearing cannot resurrect global history. Legacy unscoped user memory stays quarantined on disk until forgotten. Entries expire individually. |
| Legacy profile details could appear in another conversation | Unscoped nickname/interests are retained on disk but excluded from prompts. Only the explicit global safety cap carries across contexts. Forgetting removes all profile scopes. |
| Content commands bypassed restrictions | Shared prefix/slash guards require current server verification and an age-restricted channel, including thread parents. Existing content pagination and roleplay message processing recheck access. The agent executor invokes normal command checks. Diagnostics require the owner. |
| Roleplay settings/history crossed servers | New scoped tables isolate server settings, sessions and thread history. Only the session owner can trigger a response. Existing character records and legacy tables remain untouched. |
| Arbitrary URL requests could reach private networks | User-submitted downloads use public-address validation at connector DNS resolution, standard HTTP(S) ports, no credentials, no environment proxy and no redirects. Diagnostic bodies are limited to 1 MiB; image bodies to 8 MiB across all chunks. |
| Cache ignored selected media types | Versioned keys include media filters; fresh and cached records are filtered. Unfiltered legacy cache keys are not reused. |
| Bundled packages shadowed installed SDKs | Runtime uses installed requirements. The vendor directory remains preserved but is not inserted into Python's import path. |
| Configured custom prefixes raised TypeError | Synchronous prefix helpers are no longer awaited. |
| Blocking storage and unsafe error messages | Verification calls, memory writes, search cache/index operations and SQLite backups use workers. Roleplay uses aiosqlite with cleanup. SQLite index contexts close connections. User errors are generic; configured webhook reports contain failure classes and IDs rather than raw exceptions. Mention expansion is disabled by default. |
| Health always claimed success | The running entry point starts the health service. `/health` returns 503 until Discord is ready or when extensions failed, and 200 when ready without extension failures. |
| Itch adapter falsely appeared usable | Its capability health returns unavailable while search remains unimplemented. No discovery adapter was added. |
| Dependency entry points diverged | `requirement.txt` includes maintained `requirements.txt`. CI excludes inactive vendor files and runs behavioral and isolated startup tests. |

## Deployment

1. Back up all runtime databases and `data/` before upgrading. Stop the previous process; install `requirements.txt` in a fresh Python 3.12 environment. Do not add `vendor/` to `PYTHONPATH`.
2. Check `DATABASE_URL` before restarting. It now takes effect and supports SQLite file URLs only. Use `sqlite:///bot_data.db` to retain the previous default database; an old `sqlite:///bot.db` setting must be corrected if it previously pointed at an unused file. Relative paths resolve from the process working directory. Existing database contents are not moved automatically.
3. Keep persistent paths for the bot database, `GAMES_DB_PATH`, `INDEX_DB_PATH`, `ROLEPLAY_DB_PATH` (default `data/roleplay.db`), memory and profile JSON. Use one bot process per set of JSON files.
4. Each server administrator must run `/setrpchannel` again and users must start new sessions. Legacy sessions/history/config are deliberately not inferred into a server: they have no reliable server scope. Character records remain available. Old tables are preserved for operator-controlled recovery.
5. Verify the server's verification flow and age-restricted channel configuration. Existing server-scoped verification records remain valid; no owner exemption bypasses content restrictions.
6. Restart/redeploy, let normal command sync complete, then check `/health`, command registration and permissions in a test server. Enable required Discord privileged intents in the developer portal. Set `PORT` for the health listener if needed.

## Verification and limits

Run `python -m unittest discover -s tests -p 'test_*.py' -v`.
The suite includes behavior checks for owner denial, prefix resolution, scoped access, memory expiry/reset/reload, profile privacy, bounded downloads, DNS rejection, filtered caching, temporary legacy-table preservation, server-scoped roleplay administration and truthful health. A disposable startup test imports Gemini/Groq, loads every extension and registers commands with outbound socket connections blocked and synthetic configuration.

No live Discord login, production migration, external content API call, AI response-quality evaluation or deployment is performed by these tests. Local test results do not prove the running deployment is healthy. Runtime state files already tracked in Git remain unchanged; reviewing/removing historical private records and separating catalog seeds from runtime state needs a separate data-retention decision. Dependencies retain their existing version ranges; this update does not introduce a cross-platform lockfile. The health endpoint reports current readiness and startup failures, not ongoing per-provider latency or an external uptime monitor.
