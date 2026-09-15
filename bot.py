import json
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv
from groq import Groq

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set. Add it to your .env file or host's env vars.")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is not set. Add it to your .env file or host's env vars.")

groq_client = Groq(api_key=GROQ_API_KEY)
GROQ_MODEL = "llama-3.1-8b-instant"


def get_groq_model_candidates() -> list[str]:
    """Return a model list ordered by the most likely available Groq model."""
    preferred = [
        GROQ_MODEL,
        "llama-3.3-70b-versatile",
        "llama-3.1-70b-versatile",
    ]
    seen = set()
    models = []
    for model in preferred:
        if model and model not in seen:
            seen.add(model)
            models.append(model)
    return models


intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Emoji -> role name mapping for the onboarding gateway.
# "USF 2026" is the base role granted to anyone who reacts to any option.
ROLE_MAPPING = {
    "🌴": "Tampa Campus",
    "☀️": "St. Pete Campus",
    "🏖️": "Sarasota-Manatee",
    "🧠": "Clinical Psychology",
    "💻": "Computer Science",
    "📈": "Finance",
    "🎓": "USF 2026",
}
BASE_ROLE_NAME = "USF 2026"


def parse_server_blueprint(raw_response: str) -> dict:
    """Extract a valid JSON server blueprint from Groq output."""
    cleaned = raw_response.strip()

    if "```" in cleaned:
        parts = cleaned.split("```")
        if len(parts) >= 2:
            cleaned = parts[1].strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()

    try:
        blueprint = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("Groq did not return valid JSON for the server blueprint.") from exc

    if not isinstance(blueprint, dict):
        raise ValueError("The server blueprint must be a JSON object.")

    name = blueprint.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("The blueprint is missing a valid 'name' field.")

    roles = blueprint.get("roles", [])
    if not isinstance(roles, list) or not roles:
        raise ValueError("The blueprint is missing a valid 'roles' list.")

    categories = blueprint.get("categories", [])
    if not isinstance(categories, list) or not categories:
        raise ValueError("The blueprint is missing a valid 'categories' list.")

    valid_categories = []
    for category in categories:
        if not isinstance(category, dict):
            raise ValueError("Each category must be a JSON object with a name and channels array.")
        category_name = category.get("name")
        channels = category.get("channels", [])
        if not isinstance(category_name, str) or not category_name.strip():
            raise ValueError("Each category in the blueprint needs a valid 'name'.")
        if not isinstance(channels, list) or not channels:
            raise ValueError(f"Category '{category_name}' is missing a valid 'channels' list.")

        voice_channels = category.get("voice_channels", [])
        if not isinstance(voice_channels, list):
            voice_channels = []

        valid_categories.append({
            "name": category_name.strip(),
            "channels": [str(channel).strip() for channel in channels if str(channel).strip()],
            "voice_channels": [str(channel).strip() for channel in voice_channels if str(channel).strip()],
        })

    blueprint["roles"] = [str(role).strip() for role in roles if str(role).strip()]
    blueprint["categories"] = valid_categories
    if "welcome_message" in blueprint and blueprint["welcome_message"] is not None:
        blueprint["welcome_message"] = str(blueprint["welcome_message"]).strip()
    if "description" in blueprint and blueprint["description"] is not None:
        blueprint["description"] = str(blueprint["description"]).strip()
    return blueprint


async def create_blueprint_server(guild: discord.Guild, blueprint: dict):
    """Create a Discord guild layout based on the AI-generated blueprint."""
    if blueprint.get("name"):
        await guild.edit(name=str(blueprint["name"]).strip())

    for role_name in blueprint.get("roles", []):
        if not discord.utils.get(guild.roles, name=role_name):
            await guild.create_role(name=role_name, reason="AI-generated server blueprint")

    for category in blueprint.get("categories", []):
        category_name = category["name"]
        category_obj = await guild.create_category(category_name)

        for channel_name in category["channels"]:
            await guild.create_text_channel(channel_name, category=category_obj)

        for voice_name in category.get("voice_channels", []):
            await guild.create_voice_channel(voice_name, category=category_obj)

    welcome_message = blueprint.get("welcome_message")
    if welcome_message:
        system_channel = guild.system_channel
        if system_channel:
            await system_channel.send(welcome_message)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} ({bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="Helping USF 2026 Bulls 🤘"))


