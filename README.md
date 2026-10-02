# USF Bot

A lightweight Discord bot for answering USF-related questions using Groq and live web snippets for current information.

## Commands

| Command | What it does |
|---|---|
| `!ping` | Checks if the bot is online. |
| `!hello` | Greets the user. |
| `!status` | Shows the bot status. |
| `!commands` | Lists the available commands. |
| `!ask <question>` | Asks Groq a USF question. |
| `!search <query>` | Searches for current USF-related info and summarizes the result. |

## 1. Create the Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a new application and add a bot.
3. Copy the bot token.
4. Enable the `Message Content Intent` in the bot settings.
5. Invite the bot to your server with the `Send Messages` permission.

## 2. Get a Groq API key

Create an account at [console.groq.com](https://console.groq.com/keys) and generate an API key.

## 3. Configure locally

```bash
pip install -r requirements.txt
cp .env.example .env
```

Add your real values:

```env
DISCORD_TOKEN=your_discord_bot_token_here
GROQ_API_KEY=your_groq_api_key_here
```

## 4. Run it

```bash
python bot.py
```

Then use:

```text
!ask When is the next USF football game?
!search USF football schedule
```

## Deploying so it runs 24/7

This bot just needs `DISCORD_TOKEN` and `GROQ_API_KEY` as environment variables and a long-running process such as a background worker or a small VPS.

## Scavenger System Access

`/unlock` is a slash-only puzzle command. It accepts submissions only in the configured guild and the `#decoded` access terminal. A correct answer adds only the System Access role; it does not remove Scavenger or any other role. This only unlocks Discord channels. It does not update website progress or award the puzzle prize.

Add these settings to the private `.env` file (or the host's private environment):

```env
DISCORD_GUILD_ID=your_discord_server_id_here
SCAVENGER_ROLE_ID=your_scavenger_role_id_here
SYSTEM_ACCESS_ROLE_ID=your_system_access_role_id_here
ACCESS_CHANNEL_ID=your_decoded_channel_id_here
SYSTEM_ACCESS_CODE=your_private_puzzle_answer_here
```

Keep the real puzzle answer private. `.env` is excluded by `.gitignore`; do not put secrets in `env.example`, source code, tests, or README. If a required setting is missing or invalid, `/unlock` stays safely disabled without preventing the bot from starting. Restart the bot after changing its environment so the command tree and settings are reloaded.

### Discord Setup

1. Create the **System Access** role with zero server-wide permissions.
2. For the **System** category, deny **View Channel** to `@everyone` and allow **View Channel** and **Read Message History** to **System Access**.
3. Synchronize the locked child channels with the category, and check that other role/member overrides do not expose them.
4. Give the bot **Manage Roles** and place the bot's highest role above **System Access**.
5. Keep `#decoded` visible to **Scavenger** and allow application commands there. Set `ACCESS_CHANNEL_ID` to that channel's ID.

The bot validates that System Access exists, is not `@everyone` or managed, has zero server-wide permissions, and is below the bot's highest role before granting it. The bot does not create roles/channels or modify channel/category permissions. Wrong answers receive a private response; submissions are limited to five per user per minute.
