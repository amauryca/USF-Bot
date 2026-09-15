# USF Bot

A Discord bot for the USF Class of 2026 server. It auto-builds the server's
categories/channels/roles, runs a reaction-role onboarding flow, and answers
student questions using Groq's `llama-3.3-70b-versatile` model.

## Commands

| Command | Who | What it does |
|---|---|---|
| `!setup_usf` | Admin only | Locks down `@everyone` and creates the Gateway, Commons, and Academics categories/channels/roles. |
| `!setup_roles` | Admin only | Posts the reaction-role embed in `#get-roles`. |
| `!create_server <idea>` | Admin only | Uses Groq to generate a complete server blueprint and creates the roles/categories/channels. |
| `!ask <question>` | Everyone | Asks Groq's Llama 3.3 70B model a question. |

## 1. Create the Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**.
2. Open the **Bot** tab → under **Privileged Gateway Intents**, turn ON **Server Members Intent** and **Message Content Intent**.
3. Click **Reset Token** and copy the bot token — you'll need it below.
4. Go to **OAuth2 → URL Generator**, check `bot` and `applications.commands`, and under bot permissions check `Administrator`. Open the generated URL and invite the bot to your (empty) server.

## 2. Get a Groq API key

Create an account at [console.groq.com](https://console.groq.com/keys) and generate an API key.

## 3. Configure locally

```bash
git clone <your-repo-url>
cd usf-bot
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` with your real values:

```env
DISCORD_TOKEN=your_discord_bot_token_here
GROQ_API_KEY=your_groq_api_key_here
```

`.env` is gitignored — never commit real tokens.

## 4. Run it

```bash
python bot.py
```

In your server:

1. Run `!setup_usf` to build the categories, channels, and roles.
2. Go to `#get-roles` and run `!setup_roles` to post the onboarding embed.
3. Run `!create_server A private study and gaming community with roles for Admin, Moderator, Member, and categories like Community, Study Hall, and Events` to let Groq scaffold an entire server layout from a prompt.
4. Try `!ask When was USF founded?` in any channel.

## Deploying so it runs 24/7

This code is host-agnostic — it just needs `DISCORD_TOKEN` and `GROQ_API_KEY`
as environment variables and a process that keeps `python bot.py` running.
Common free/low-cost options: Railway, Render (background worker), or a small
VPS with a process manager (`systemd`, `pm2`, or `screen`/`tmux` + a restart
loop). Whichever you pick, set the same two environment variables in that
platform's dashboard instead of uploading `.env`.