# ---------------------------------------------------------------------------
# 1. Server architecture auto-builder
# ---------------------------------------------------------------------------
@bot.command()
@commands.has_permissions(administrator=True)
async def setup_usf(ctx: commands.Context):
    guild = ctx.guild
    await ctx.send("🏗️ Building USF Server Architecture... this might take a minute.")

    # Create roles that don't already exist.
    for role_name in ROLE_MAPPING.values():
        if not discord.utils.get(guild.roles, name=role_name):
            await guild.create_role(name=role_name, reason="USF Bot Auto-Setup")

    everyone_role = guild.default_role
    student_role = discord.utils.get(guild.roles, name=BASE_ROLE_NAME)

    # Lock down @everyone by default.
    locked_perms = discord.Permissions()
    locked_perms.update(read_messages=False, send_messages=False)
    await everyone_role.edit(permissions=locked_perms)

    # Gateway category: visible to everyone, read-only.
    gateway_overrides = {
        everyone_role: discord.PermissionOverwrite(read_messages=True, send_messages=False)
    }
    gateway_cat = await guild.create_category("🛂 GATEWAY")
    await guild.create_text_channel("rules", category=gateway_cat, overwrites=gateway_overrides)
    await guild.create_text_channel("get-roles", category=gateway_cat, overwrites=gateway_overrides)

    # Everything past the gateway requires the base student role.
    member_overrides = {
        everyone_role: discord.PermissionOverwrite(read_messages=False),
        student_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }

    commons_cat = await guild.create_category("🌴 THE COMMONS", overwrites=member_overrides)
    await guild.create_text_channel("general", category=commons_cat)
    await guild.create_text_channel("dorm-chat", category=commons_cat)

    academics_cat = await guild.create_category("📚 ACADEMICS", overwrites=member_overrides)
    await guild.create_text_channel("psychology-hub", category=academics_cat)
    await guild.create_text_channel("engineering-cs", category=academics_cat)
    await guild.create_text_channel("muma-business", category=academics_cat)

    await ctx.send("✅ USF Server Architecture built successfully!")


@setup_usf.error
async def setup_usf_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 You need Administrator permissions to run this command.")
    else:
        await ctx.send(f"⚠️ Something went wrong: {error}")


# ---------------------------------------------------------------------------
# 2. Reaction-role onboarding
# ---------------------------------------------------------------------------
@bot.command()
@commands.has_permissions(administrator=True)
async def setup_roles(ctx: commands.Context):
    embed = discord.Embed(
        title="🤘 Welcome to USF Class of 2026!",
        description=(
            "React to this message to get your campus and major roles, and unlock the server!\n\n"
            "**Campuses:**\n"
            "🌴 : Tampa Campus\n"
            "☀️ : St. Pete Campus\n"
            "🏖️ : Sarasota-Manatee\n\n"
            "**Popular Majors:**\n"
            "🧠 : Clinical Psychology\n"
            "💻 : Computer Science\n"
            "📈 : Finance\n\n"
            "*Reacting to any of these will grant you the **USF 2026** role!*"
        ),
        color=discord.Color.green(),
    )
    msg = await ctx.send(embed=embed)

    for emoji in ROLE_MAPPING:
        if emoji != "🎓":  # base role isn't a reaction option on its own
            await msg.add_reaction(emoji)


@setup_roles.error
async def setup_roles_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 You need Administrator permissions to run this command.")
    else:
        await ctx.send(f"⚠️ Something went wrong: {error}")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    emoji = str(payload.emoji)
    if emoji not in ROLE_MAPPING:
        return

    specific_role = discord.utils.get(guild.roles, name=ROLE_MAPPING[emoji])
    base_role = discord.utils.get(guild.roles, name=BASE_ROLE_NAME)
    member = guild.get_member(payload.user_id)

    if member is None:
        return
    if specific_role:
        await member.add_roles(specific_role)
    if base_role:
        await member.add_roles(base_role)


