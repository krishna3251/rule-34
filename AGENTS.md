# AGENTS.md — Rule-34 Project

## Project map
- Python application with `main.py` as the entry point, Discord feature modules under `cogs/`, shared persistence in `database.py`, helper services under `services/`, tests, and deployment/health support in `keep_alive.py`.
- Read `README.md`, `requirements.txt`, and existing tests before changing commands, storage, runtime behavior, or deployment settings.
- This repository includes local database files and a `vendor/` directory. Inspect how they are used before changing, deleting, regenerating, or committing data/assets.

## Implementation rules
- Follow the existing Python and Discord.py conventions; keep command handling, business logic, and database operations separated where practical.
- Preserve current command names, data formats, configuration keys, and runtime behavior unless the task explicitly requests a change.
- Validate user input and enforce permissions/access checks at the server/action boundary.
- Handle missing resources, malformed external responses, API errors, rate limits, and transient network failures without crashing the bot.
- Keep async handlers non-blocking. Use the existing persistence layer instead of duplicating database access patterns.
- Avoid unrelated rewrites, unnecessary dependencies, or changing stored records without a migration/compatibility plan.

## Security and content
- Read credentials from environment variables; use `.env.example` for safe placeholder documentation.
- Never commit, print, or reproduce real tokens, API keys, passwords, or private user data.
- Respect platform rules, content access restrictions, applicable age restrictions, and user privacy. Do not bypass authentication or access controls.
- Never include raw exception traces or secret configuration in user-facing Discord responses.

## Validation
- Inspect the test suite and project scripts before choosing commands.
- Run focused tests and Python syntax/import checks where feasible; explain when remote APIs or credentials prevent full integration tests.
- Review the final diff for accidental edits to database files, generated assets, `vendor/`, and deployment configuration.