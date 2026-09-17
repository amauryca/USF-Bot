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
