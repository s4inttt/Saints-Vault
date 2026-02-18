# Discord Template Bot

A Xenon-style Discord bot that can **save** any server's structure as a reusable template and **load** it onto another server. Also supports server **backups**.

## Features

- **Save templates** — Capture a server's roles, channels, categories, permissions, name, and icon into a portable template
- **Load templates** — Apply a saved template onto any server, fully recreating its structure
- **Server backups** — Quick-save a server's state with auto-generated names for easy restoration
- **Slash commands** — All commands use Discord's native slash command interface
- **SQLite storage** — Templates and backups stored locally with async I/O
- **Permission overwrites** — Channel permissions are preserved and resolved by role name across servers

## Commands

### Templates

| Command | Description |
|---------|-------------|
| `/template save <name>` | Save the current server as a template |
| `/template load <name>` | Load a template onto the current server (destructive) |
| `/template list` | List all your saved templates |
| `/template info <name>` | Show details about a template |
| `/template delete <name>` | Delete a saved template |

### Backups

| Command | Description |
|---------|-------------|
| `/backup save [name]` | Create a backup (auto-names if no name given) |
| `/backup load <name>` | Restore a backup onto the current server (destructive) |
| `/backup list` | List all your saved backups |
| `/backup info <name>` | Show details about a backup |
| `/backup delete <name>` | Delete a saved backup |

All commands require **Administrator** permission.

## What Gets Saved

- Server name and icon
- Roles (name, color, permissions, hoist, mentionable, position)
- Categories with their permission overwrites
- Channels (text, voice, stage, forum, announcement) with topic, slowmode, NSFW, bitrate, user limit
- Per-channel permission overwrites (resolved by role name, not ID)

Bot-managed and integration roles are automatically skipped.

## Setup

### Prerequisites

- Python 3.10+
- A Discord bot token

### 1. Discord Developer Portal

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application and add a bot
3. Enable these **Privileged Gateway Intents**:
   - Server Members Intent
4. Invite the bot with these settings:
   - **Scopes**: `bot`, `applications.commands`
   - **Permissions**: Administrator

### 2. Installation

```bash
git clone <your-repo-url>
cd "Discord Template Bot"
pip install -r requirements.txt
```

### 3. Configuration

Create a `.env` file in the project root:

```
DISCORD_TOKEN=your_bot_token_here
```

### 4. Run

```bash
python bot.py
```

You should see:

```
Database initialized
Cogs loaded
Slash commands synced (2 commands)
Logged in as YourBot#1234 (ID: ...)
Connected to X guild(s)
```

## Project Structure

```
Discord Template Bot/
  bot.py                  # Entry point — loads cogs, syncs commands
  database.py             # Async SQLite layer (templates + backups tables)
  models.py               # Dataclasses for template data + JSON serialization
  requirements.txt        # Dependencies
  .env                    # Bot token (not committed)
  cogs/
    template_cog.py       # /template command group
    backup_cog.py         # /backup command group
  utils/
    serializer.py         # Guild -> TemplateData (captures server structure)
    loader.py             # TemplateData -> Guild (destructive rebuild)
    confirmation.py       # Confirm/Cancel button view for destructive ops
```

## How Loading Works

Loading a template is a **destructive** operation that replaces the entire server structure. It runs in 5 phases:

1. **Delete channels** — Removes all existing channels (keeps one alive for progress updates)
2. **Delete roles** — Removes all deletable roles (skips @everyone, managed, and roles above the bot)
3. **Create roles** — Recreates roles bottom-to-top with a 0.5s delay between each
4. **Create categories + channels** — Rebuilds the channel structure with resolved permission overwrites (0.3s delay between each)
5. **Apply server settings** — Sets the server name and icon

A confirmation prompt with a 30-second timeout is shown before any destructive operation.

## Dependencies

- [discord.py](https://github.com/Rapptz/discord.py) >= 2.3.0
- [python-dotenv](https://github.com/theskumar/python-dotenv) >= 1.0.0
- [aiosqlite](https://github.com/omnilib/aiosqlite) >= 0.19.0