# ---------------------------------------------------------------------------
# 3. Groq-powered server builder
# ---------------------------------------------------------------------------
@bot.command()
@commands.has_permissions(administrator=True)
async def create_server(ctx: commands.Context, *, prompt: str):
    """Use Groq to design and build a Discord server from a plain-language prompt."""
    if not prompt.strip():
        await ctx.send("🧠 Add a short description for the server you want, for example: `!create_server A cozy gaming community for friends`")
        return

    async with ctx.typing():
        try:
            last_error = None
            for model_name in get_groq_model_candidates():
                try:
                    chat_completion = groq_client.chat.completions.create(
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "You are a senior Discord server architect. Return only valid JSON, with no code fences, "
                                    "using this exact schema: { \"name\": \"string\", \"description\": \"string\", \"roles\": [\"string\"], "
                                    "\"welcome_message\": \"string\", \"categories\": [{\"name\": \"string\", \"channels\": [\"string\"], "
                                    "\"voice_channels\": [\"string\"]}] }. Keep the server from being too overwhelming, choose a memorable name, "
                                    "include 3-6 roles, create 2-4 categories, make sure each category has at least 2 text channels, "
                                    "include 1-2 voice channels per category, and set a friendly welcome message."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                        model=model_name,
                        temperature=0.7,
                    )
                    raw_response = chat_completion.choices[0].message.content
                    blueprint = parse_server_blueprint(raw_response)
                    await create_blueprint_server(ctx.guild, blueprint)

                    summary = (
                        f"✅ Server blueprint created: **{blueprint['name']}**\n\n"
                        f"**Roles:** {', '.join(blueprint['roles'])}\n"
                        f"**Categories:** {', '.join(category['name'] for category in blueprint['categories'])}"
                    )
                    await ctx.send(summary)
                    return
                except Exception as exc:  # noqa: BLE001 - keep trying other models
                    last_error = exc
                    if "model" not in str(exc).lower() or "not found" not in str(exc).lower():
                        raise
            if last_error is not None:
                raise last_error

            summary = (
                f"✅ Server blueprint created: **{blueprint['name']}**\n\n"
                f"**Roles:** {', '.join(blueprint['roles'])}\n"
                f"**Categories:** {', '.join(category['name'] for category in blueprint['categories'])}"
            )
            await ctx.send(summary)
        except ValueError as exc:
            await ctx.send(f"⚠️ Groq returned an invalid server plan: {exc}")
        except Exception as exc:  # noqa: BLE001 - report and keep the bot alive
            error_text = str(exc).lower()
            if "api key" in error_text or "401" in error_text or "unauthorized" in error_text:
                await ctx.send("❌ The Groq API key is missing, invalid, or not loaded from your `.env` file.")
            elif "rate limit" in error_text or "429" in error_text:
                await ctx.send("❌ Groq is rate-limiting requests right now. Please wait a moment and try again.")
            elif "model" in error_text and ("not found" in error_text or "unavailable" in error_text):
                await ctx.send("❌ The selected Groq model is unavailable for your account. I can switch to a more compatible model.")
            else:
                await ctx.send(f"❌ Groq request failed: {exc}")
            print(f"Groq API Error: {exc}")


@create_server.error
async def create_server_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 You need Administrator permissions to run this command.")
    else:
        await ctx.send(f"⚠️ Something went wrong: {error}")


# ---------------------------------------------------------------------------
# 4. Groq-powered AI assistant
# ---------------------------------------------------------------------------
@bot.command()
async def ask(ctx: commands.Context, *, question: str):
    """Ask the AI assistant a question, e.g. !ask when was USF founded?"""
    async with ctx.typing():
        try:
            last_error = None
            for model_name in get_groq_model_candidates():
                try:
                    chat_completion = groq_client.chat.completions.create(
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "You are BullBot, a helpful AI assistant for the University of "
                                    "South Florida (USF) Class of 2026 Discord server. Keep answers "
                                    "concise, friendly, and helpful."
                                ),
                            },
                            {"role": "user", "content": question},
                        ],
                        model=model_name,
                    )
                    answer = chat_completion.choices[0].message.content

                    # Discord messages are capped at 2000 characters.
                    if len(answer) > 2000:
                        answer = answer[:1996] + "..."

                    await ctx.send(answer)
                    return
                except Exception as exc:  # noqa: BLE001 - keep trying other models
                    last_error = exc
                    if "model" not in str(exc).lower() or "not found" not in str(exc).lower():
                        raise
            if last_error is not None:
                raise last_error
        except Exception as exc:  # noqa: BLE001 - report and keep the bot alive
            await ctx.send("❌ Sorry, I'm having trouble connecting to my AI brain right now.")
            print(f"Groq API Error: {exc}")


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
