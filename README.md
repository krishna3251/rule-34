<div align="center">

# 🔞 Rule43

### R34 Discord Bot · Search · Discovery · Verification · Automation

A feature-rich Discord bot built around **Rule34 content discovery**, community access control, API tooling, and supporting utilities.

<p>
  <img src="https://img.shields.io/badge/Discord-Bot-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord Bot">
  <img src="https://img.shields.io/badge/Python-3.x-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/SQLite-Database-003B57?style=for-the-badge&logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/18%2B-Adult%20Content-111827?style=for-the-badge" alt="18+">
</p>

<p>
  <a href="#-features">Features</a> ·
  <a href="#-architecture">Architecture</a> ·
  <a href="#-setup">Setup</a> ·
  <a href="#-configuration">Configuration</a> ·
  <a href="#-security">Security</a>
</p>

</div>

---

> [!CAUTION]
> **18+ project.** This bot is intended for adults and communities that permit adult content.
>
> The verification flow is **self-attestation plus basic account-risk screening**. It is not independent, biometric, legal, or government-ID age verification.
>
> This project is an independent bot implementation and is not presented as an official Rule34 service.

## ✨ What is Rule43?

Rule43 is a Discord bot designed to make **R34 content discovery and community management** easier from inside Discord.

The project combines several systems into one bot:

<table>
<tr>
<td width="50%" valign="top">

### 🔎 Content
- R34 API integration
- Tag-oriented discovery
- External API health checks
- Cloudflare / CAPTCHA detection
- Configurable API credentials

</td>
<td width="50%" valign="top">

### 🛡️ Community
- 18+ verification flow
- Verification cooldowns
- Session expiration
- Guild-scoped role assignment
- Optional target-server invitation

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🎮 Utilities
- Game catalog
- Search and filtering
- Ratings
- JSON import/export
- Optional Gemini summaries

</td>
<td width="50%" valign="top">

### ⚙️ Operations
- SQLite persistence
- Async API/database workflows
- Logging
- Health endpoint
- Automatic SQLite backups
- Environment-based configuration

</td>
</tr>
</table>

---

## 🧩 Architecture

~~~mermaid
flowchart TD
    A[Discord User] --> B[Discord Bot]

    B --> C[Commands / Slash Commands]
    B --> D[Verification]
    B --> E[R34 Services]
    B --> F[Game Services]
    B --> G[Operations]

    C --> H[Command Handlers]
    D --> I[Session + Security Checks]
    D --> J[Verification Roles]
    E --> K[Rule34 / External APIs]
    E --> L[Cloudflare / CAPTCHA Checks]
    F --> M[Game Database]
    F --> N[Optional Gemini AI]
    G --> O[Logging]
    G --> P[Health Endpoint]
    G --> Q[SQLite Backups]

    H --> R[(SQLite)]
    I --> R
    M --> R
~~~

The codebase uses Discord cogs plus shared persistence and external services. The goal is to keep **Discord presentation**, **business logic**, **external APIs**, and **storage** from becoming one enormous Python sandwich.

---

## 📁 Project Structure

~~~text
rule-34/
│
├── main.py                 # Bot entry point, config, lifecycle
├── database.py             # SQLite persistence
├── keep_alive.py           # Health / keep-alive HTTP service
├── games.json              # Game catalog data
│
├── cogs/
│   ├── game.py             # Game catalog, ratings, AI summaries
│   └── verification.py     # 18+ verification workflow
│
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variable template
├── .gitignore              # Runtime / secret exclusions
└── README.md               # Project documentation
~~~

---

## 🚀 Features

### 🔎 R34 Integration

The bot is designed around API-driven content discovery rather than storing an entire remote content database locally.

Typical responsibilities include:

- API requests
- Search and filter handling
- External response validation
- API health monitoring
- Cloudflare / CAPTCHA detection
- Configurable credentials

### 🔐 Verification

The verification subsystem includes:

- 18+ self-attestation
- Per-user rate limits
- Session expiration
- Strong random session tokens
- Verification-message binding
- Guild-scoped role assignment
- Optional invitation to a configured target guild

### 🎮 Game Catalog

The game subsystem provides:

- SQLite-backed game records
- Title and tag filtering
- Ratings
- Pagination
- JSON import/export
- Optional AI-generated summaries
- Configurable Gemini model

