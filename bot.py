import os

import discord
from discord.ext import commands
from dotenv import load_dotenv
from groq import Groq

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set. Add it to your .env file or host's env vars.")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is not set. Add it to your .env file or host's env vars.")

groq_client = Groq(api_key=GROQ_API_KEY)

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
# 3. Groq-powered AI assistant
# ---------------------------------------------------------------------------
@bot.command()
async def ask(ctx: commands.Context, *, question: str):
    """Ask the AI assistant a question, e.g. !ask when was USF founded?"""
    async with ctx.typing():
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
                model="llama-3.3-70b-versatile",
            )
            answer = chat_completion.choices[0].message.content

            # Discord messages are capped at 2000 characters.
            if len(answer) > 2000:
                answer = answer[:1996] + "..."

            await ctx.send(answer)
        except Exception as exc:  # noqa: BLE001 - report and keep the bot alive
            await ctx.send("❌ Sorry, I'm having trouble connecting to my AI brain right now.")
            print(f"Groq API Error: {exc}")


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
