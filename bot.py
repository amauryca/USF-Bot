import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import discord
from discord.ext import commands
from dotenv import load_dotenv
from groq import Groq

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set. Add it to your .env file or host's env vars.")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is not set. Add it to your .env file or host's env vars.")

groq_client = Groq(api_key=GROQ_API_KEY)
GROQ_MODEL = os.getenv("GROQ_MODEL", "groq/compound")


def get_groq_model_candidates() -> list[str]:
    """Return a prioritized list of Groq models while preserving env overrides."""
    preferred = []
    env_model = os.getenv("GROQ_MODEL")
    if env_model and env_model.strip():
        preferred.append(env_model.strip())

    if GROQ_MODEL and GROQ_MODEL.strip() and GROQ_MODEL not in preferred:
        preferred.append(GROQ_MODEL.strip())

    for model_name in [
        "groq/compound",
        "llama-3.3-70b-versatile",
    ]:
        if model_name not in preferred:
            preferred.append(model_name)

    return preferred


def build_usf_context_prompt(query: str) -> str:
    """Add time-sensitive USF framing to user questions for better answers."""
    current_date = datetime.now().strftime("%A, %B %d, %Y")
    return (
        f"Current date: {current_date}. You are the USF assistant for the University of South Florida. "
        "Answer current USF-related questions about athletics, admissions, campus life, classes, events, "
        "football, and other student topics. If the user asks about upcoming events or schedules, be careful "
        "and say to check the official USF calendar or athletics pages for final confirmation. Keep answers concise, "
        f"friendly, and helpful.\n\nUser question: {query}"
    )


def fetch_search_snippets(query: str, max_results: int = 3) -> str:
    """Try a lightweight web search to gather current info for USF questions."""
    try:
        search_url = "https://duckduckgo.com/html/?q=" + quote(query)
        request = Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=12) as response:
            html = response.read().decode("utf-8", "ignore")

        matches = re.findall(
            r'<a rel="nofollow" class="result-link"[^>]*>(.*?)</a>.*?<a class="result-snippet"[^>]*>(.*?)</a>',
            html,
            re.S | re.I,
        )
        snippets = []
        for title, snippet in matches[:max_results]:
            clean_title = re.sub(r"<.*?>", "", title).strip()
            clean_snippet = re.sub(r"<.*?>", "", snippet).strip()
            if clean_title or clean_snippet:
                snippets.append(f"{clean_title} — {clean_snippet}")

        if snippets:
            return "\n\n".join(snippets)
    except Exception:
        pass

    return "No live web snippets were available for this query. Use official USF sources for final verification."


SLUR_PATTERNS = [
    re.compile(r"\b(?:nigga|nigger|n1gga|n!gga)\b", re.I),
    re.compile(r"\b(?:faggot|fag)\b", re.I),
    re.compile(r"\b(?:retard|retarded)\b", re.I),
    re.compile(r"\b(?:chink|spic|beaner|coon)\b", re.I),
]


def contains_slur(text: str) -> bool:
    """Return True when a message includes a known slur pattern."""
    normalized = " ".join((text or "").split())
    return any(pattern.search(normalized) for pattern in SLUR_PATTERNS)


MODERATION_COUNTS = defaultdict(int)


async def handle_slur_violation(member: discord.Member, channel: discord.TextChannel):
    """Timeout users for slur violations and ban them after repeated offenses."""
    key = str(member.id)
    MODERATION_COUNTS[key] += 1
    count = MODERATION_COUNTS[key]

    if count >= 3:
        try:
            await member.ban(reason="Repeated slur usage")
            await channel.send(f"🚫 {member.mention} was banned for repeated slur usage.")
        except Exception:
            pass
        MODERATION_COUNTS[key] = 0
        return

    timeout_duration = timedelta(minutes=10)
    try:
        await member.timeout(timeout_duration, reason="Slur usage detected")
        await channel.send(f"⏱️ {member.mention} has been timed out for 10 minutes for slur usage. This is warning {count}/3.")
    except Exception:
        pass