### 🩺 Operations

The bot also includes operational tooling for hosted deployments:

- Flask health endpoint
- Runtime logging
- Background tasks
- SQLite database backups
- Environment-variable configuration
- Extension discovery

---

## 🛠️ Setup

### 1. Clone

~~~bash
git clone https://github.com/krishna3251/rule-34.git
cd rule-34
~~~

### 2. Create a virtual environment

**Windows**

~~~powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
~~~

**Linux / macOS**

~~~bash
python3 -m venv .venv
source .venv/bin/activate
~~~

### 3. Install dependencies

~~~bash
pip install -r requirements.txt
~~~

### 4. Configure environment variables

Copy the example file:

~~~bash
cp .env.example .env
~~~

On Windows PowerShell:

~~~powershell
Copy-Item .env.example .env
~~~

Then fill in the required values.

### 5. Start the bot

~~~bash
python main.py
~~~

---

## 🔧 Configuration

The project reads runtime configuration from environment variables.

| Variable | Required | Purpose |
|---|:---:|---|
| <code>DISCORD_TOKEN</code> | ✅ | Discord bot token |
| <code>OWNER_IDS</code> | Recommended | Comma-separated owner IDs |
| <code>DEBUG_MODE</code> | ❌ | Enables debug behavior |
| <code>DEBUG_GUILD_ID</code> | ❌ | Debug guild for faster command sync |
| <code>VERIFICATION_TARGET_GUILD_ID</code> | ❌ | Optional post-verification target guild |
| <code>R34_USER_ID</code> | ❌ | Optional R34 API user ID |
| <code>R34_API_KEY</code> | ❌ | Optional R34 API key |
| <code>GEMINI_API_KEY</code> | ❌ | Optional Gemini credential |
| <code>GEMINI_MODEL</code> | ❌ | Gemini model used for summaries |
| <code>GAMES_DB_PATH</code> | ❌ | SQLite database path |
| <code>ITCH_API_KEY</code> | ❌ | Optional external game API credential |
| <code>GAME_OF_DAY_CHANNEL_ID</code> | ❌ | Optional game-of-the-day channel |
| <code>ENABLE_ANALYTICS</code> | ❌ | Enable bot analytics |
| <code>ENABLE_AUTO_BACKUP</code> | ❌ | Enable automatic SQLite backups |

See **[.env.example](.env.example)** for the full template.

---

## 🔒 Security

A bot handling adult-community access should assume that users, URLs, and third-party API responses are untrusted.

Current protections include:

- Verification rate limiting
- Session expiry
- Cryptographically strong verification session tokens
- Exact verification-message binding
- Guild-scoped role changes
- Environment-based secrets
- Whitelisted dynamic database fields
- HTTP/HTTPS URL validation
- Runtime data excluded through <code>.gitignore</code>

### Important deployment rules

**Never commit:**

~~~text
.env
*.db
*.sqlite
logs/
backups/
API keys
Discord bot tokens
~~~

Also keep the Discord bot's role hierarchy and permissions as narrow as your deployment allows.

---

## 💾 Backups

When automatic backups are enabled, the bot creates SQLite snapshots under:

~~~text
backups/
└── bot_data_YYYYMMDD_HHMMSS.db
~~~

These files may contain user and server configuration data, so treat them as sensitive operational data.

---

## 🧪 Development

For development, keep responsibilities separated:

~~~text
Discord command
      ↓
Validation
      ↓
Service / business logic
      ↓
External API or database
      ↓
Formatted Discord response
~~~

Avoid putting API requests, SQL, permission checks, and large UI responses into one command handler. That path eventually becomes a Python horror novel.

### Useful checks

~~~bash
python -m compileall .
~~~

For targeted development, run and test the affected cog or service rather than relying only on startup logs.

---

## 📌 Project Direction

The repository is moving toward a cleaner separation between:

- **R34 content services**
- **Verification and access control**
- **Game utilities**
- **Database services**
- **Operational tooling**

The long-term goal is to keep the bot modular enough that new commands can be added without turning <code>main.py</code> into the final boss.

---

<div align="center">

### 🔞 Rule43 · Discord R34 Bot

Built with Python, Discord.py, SQLite, and external APIs.

**18+ communities only.**

</div>