async def ask_groq(question: str, extra_context: str = "") -> str:
    """Ask Groq for an answer, trying candidate models until one works."""
    model_candidates = get_groq_model_candidates()
    system_prompt = build_usf_context_prompt(question)
    if extra_context:
        system_prompt += f"\n\nCurrent web context:\n{extra_context}"

    last_error = None
    for model_name in model_candidates:
        try:
            response = groq_client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                temperature=0.7,
                max_tokens=600,
            )
            return response.choices[0].message.content.strip()
        except Exception as exc:  # pragma: no cover - runtime dependent
            last_error = exc
            continue

    if last_error:
        raise RuntimeError(f"All Groq model attempts failed: {last_error}")
    raise RuntimeError("No Groq models were available.")


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def staff_only():
    async def predicate(ctx: commands.Context):
        if ctx.guild is None:
            return False

        if ctx.author.guild_permissions.administrator:
            return True

        staff_role = discord.utils.get(ctx.guild.roles, name="staff")
        if staff_role is None:
            return False

        return staff_role in ctx.author.roles

    return commands.check(predicate)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} ({bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="Hello, I'm the USF Bot! Go Bulls 🤘"))


@bot.command()
async def ping(ctx: commands.Context):
    await ctx.send("Pong! 🏓 I am online and ready to help.")


@bot.command()
async def hello(ctx: commands.Context):
    await ctx.send("Hello! I’m the USF Bot! Go Bulls 🤘")


@bot.command()
async def status(ctx: commands.Context):
    await ctx.send("Hello, I’m the USF Bot! Go Bulls 🤘\nI can help with USF info, server tools, and moderation.")


@bot.command()
async def about(ctx: commands.Context):
    await ctx.send(
        "**USF Bot**\n"
        "A Discord helper for both campus life and server management.\n"
        "Use me for USF questions, event updates, moderation, channel tools, and general server support."
    )


@bot.command(name="commands")
async def list_commands(ctx: commands.Context):
    command_list = (
        "**USF Bot Command Guide**\n\n"
        "**General**\n"
        "`!ping` - Check bot availability\n"
        "`!hello` - Friendly greeting\n"
        "`!status` - Show bot status\n"
        "`!about` - Brief bot overview\n"
        "`!commands` - Show this menu\n"
        "`!serverinfo` - Show server details\n"
        "`!userinfo @user` - Show user info\n"
        "`!channelinfo` - Show current channel info\n"
        "`!report @user <reason>` - Send a report for moderation review\n\n"
        "**Server Tools**\n"
        "`!create_channel <name>` - Create a text channel in the current category\n"
        "`!lockdown` - Lock the current channel to staff only\n"
        "`!clear <amount>` - Delete recent messages\n"
        "`!kick @user <reason>` - Kick a member\n"
        "`!ban @user <reason>` - Ban a member\n"
        "`!timeout @user <minutes> [reason]` - Time out a member\n"
        "`!untimeout @user` - Remove a timeout\n"
        "`!warn @user <reason>` - Temporarily warn a member\n\n"
        "**USF & Campus**\n"
        "`!ask <question>` - Ask a USF question with Groq\n"
        "`!search <query>` - Search for current USF-related info\n"
        "`!today` - What's happening at USF today\n"
        "`!football` - Latest USF football updates\n"
        "`!sports [team]` - USF sports schedule and updates\n"
        "`!calendar` - Academic and campus events\n"
        "`!campus [name]` - Campus information\n"
        "`!dining` - Dining options and hours\n"
        "`!parking` - Parking rules and garages\n"
        "`!admissions` - Admissions information\n"
        "`!financialaid` - Financial aid info\n"
        "`!academic` - Academic calendar and registration\n"
        "`!major <name>` - Learn about a major\n"
        "`!transit` - Bull Runner and transportation\n"
        "`!weather` - Weather near USF\n"
        "`!news` - Latest USF news\n"
        "`!events` - Student events and activities\n"
        "`!resources` - Student support resources\n"
        "`!bulls` - Fun USF trivia or fact\n"
        "`!sources <topic>` - Official USF links\n"
    )
    await ctx.send(command_list)


@bot.command(name="serverinfo")
async def serverinfo(ctx: commands.Context):
    guild = ctx.guild
    member_count = guild.member_count
    text_channels = len(guild.text_channels)
    voice_channels = len(guild.voice_channels)
    created = guild.created_at.strftime("%Y-%m-%d")
    await ctx.send(
        f"**{guild.name}**\n"
        f"Members: {member_count}\n"
        f"Text channels: {text_channels}\n"
        f"Voice channels: {voice_channels}\n"
        f"Created: {created}\n"
        f"Owner: {guild.owner.mention if guild.owner else 'Unknown'}"
    )


@bot.command(name="userinfo")
async def userinfo(ctx: commands.Context, member: discord.Member = None):
    target = member or ctx.author
    roles = " ".join(role.mention for role in target.roles[1:]) or "No extra roles"
    joined = target.joined_at.strftime("%Y-%m-%d") if target.joined_at else "Unknown"
    created = target.created_at.strftime("%Y-%m-%d") if target.created_at else "Unknown"
    await ctx.send(
        f"**User info for {target.mention}**\n"
        f"ID: {target.id}\n"
        f"Joined: {joined}\n"
        f"Account created: {created}\n"
        f"Roles: {roles}"
    )


@bot.command(name="channelinfo")
async def channelinfo(ctx: commands.Context):
    channel = ctx.channel
    await ctx.send(
        f"**Channel info**\n"
        f"Name: {channel.mention}\n"
        f"Type: {channel.type}\n"
        f"Category: {channel.category.name if channel.category else 'None'}\n"
        f"Created: {channel.created_at.strftime('%Y-%m-%d')}"
    )


@bot.command(name="create_channel")
@staff_only()
@commands.has_permissions(manage_channels=True)
async def create_channel(ctx: commands.Context, channel_name: str):
    if not channel_name or not channel_name.strip():
        await ctx.send("📣 Use `!create_channel <name>` to create a text channel.")
        return

    safe_name = channel_name.strip().lower().replace(" ", "-")
    category = ctx.channel.category
    new_channel = await ctx.guild.create_text_channel(safe_name, category=category)
    await ctx.send(f"📣 Created {new_channel.mention} in {category.mention if category else 'the server'}.")


@bot.command(name="lockdown")
@staff_only()
@commands.has_permissions(manage_channels=True)
async def lockdown_channel(ctx: commands.Context):
    overwrites = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrites.send_messages = False
    overwrites.read_messages = False
    overwrites.read_message_history = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrites)
    await ctx.send(f"🔒 {ctx.channel.mention} has been locked down to staff only.")


@bot.command(name="clear")
@staff_only()
@commands.has_permissions(manage_messages=True)
async def clear_messages(ctx: commands.Context, amount: int = 10):
    if amount <= 0:
        await ctx.send("🧹 Use `!clear <number>` with a positive number.")
        return

    deleted = await ctx.channel.purge(limit=amount)
    await ctx.send(f"🧹 Deleted {len(deleted)} messages.", delete_after=3)


@bot.command(name="kick")
@staff_only()
@commands.has_permissions(kick_members=True)
async def kick_member(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    await member.kick(reason=reason)
    await ctx.send(f"👢 {member.mention} was kicked. Reason: {reason}")


@bot.command(name="ban")
@staff_only()
@commands.has_permissions(ban_members=True)
async def ban_member(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    await member.ban(reason=reason)
    await ctx.send(f"🚫 {member.mention} was banned. Reason: {reason}")


@bot.command(name="timeout")
@staff_only()
@commands.has_permissions(moderate_members=True)
async def timeout_member(ctx: commands.Context, member: discord.Member, minutes: int = 10, *, reason: str = "No reason provided"):
    if minutes <= 0:
        await ctx.send("⏱️ Use `!timeout @user <minutes> [reason]` with a positive number.")
        return

    duration = timedelta(minutes=minutes)
    await member.timeout(duration, reason=reason)
    await ctx.send(f"⏱️ {member.mention} was timed out for {minutes} minutes. Reason: {reason}")


@bot.command(name="untimeout")
@staff_only()
@commands.has_permissions(moderate_members=True)
async def untimeout_member(ctx: commands.Context, member: discord.Member):
    await member.timeout(None, reason="Removed by moderator")
    await ctx.send(f"✅ {member.mention} is no longer timed out.")


@bot.command(name="warn")
@staff_only()
@commands.has_permissions(moderate_members=True)
async def warn_member(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    await member.timeout(timedelta(minutes=5), reason=f"Warning issued by {ctx.author}: {reason}")
    await ctx.send(f"⚠️ {member.mention} was warned and timed out for 5 minutes. Reason: {reason}")


@bot.command(name="report")
async def report_member(ctx: commands.Context, member: discord.Member, *, reason: str):
    mod_channel = discord.utils.get(ctx.guild.text_channels, name="mod-logs") or discord.utils.get(ctx.guild.text_channels, name="moderation")
    if mod_channel:
        await mod_channel.send(
            f"📣 Report received by {ctx.author.mention} against {member.mention}\nReason: {reason}"
        )
    await ctx.send(f"📣 Your report against {member.mention} has been submitted.")


@bot.command(name="ask")
async def ask(ctx: commands.Context, *, question: str):
    if not question.strip():
        await ctx.send("❓ Ask me a USF question like: `!ask When is the next USF football game?`")
        return

    try:
        extra_context = fetch_search_snippets(question)
        answer = await ask_groq(question, extra_context)
        await ctx.send(answer)
    except Exception as exc:
        await ctx.send(f"⚠️ I couldn't answer that right now. Error: {exc}")


@bot.command(name="search")
async def search(ctx: commands.Context, *, query: str):
    if not query.strip():
        await ctx.send("🔎 Use `!search <topic>` to look up current USF-related information.")
        return

    try:
        snippets = fetch_search_snippets(query)
        if not snippets or "No live web snippets" in snippets:
            await ctx.send("🔎 I couldn’t pull live results for that topic, but I can still answer from the USF context if you ask directly.")
            return

        prompt = (
            "You are a helpful USF assistant. Use the web snippets below to answer the user's query. "
            "Be concise, cite that the information may need final verification, and stay focused on USF-related facts.\n\n"
            f"Web snippets:\n{snippets}\n\nUser query: {query}"
        )
        response = groq_client.chat.completions.create(
            model=get_groq_model_candidates()[0],
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": query},
            ],
            temperature=0.7,
            max_tokens=500,
        )
        await ctx.send(response.choices[0].message.content.strip())
    except Exception as exc:
        await ctx.send(f"⚠️ I couldn’t search for that right now. Error: {exc}")


async def send_usf_topic_answer(ctx: commands.Context, topic: str):
    """Answer a preset USF topic using live snippets and Groq."""
    try:
        snippets = fetch_search_snippets(f"USF {topic}")
        answer = await ask_groq(f"What is the latest information about USF {topic}?", snippets)
        await ctx.send(answer)
    except Exception as exc:
        await ctx.send(f"⚠️ I couldn’t get the latest USF information right now. Error: {exc}")


@bot.command()
async def today(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "events and activities happening today")


@bot.command()
async def football(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "football schedule and upcoming games")


@bot.command()
async def sports(ctx: commands.Context, *, team: str = "all sports"):
    await send_usf_topic_answer(ctx, f"{team} sports schedule and upcoming games")


@bot.command()
async def calendar(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "academic calendar, deadlines, and upcoming campus events")


@bot.command()
async def campus(ctx: commands.Context, *, name: str = "all campuses"):
    await send_usf_topic_answer(ctx, f"{name} campus information")


@bot.command()
async def dining(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "dining locations, menus, and hours")


@bot.command()
async def parking(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "parking permits, rules, garages, and availability")


@bot.command()
async def admissions(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "admissions requirements and application deadlines")


@bot.command()
async def financialaid(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "financial aid, FAFSA, scholarships, and grants")


@bot.command()
async def academic(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "academic calendar, registration, drop dates, and exams")


@bot.command()
async def major(ctx: commands.Context, *, name: str):
    await send_usf_topic_answer(ctx, f"the {name} major or program")


@bot.command()
async def transit(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "Bull Runner routes and transportation")


@bot.command()
async def weather(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "current weather near the Tampa campus")


@bot.command()
async def news(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "latest news and announcements")


@bot.command()
async def events(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "student events, clubs, and campus activities")


@bot.command()
async def resources(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "counseling, tutoring, health services, and student support")


@bot.command()
async def bulls(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "a fun fact or trivia question about USF")


@bot.command()
async def sources(ctx: commands.Context, *, topic: str):
    official_sources = {
        "general": "https://www.usf.edu/",
        "athletics": "https://gousfbulls.com/",
        "football": "https://gousfbulls.com/sports/football",
        "calendar": "https://www.usf.edu/student-affairs/events/",
        "news": "https://www.usf.edu/news/",
        "admissions": "https://www.usf.edu/admissions/",
        "academic": "https://www.usf.edu/academic-calendar/",
        "dining": "https://usf.campusdish.com/",
        "transit": "https://www.usf.edu/administrative-services/parking/",
    }
    topic_key = topic.lower().strip()
    matching_sources = [url for key, url in official_sources.items() if key in topic_key]
    if not matching_sources:
        matching_sources = [official_sources["general"], official_sources["athletics"]]
    await ctx.send("Official USF sources:\n" + "\n".join(matching_sources))


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    if contains_slur(message.content):
        try:
            await message.delete()
        except Exception:
            pass

        await handle_slur_violation(message.author, message.channel)

    await bot.process_commands(message)


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
