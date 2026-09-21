import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import discord
from discord.ext import commands
from dotenv import load_dotenv

try:
    from groq import Groq
except ImportError:  # pragma: no cover - optional provider dependency
    Groq = None

try:
    from google import genai
    from google.genai import types
except ImportError:  # pragma: no cover - optional provider dependency
    genai = None
    types = None

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
AI_PROVIDER = os.getenv("AI_PROVIDER", "google").lower()

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set. Add it to your .env file or host's env vars.")

if AI_PROVIDER == "groq" and not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is not set. Add it to your .env file or host's env vars.")

if AI_PROVIDER == "google" and not GOOGLE_API_KEY:
    raise RuntimeError("GOOGLE_API_KEY is not set. Add it to your .env file or host's env vars.")

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile")
GOOGLE_MODEL = os.getenv("GOOGLE_MODEL", "gemini-2.5-flash")

groq_client = Groq(api_key=GROQ_API_KEY) if Groq and GROQ_API_KEY else None


def get_ai_provider() -> str:
    provider = os.getenv("AI_PROVIDER", "google").lower()
    if provider == "groq":
        return "google"
    if provider in {"", "searxng", "search"}:
        return "google"
    return provider


def get_groq_model_candidates() -> list[str]:
    candidates = []
    env_model = os.getenv("GROQ_MODEL")
    if env_model:
        candidates.append(env_model)
    if GROQ_MODEL not in candidates:
        candidates.append(GROQ_MODEL)
    return candidates


def get_google_model_name() -> str:
    return os.getenv("GOOGLE_MODEL", "gemini-2.5-flash")


def build_usf_context_prompt(query: str) -> str:
    """Always anchor the assistant around USF and reinforce a pro-USF tone."""
    current_date = datetime.now().strftime("%A, %B %d, %Y")
    return (
        f"Current date: {current_date}. You are the official USF assistant for the University of South Florida. "
        "You are always USF-focused, helpful, and enthusiastic about USF. "
        "Speak positively about USF, its students, athletics, campus, events, academics, and community. "
        "When answering about upcoming events or schedules, say to verify the official USF calendar or athletics page for final confirmation. "
        "Keep answers concise, friendly, and proud of USF. "
        "If a question is not about USF, gently redirect it back to USF or explain how it connects to campus life. "
        f"\n\nUser question: {query}"
    )


def trim_text_for_model(text: str, max_chars: int = 2200) -> str:
    """Trim long prompt text so Groq requests stay within safe payload limits."""
    if text is None:
        return ""

    cleaned = " ".join((str(text)).split())
    if len(cleaned) <= max_chars:
        return cleaned

    return cleaned[: max_chars - 3].rstrip() + "..."


def build_context_prompt(question: str, extra_context: str = "", max_context_chars: int = 700) -> str:
    """Build a compact USF prompt that keeps web context under provider limits."""
    prompt = build_usf_context_prompt(question)
    if extra_context:
        prompt += f"\n\nCurrent web context:\n{trim_text_for_model(extra_context, max_context_chars)}"
    if len(prompt) > 3500:
        prompt = build_usf_context_prompt(question)
        compact_context = trim_text_for_model(extra_context, 400)
        if compact_context:
            prompt += f"\n\nCurrent web context:\n{compact_context}"
    return prompt


def normalize_search_snippets(snippets: list[str], max_items: int = 5, max_chars: int = 220) -> list[str]:
    """Keep only the highest-value snippets, removing duplicates and trimming hard for model input."""
    cleaned = []
    seen = set()

    for snippet in snippets or []:
        text = " ".join(str(snippet or "").split())
        if not text:
            continue

        key = text.lower()
        if key in seen:
            continue
        seen.add(key)

        if len(text) > max_chars:
            text = text[: max_chars - 3].rstrip(" .;:,") + "..."

        cleaned.append(text)
        if len(cleaned) >= max_items:
            break

    return cleaned


def summarize_search_results(query: str, context: str) -> str:
    """Turn SearxNG results into a readable USF answer without relying on Groq or Gemini."""
    lines = [line.strip() for line in str(context or "").splitlines() if line.strip()]
    top = []
    seen = set()
    for line in lines[:6]:
        clean = re.sub(r"\s+", " ", line)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        top.append(clean)

    if not top:
        return (
            f"**USF search for: {query}**\n\n"
            "I could not find any current live results for that topic right now. "
            "Check the official USF athletics or university pages for final confirmation."
        )

    joined = "\n• ".join(top)
    return (
        f"**USF search: {query}**\n\n"
        f"• {joined}\n\n"
        "Use the official USF athletics or university pages for final confirmation."
    )


NCAA_API_BASE = "https://ncaa-api.henrygd.me"
SEARXNG_URL = os.getenv("SEARXNG_URL", "").rstrip("/")
SEARXNG_CLIENT_IP = os.getenv("SEARXNG_CLIENT_IP", "8.8.8.8")
NICKNAME_CHANNEL_ID = 1551719938786332772


def build_game_embed(title: str, away_team: str, home_team: str, status: str, date_text: str, away_score=None, home_score=None, description: str = "") -> discord.Embed:
    """Create a clean Discord embed for NCAA game information, including real scores when available."""
    embed = discord.Embed(title=title, description=description or "USF game update", color=discord.Color.gold())
    embed.add_field(name="Matchup", value=f"{away_team} vs {home_team}", inline=False)

    if away_score is not None and home_score is not None:
        embed.add_field(name="Score", value=f"{away_team} {away_score} - {home_team} {home_score}", inline=False)
    else:
        embed.add_field(name="Score", value="No score posted yet", inline=False)

    embed.add_field(name="Status", value=str(status or "Status unknown"), inline=True)
    embed.add_field(name="Date", value=str(date_text or "Unknown"), inline=True)
    return embed


def fetch_ncaa_json(path: str):
    """Fetch JSON from the public NCAA API, returning {} on network or schema failures."""
    if not path:
        return {}

    url = f"{NCAA_API_BASE.rstrip('/')}/{path.lstrip('/')}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8", "ignore"))
    except Exception:
        return {}


def safe_discord_text(text: str, max_chars: int = 3800) -> str:
    """Keep Discord payloads under the character limit."""
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


EMBED_COLOR_NAMES = {
    "gold": discord.Color.gold(),
    "usf": discord.Color.gold(),
    "green": discord.Color.green(),
    "red": discord.Color.red(),
    "blue": discord.Color.blue(),
    "purple": discord.Color.purple(),
    "orange": discord.Color.orange(),
    "teal": discord.Color.teal(),
    "blurple": discord.Color.blurple(),
    "black": discord.Color.default(),
}


def parse_embed_color(value: str) -> discord.Color:
    """Accept a known color name or a hex code, defaulting to USF gold."""
    if not value:
        return discord.Color.gold()

    name = value.strip().lower()
    if name in EMBED_COLOR_NAMES:
        return EMBED_COLOR_NAMES[name]

    hex_value = name.lstrip("#")
    if re.fullmatch(r"[0-9a-f]{6}", hex_value):
        return discord.Color(int(hex_value, 16))

    return discord.Color.gold()


def is_safe_embed_url(url: str) -> bool:
    """Only allow http/https URLs for embed images to avoid unsupported or unsafe schemes."""
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def build_ai_answer_embed(title: str, query: str, answer: str, sources: list[str] = None) -> discord.Embed:
    """Create a Discord embed for AI-generated USF answers, including source links when available."""
    embed = discord.Embed(title=safe_discord_text(title, 250), description=safe_discord_text(answer, 4000), color=discord.Color.gold())
    if query:
        embed.add_field(name="Question", value=safe_discord_text(query, 250), inline=False)
    if sources:
        joined = "\n".join(sources[:5])
        embed.add_field(name="Sources", value=safe_discord_text(joined, 1000), inline=False)
    return embed


def is_usf_team_name(name: str) -> bool:
    """Recognize likely USF team names in NCAA payloads."""
    if not name:
        return False
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    variants = {
        "usf",
        "southflorida",
        "southfla",
        "bulls",
    }
    return any(token in normalized for token in variants)


def extract_ncaa_game_summary(payload):
    """Normalize different NCAA API payload shapes into a readable USF game summary."""
    if not payload:
        return "I couldn’t find any USF-related NCAA game data right now."

    games = []
    if isinstance(payload, dict):
        for key in ("games", "items", "data", "scoreboard", "events"):
            value = payload.get(key)
            if isinstance(value, list):
                games.extend(value)
        games_by_date = payload.get("gamesByDate")
        if isinstance(games_by_date, dict):
            for value in games_by_date.values():
                if isinstance(value, list):
                    games.extend(value)
    elif isinstance(payload, list):
        games = payload

    if not games:
        return "I couldn’t find any USF-related NCAA game data right now."

    usf_matches = []
    for entry in games[:10]:
        game = entry.get("game") if isinstance(entry, dict) and isinstance(entry.get("game"), dict) else entry
        if not isinstance(game, dict):
            continue

        home = game.get("home") or game.get("homeTeam") or game.get("home_team") or {}
        away = game.get("away") or game.get("awayTeam") or game.get("away_team") or {}

        home_names = home.get("names") if isinstance(home, dict) else {}
        away_names = away.get("names") if isinstance(away, dict) else {}

        home_name = (
            (home_names.get("full") if isinstance(home_names, dict) else "")
            or (home_names.get("short") if isinstance(home_names, dict) else "")
            or home.get("name")
            or home.get("displayName")
            or home.get("school")
            or ""
        )
        away_name = (
            (away_names.get("full") if isinstance(away_names, dict) else "")
            or (away_names.get("short") if isinstance(away_names, dict) else "")
            or away.get("name")
            or away.get("displayName")
            or away.get("school")
            or ""
        )

        names = [home_name, away_name]
        if not any(is_usf_team_name(name) for name in names):
            continue

        generic_names = {"", "home team", "away team", "team"}
        if not away_name or not home_name or away_name.lower() in generic_names or home_name.lower() in generic_names:
            continue

        status = game.get("gameState") or game.get("status") or game.get("state") or "Status unknown"
        start_time = game.get("startDate") or game.get("startTime") or game.get("start_time") or game.get("date") or ""
        if start_time and "T" in start_time:
            start_time = start_time.split("T", 1)[0]

        away_score = game.get("awayScore") or game.get("away_score")
        home_score = game.get("homeScore") or game.get("home_score")
        score_text = ""
        if away_score is not None and home_score is not None:
            score_text = f" — {away_name} {away_score}, {home_name} {home_score}"

        if start_time:
            usf_matches.append(f"{away_name} vs {home_name}{score_text} — {status} ({start_time})")
        else:
            usf_matches.append(f"{away_name} vs {home_name}{score_text} — {status}")

    if not usf_matches:
        return "I couldn’t find any USF-related NCAA game data right now."

    summary_text = "USF-related NCAA game info:\n" + "\n".join(usf_matches[:5])
    if "Away team vs Home team" in summary_text or ("away team" in summary_text.lower() and "home team" in summary_text.lower()):
        return "I couldn’t find any USF-related NCAA game data right now."
    return safe_discord_text(summary_text, 1800)


def extract_any_ncaa_game_summary(payload):
    """Return the first real live NCAA matchup even when it is not USF-specific."""
    if not payload:
        return "I couldn’t find any NCAA game data right now."

    games = []
    if isinstance(payload, dict):
        for key in ("games", "items", "data", "scoreboard", "events"):
            value = payload.get(key)
            if isinstance(value, list):
                games.extend(value)
        games_by_date = payload.get("gamesByDate")
        if isinstance(games_by_date, dict):
            for value in games_by_date.values():
                if isinstance(value, list):
                    games.extend(value)
    elif isinstance(payload, list):
        games = payload

    if not games:
        return "I couldn’t find any NCAA game data right now."

    matches = []
    for entry in games[:10]:
        game = entry.get("game") if isinstance(entry, dict) and isinstance(entry.get("game"), dict) else entry
        if not isinstance(game, dict):
            continue

        home = game.get("home") or game.get("homeTeam") or game.get("home_team") or {}
        away = game.get("away") or game.get("awayTeam") or game.get("away_team") or {}

        home_names = home.get("names") if isinstance(home, dict) else {}
        away_names = away.get("names") if isinstance(away, dict) else {}

        home_name = (
            (home_names.get("full") if isinstance(home_names, dict) else "")
            or (home_names.get("short") if isinstance(home_names, dict) else "")
            or home.get("name")
            or home.get("displayName")
            or home.get("school")
            or home.get("char6")
            or ""
        )
        away_name = (
            (away_names.get("full") if isinstance(away_names, dict) else "")
            or (away_names.get("short") if isinstance(away_names, dict) else "")
            or away.get("name")
            or away.get("displayName")
            or away.get("school")
            or away.get("char6")
            or ""
        )

        generic_names = {"", "home team", "away team", "team"}
        if not away_name or not home_name or away_name.lower() in generic_names or home_name.lower() in generic_names:
            continue

        status = game.get("gameState") or game.get("status") or game.get("state") or "Status unknown"
        start_time = game.get("startDate") or game.get("startTime") or game.get("start_time") or game.get("date") or ""
        if start_time and "T" in start_time:
            start_time = start_time.split("T", 1)[0]

        away_score = game.get("awayScore") or game.get("away_score")
        home_score = game.get("homeScore") or game.get("home_score")
        score_text = ""
        if away_score is not None and home_score is not None:
            score_text = f" — {away_name} {away_score}, {home_name} {home_score}"

        if start_time:
            matches.append(f"{away_name} vs {home_name}{score_text} — {status} ({start_time})")
        else:
            matches.append(f"{away_name} vs {home_name}{score_text} — {status}")

    if not matches:
        return "I couldn’t find any NCAA game data right now."

    summary_text = "NCAA game info:\n" + "\n".join(matches[:5])
    if "Away team vs Home team" in summary_text or ("away team" in summary_text.lower() and "home team" in summary_text.lower()):
        return "I couldn’t find any NCAA game data right now."
    return safe_discord_text(summary_text, 1800)


def extract_next_usf_game(payload):
    """Return the next USF NCAA game when one exists, otherwise fall back to the latest USF result."""
    if not payload:
        return "I couldn’t find any upcoming USF NCAA game right now."

    games = []
    if isinstance(payload, dict):
        for key in ("games", "items", "data", "scoreboard", "events"):
            value = payload.get(key)
            if isinstance(value, list):
                games.extend(value)
        games_by_date = payload.get("gamesByDate")
        if isinstance(games_by_date, dict):
            for value in games_by_date.values():
                if isinstance(value, list):
                    games.extend(value)
    elif isinstance(payload, list):
        games = payload

    upcoming = []
    latest = []

    for entry in games:
        game = entry.get("game") if isinstance(entry, dict) and isinstance(entry.get("game"), dict) else entry
        if not isinstance(game, dict):
            continue

        home = game.get("home") or game.get("homeTeam") or game.get("home_team") or {}
        away = game.get("away") or game.get("awayTeam") or game.get("away_team") or {}

        home_names = home.get("names") if isinstance(home, dict) else {}
        away_names = away.get("names") if isinstance(away, dict) else {}

        home_name = (
            (home_names.get("full") if isinstance(home_names, dict) else "")
            or (home_names.get("short") if isinstance(home_names, dict) else "")
            or home.get("name")
            or home.get("displayName")
            or home.get("school")
            or home.get("char6")
            or ""
        )
        away_name = (
            (away_names.get("full") if isinstance(away_names, dict) else "")
            or (away_names.get("short") if isinstance(away_names, dict) else "")
            or away.get("name")
            or away.get("displayName")
            or away.get("school")
            or away.get("char6")
            or ""
        )

        if not any(is_usf_team_name(name) for name in (home_name, away_name)):
            continue

        if not home_name or not away_name:
            continue

        generic_names = {"", "home team", "away team", "team"}
        if home_name.lower() in generic_names or away_name.lower() in generic_names:
            continue

        status = (game.get("gameState") or game.get("status") or game.get("state") or "Status unknown").lower()
        start_time = game.get("startDate") or game.get("startTime") or game.get("start_time") or game.get("date") or ""
        if start_time and "T" in start_time:
            start_time = start_time.split("T", 1)[0]

        result = f"{away_name} vs {home_name} — {status.upper() if status else 'Status unknown'}"
        if start_time:
            result += f" ({start_time})"

        try:
            parsed_date = datetime.strptime(start_time, "%Y-%m-%d") if start_time else datetime.now()
        except ValueError:
            parsed_date = datetime.now()

        if status in {"pre", "scheduled", "pending", "not started", "tbd"} or (start_time and parsed_date >= datetime.now()):
            upcoming.append((parsed_date, result))
        else:
            latest.append((parsed_date, result))

    if upcoming:
        upcoming.sort(key=lambda item: item[0])
        return safe_discord_text(upcoming[0][1], 300)
    if latest:
        latest.sort(key=lambda item: item[0], reverse=True)
        return safe_discord_text(f"Latest USF game: {latest[0][1]}", 300)

    return "I couldn’t find any upcoming USF NCAA game right now."


def extract_next_any_game(payload):
    """Return the next live NCAA game from any team, with a safe fallback if no upcoming game exists."""
    if not payload:
        return "I couldn’t find any upcoming NCAA game right now."

    games = []
    if isinstance(payload, dict):
        for key in ("games", "items", "data", "scoreboard", "events"):
            value = payload.get(key)
            if isinstance(value, list):
                games.extend(value)
        games_by_date = payload.get("gamesByDate")
        if isinstance(games_by_date, dict):
            for value in games_by_date.values():
                if isinstance(value, list):
                    games.extend(value)
    elif isinstance(payload, list):
        games = payload

    upcoming = []
    latest = []

    for entry in games:
        game = entry.get("game") if isinstance(entry, dict) and isinstance(entry.get("game"), dict) else entry
        if not isinstance(game, dict):
            continue

        home = game.get("home") or game.get("homeTeam") or game.get("home_team") or {}
        away = game.get("away") or game.get("awayTeam") or game.get("away_team") or {}

        home_names = home.get("names") if isinstance(home, dict) else {}
        away_names = away.get("names") if isinstance(away, dict) else {}

        home_name = (
            (home_names.get("full") if isinstance(home_names, dict) else "")
            or (home_names.get("short") if isinstance(home_names, dict) else "")
            or home.get("name")
            or home.get("displayName")
            or home.get("school")
            or home.get("char6")
            or ""
        )
        away_name = (
            (away_names.get("full") if isinstance(away_names, dict) else "")
            or (away_names.get("short") if isinstance(away_names, dict) else "")
            or away.get("name")
            or away.get("displayName")
            or away.get("school")
            or away.get("char6")
            or ""
        )

        if not home_name or not away_name:
            continue

        generic_names = {"", "home team", "away team", "team"}
        if home_name.lower() in generic_names or away_name.lower() in generic_names:
            continue

        status = (game.get("gameState") or game.get("status") or game.get("state") or "Status unknown").lower()
        start_time = game.get("startDate") or game.get("startTime") or game.get("start_time") or game.get("date") or ""
        if start_time and "T" in start_time:
            start_time = start_time.split("T", 1)[0]

        result = f"{away_name} vs {home_name} — {status.upper() if status else 'Status unknown'}"
        if start_time:
            result += f" ({start_time})"

        try:
            parsed_date = datetime.strptime(start_time, "%Y-%m-%d") if start_time else datetime.now()
        except ValueError:
            parsed_date = datetime.now()

        if status in {"pre", "scheduled", "pending", "not started", "tbd"} or (start_time and parsed_date >= datetime.now()):
            upcoming.append((parsed_date, result))
        else:
            latest.append((parsed_date, result))

    if upcoming:
        upcoming.sort(key=lambda item: item[0])
        return safe_discord_text(upcoming[0][1], 300)
    if latest:
        latest.sort(key=lambda item: item[0], reverse=True)
        return safe_discord_text(f"Latest NCAA game: {latest[0][1]}", 300)

    return "I couldn’t find any upcoming NCAA game right now."


def get_supported_ncaa_sport_slugs() -> list[str]:
    """Return the NCAA-backed sport slugs currently supported for USF updates."""
    return [
        "football",
        "basketball-men",
        "basketball-women",
        "baseball",
        "softball",
        "soccer-men",
        "soccer-women",
        "volleyball",
        "volleyball-women",
        "track-field",
    ]


def fetch_ncaa_sport_summary(sport_slug: str, division: str = "fbs") -> str:
    """Try a few NCAA scoreboard and schedule routes for a sport, with a generic fallback when USF is not in the active feed."""
    year = datetime.now().year
    path_variants = []

    if sport_slug == "football":
        division = "fbs"
        path_variants = [
            f"/scoreboard/{sport_slug}/{division}/{year}/all-conf",
            f"/scoreboard/{sport_slug}/{division}/{year}/1/all-conf",
            f"/schedule/{sport_slug}/{division}/{year}",
        ]
    elif sport_slug in {"basketball-men", "basketball-women", "baseball", "softball", "soccer-men", "soccer-women", "volleyball", "volleyball-women"}:
        sport_candidates = [sport_slug]
        if sport_slug == "volleyball":
            sport_candidates.extend(["volleyball-women", "volleyball-men"])
        path_variants = []
        for candidate in sport_candidates:
            path_variants.extend([
                f"/scoreboard/{candidate}/{division}/{year}/all-conf",
                f"/scoreboard/{candidate}/{division}/{year}/1/all-conf",
                f"/schedule/{candidate}/{division}/{year}",
            ])
    else:
        path_variants = [
            f"/scoreboard/{sport_slug}/{division}/{year}/all-conf",
            f"/schedule/{sport_slug}/{division}/{year}",
        ]

    seen = set()
    for path in path_variants:
        if path in seen:
            continue
        seen.add(path)
        try:
            payload = fetch_ncaa_json(path)
            if not payload:
                continue

            usf_summary = extract_ncaa_game_summary(payload)
            if "I couldn’t find any USF-related NCAA game data right now." not in usf_summary and "Away team vs Home team" not in usf_summary and "away team" not in usf_summary.lower():
                return usf_summary

            generic_summary = extract_any_ncaa_game_summary(payload)
            if "I couldn’t find any NCAA game data right now." not in generic_summary and "Away team vs Home team" not in generic_summary and "away team" not in generic_summary.lower():
                return generic_summary
        except Exception:
            continue

    return "I couldn’t find any NCAA game data right now."


def _fetch_searxng_results(query: str, max_results: int = 5) -> list[dict]:
    """Query a local SearxNG instance if configured, returning result dicts with title, snippet, and url."""
    if not SEARXNG_URL:
        return []

    try:
        params = {
            "q": query,
            "format": "json",
            "language": "en-US",
            "engines": "google,bing,duckduckgo",
            "categories": "general",
        }
        query_string = "&".join(f"{key}={quote(str(value))}" for key, value in params.items())
        search_url = f"{SEARXNG_URL}/search?{query_string}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/html, application/xhtml+xml, */*; q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": SEARXNG_URL,
            "X-Forwarded-For": SEARXNG_CLIENT_IP,
            "X-Real-IP": SEARXNG_CLIENT_IP,
            "Upgrade-Insecure-Requests": "1",
            "Connection": "keep-alive",
        }
        request = Request(search_url, headers=headers)
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8", "ignore"))

        results = payload.get("results") or []
        cleaned = []
        seen = set()
        for item in results:
            title = str(item.get("title") or "").strip()
            snippet = str(item.get("content") or item.get("snippet") or "").strip()
            url = str(item.get("url") or "").strip()
            if not title and not snippet:
                continue

            key = f"{title.lower()}|{snippet.lower()}"
            if key in seen:
                continue
            seen.add(key)

            if len(snippet) > 220:
                snippet = snippet[:217].rstrip(" .;:,") + "..."

            cleaned.append({"title": title, "snippet": snippet, "url": url})
            if len(cleaned) >= max_results:
                break

        return cleaned
    except Exception:
        return []


def _fetch_searxng_snippets(query: str, max_results: int = 5) -> list[str]:
    """Query a local SearxNG instance if configured, returning a compact, deduped snippet list."""
    results = _fetch_searxng_results(query, max_results)
    snippets = [f"{r['title']} — {r['snippet']}".strip(" — ") for r in results if r["title"] or r["snippet"]]
    return normalize_search_snippets(snippets, max_items=max_results, max_chars=220)


def fetch_search_snippets(query: str, max_results: int = 5) -> str:
    """Try SearxNG first, then fall back to DuckDuckGo for current USF information."""
    loaders = [
        _fetch_searxng_snippets,
        _fetch_duckduckgo_snippets,
    ]
    for loader in loaders:
        snippets = loader(query, max_results)
        if snippets:
            return "\n\n".join(snippets)

    return "No live web snippets were available for this query. Use official USF sources for final verification."


def fetch_search_results_with_links(query: str, max_results: int = 5, max_chars: int = 1200) -> tuple[str, list[str]]:
    """Combine SearxNG (or DuckDuckGo fallback) titles, snippets, and links into AI-ready context plus a link list, capped to max_chars per request."""
    results = _fetch_searxng_results(query, max_results)
    if not results:
        results = _fetch_duckduckgo_results(query, max_results)

    if not results:
        return "", []

    context_lines = []
    links = []
    total_chars = 0
    for result in results:
        line = f"- {result['title']}: {result['snippet']}".strip(": -")
        if result["url"]:
            line += f" (Source: {result['url']})"

        # Stop adding lines once the running total would exceed the per-request budget.
        if total_chars + len(line) + 1 > max_chars:
            break

        if result["url"]:
            links.append(result["url"])
        context_lines.append(line)
        total_chars += len(line) + 1

    joined = "\n".join(context_lines)
    if len(joined) > max_chars:
        joined = joined[: max_chars - 3].rstrip() + "..."

    return joined, links


def _fetch_duckduckgo_results(query: str, max_results: int = 5) -> list[dict]:
    """Fallback search provider used when SearxNG is unavailable or not configured."""
    try:
        search_url = "https://duckduckgo.com/html/?q=" + quote(query)
        request = Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=12) as response:
            html = response.read().decode("utf-8", "ignore")

        matches = re.findall(
            r'<a rel="nofollow" class="result-link"([^>]*)>(.*?)</a>.*?<a class="result-snippet"[^>]*>(.*?)</a>',
            html,
            re.S | re.I,
        )
        cleaned = []
        for attrs, title, snippet in matches[:max_results]:
            clean_title = re.sub(r"<.*?>", "", title).strip()
            clean_snippet = re.sub(r"<.*?>", "", snippet).strip()
            href_match = re.search(r'href="([^"]*)"', attrs)
            url = href_match.group(1).strip() if href_match else ""
            if clean_title or clean_snippet:
                if len(clean_snippet) > 220:
                    clean_snippet = clean_snippet[:217].rstrip(" .;:,") + "..."
                cleaned.append({"title": clean_title, "snippet": clean_snippet, "url": url})
        return cleaned
    except Exception:
        return []


def _fetch_duckduckgo_snippets(query: str, max_results: int = 5) -> list[str]:
    """Fallback search provider used when SearxNG is unavailable or not configured."""
    results = _fetch_duckduckgo_results(query, max_results)
    snippets = [f"{r['title']} — {r['snippet']}".strip(" — ") for r in results if r["title"] or r["snippet"]]
    return normalize_search_snippets(snippets, max_items=max_results, max_chars=220)


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


async def ask_google(question: str, extra_context: str = "") -> str:
    """Ask Gemini for a response using the configured Google API key."""
    if not GOOGLE_API_KEY:
        raise RuntimeError("Google is not configured for this instance.")
    if genai is None or types is None:
        raise RuntimeError("Google SDK is not installed. Run: pip install google-genai")

    client = genai.Client(api_key=GOOGLE_API_KEY)
    model_name = get_google_model_name()
    prompt = build_context_prompt(question, extra_context, max_context_chars=700)

    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            max_output_tokens=500,
            temperature=0.7,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )

    text = getattr(response, "text", None)
    if text:
        return text.strip()

    if hasattr(response, "candidates") and response.candidates:
        candidate = response.candidates[0]
        if hasattr(candidate, "content") and candidate.content:
            parts = getattr(candidate.content, "parts", [])
            joined = "".join(getattr(part, "text", "") for part in parts if getattr(part, "text", ""))
            if joined:
                return joined.strip()

    raise RuntimeError("Google did not return usable content.")


async def ask_groq(question: str, extra_context: str = "") -> str:
    """Ask Groq for a USF answer using the configured Groq API key."""
    if groq_client is None:
        raise RuntimeError("Groq is not configured for this instance. Run: pip install groq")

    prompt = build_context_prompt(question, extra_context, max_context_chars=700)

    response = groq_client.chat.completions.create(
        model=get_groq_model_candidates()[0],
        messages=[
            {"role": "system", "content": trim_text_for_model(prompt, 2200)},
            {"role": "user", "content": question},
        ],
        temperature=0.7,
        max_completion_tokens=500,
    )

    return response.choices[0].message.content.strip()


async def ask_ai(question: str, extra_context: str = "") -> str:
    """Use the configured provider for USF answers."""
    if get_ai_provider() == "google":
        return await ask_google(question, extra_context)
    return await ask_groq(question, extra_context)


def is_prompt_too_large_error(exc: Exception | str) -> bool:
    """Return True only for actual model input-size failures."""
    message = str(exc).lower()

    user_facing_fallbacks = (
        "the live search context is a bit too large for the model right now",
        "the live search context too large for the model right now",
        "try a shorter search or ask again with a more specific usf question",
        "that question is a bit too long for the model",
        "a bit too long for the model",
        "that question is a bit too long",
    )
    if any(phrase in message for phrase in user_facing_fallbacks):
        return False

    actual_limit_tokens = (
        "request_too_large",
        "413",
        "prompt too long",
        "prompt too large",
        "must be 4000 or fewer in length",
        "must be 20000 or fewer in length",
        "input too large",
        "context too large",
        "context length exceeded",
        "context_length_exceeded",
        "too many tokens",
        "reduce the length of the messages",
        "maximum context length",
    )
    if any(token in message for token in actual_limit_tokens):
        return True

    if "too long" in message and any(marker in message for marker in ("prompt", "context length", "token limit", "request")):
        return True

    return False


def format_ai_error_message(exc: Exception) -> str:
    """Return a user-friendly message for provider throttling or generic failures."""
    message = str(exc).lower()
    if "rate limit reached" in message or "rate_limit_exceeded" in message or "429" in message:
        return "woah someone else is using this so like....chill out cause im answering questions wayyyy to fast"
    if is_prompt_too_large_error(exc):
        return "⚠️ That question is a bit too long for the model. Try a shorter version and I’ll answer it."
    return f"⚠️ I couldn't answer that right now. Error: {exc}"


async def build_ai_search_embed(title: str, query: str) -> discord.Embed:
    """Fetch SearxNG/DuckDuckGo context, ask Groq to compose an answer, and package it with source links."""
    context_text, links = fetch_search_results_with_links(query, max_results=5)
    if not context_text:
        return discord.Embed(
            title=safe_discord_text(title, 250),
            description="I couldn’t pull live results for that topic, but I can still help with the USF angle if you ask directly.",
            color=discord.Color.gold(),
        )

    try:
        answer = await ask_groq(query, context_text)
    except Exception as exc:
        if isinstance(exc, RuntimeError) and "Groq is not configured" in str(exc):
            answer = summarize_search_results(query, context_text)
        else:
            answer = format_ai_error_message(exc)

    return build_ai_answer_embed(title, query, answer, links)


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


async def nickname_channel_check(interaction: discord.Interaction) -> bool:
    """Allow only /nick in the nickname-only channel."""
    if interaction.channel_id != NICKNAME_CHANNEL_ID:
        return True

    command = getattr(interaction, "command", None)
    if command and command.name == "nick":
        return True

    if not interaction.response.is_done():
        await interaction.response.send_message(
            "Only `/nick` can be used in this channel.",
            ephemeral=True,
        )
    return False


bot.tree.interaction_check = nickname_channel_check


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


def build_command_pages() -> list[str]:
    """Split the command help into short, Discord-friendly pages."""
    pages = [
        (
            "**USF Bot Command Guide**\n\n"
            "**General**\n"
            "`/ping` - Check bot availability\n"
            "`/status` - Show bot status\n"
            "`/commands` - Show this menu\n"
            "`/ask <question>` - Ask a USF question\n"
            "`/search <query>` - Search current USF info\n"
            "`/nick <nickname>` - Change your server nickname\n"
            "`/today` - What's happening at USF today\n"
            "`/nextgame` - Next USF NCAA game\n"
            "`/football` - USF football updates\n"
            "`/basketball` - USF basketball updates\n"
            "`/baseball` - USF baseball updates\n"
            "`/softball` - USF softball updates\n"
            "`/soccer` - USF soccer updates\n"
            "`/volleyball` - USF volleyball updates\n"
            "`/crisis` - USF and national crisis support resources\n"
        ),
        (
            "**Staff & Server Tools**\n"
            "`/create_channel <name>` - Create a text channel\n"
            "`/invite [channel] [max_uses] [max_age_hours] [temporary]` - Create a server invite link\n"
            "`/lockdown` - Lock current channel to staff only\n"
            "`/clear <amount>` - Delete recent messages\n"
            "`/timeout @user <minutes> [reason]` - Time out a member\n"
            "`/untimeout @user` - Remove a timeout\n"
            "`/kick @user <reason>` - Kick a member\n"
            "`/ban @user <reason>` - Ban a member\n"
            "`/warn @user <reason>` - Warn a member\n"
            "`/report @user <reason>` - Submit a moderation report\n"
            "`/embed title: ... description: ...` - Build and post a custom embed\n"
        ),
    ]

    final_pages = []
    for page in pages:
        if len(page) <= 1900:
            final_pages.append(page)
        else:
            chunks = []
            current = ""
            for line in page.splitlines(True):
                if len(current) + len(line) > 1800 and current:
                    chunks.append(current)
                    current = line
                else:
                    current += line
            if current:
                chunks.append(current)
            final_pages.extend(chunks)

    return final_pages


class CommandPagerView(discord.ui.View):
    """A paginated Discord view for the command help."""

    def __init__(self, pages: list[str], start_page: int = 1):
        super().__init__(timeout=180)
        self.pages = pages
        self.current_page = max(1, min(start_page, len(pages)))
        self.prev_button = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary)
        self.next_button = discord.ui.Button(label="Next", style=discord.ButtonStyle.primary)
        self.prev_button.callback = self._go_back
        self.next_button.callback = self._go_next
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self._sync_buttons()

    def _content_for_page(self) -> str:
        return f"Page {self.current_page}/{len(self.pages)}\n\n{self.pages[self.current_page - 1]}"

    def _sync_buttons(self):
        self.prev_button.disabled = self.current_page <= 1
        self.next_button.disabled = self.current_page >= len(self.pages)

    async def _go_back(self, interaction: discord.Interaction):
        if self.current_page > 1:
            self.current_page -= 1
            self._sync_buttons()
        await interaction.response.edit_message(content=self._content_for_page(), view=self)

    async def _go_next(self, interaction: discord.Interaction):
        if self.current_page < len(self.pages):
            self.current_page += 1
            self._sync_buttons()
        await interaction.response.edit_message(content=self._content_for_page(), view=self)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} ({bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="Hello, I'm the USF Bot! Go Bulls 🤘"))

    try:
        guild_id = os.getenv("DISCORD_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
        else:
            synced = await bot.tree.sync()
        print(f"Synced {len(synced)} application command(s).")
    except Exception as exc:
        print(f"Failed to sync application commands: {exc}")


@bot.hybrid_command(name="ping", description="Check bot availability")
async def ping(ctx: commands.Context):
    await ctx.send("Pong! 🏓 I am online and ready to help.")


@bot.hybrid_command(name="hello", description="Say hello to the USF Bot", hidden=True)
async def hello(ctx: commands.Context):
    await ctx.send("Hello! I’m the USF Bot! Go Bulls 🤘 This bot is built for USF news, schedules, and server help.")


@bot.hybrid_command(name="status", description="Show bot status")
async def status(ctx: commands.Context):
    await ctx.send("Hello, I’m the USF Bot! Go Bulls 🤘\nI can help with USF info, server tools, and moderation.")


@bot.hybrid_command(name="about", description="Learn what the USF Bot does", hidden=True)
async def about(ctx: commands.Context):
    await ctx.send(
        "**USF Bot**\n"
        "A Discord helper for both campus life and server management.\n"
        "Use me for USF questions, event updates, moderation, channel tools, and general server support."
    )


@bot.hybrid_command(name="commands", description="Show the command guide")
async def list_commands(ctx: commands.Context, page: int = 1):
    pages = build_command_pages()
    if not pages:
        await ctx.send("📖 No command pages are available right now.")
        return

    requested_page = max(1, min(page, len(pages)))
    view = CommandPagerView(pages, requested_page)
    message_content = view._content_for_page()
    try:
        await ctx.send(message_content, view=view, ephemeral=True)
    except TypeError:
        await ctx.send(message_content, view=view)


@bot.hybrid_command(name="serverinfo", description="Show server information", hidden=True)
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


@bot.hybrid_command(name="userinfo", description="Show info about a member", hidden=True)
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


@bot.hybrid_command(name="channelinfo", description="Show info about the current channel", hidden=True)
async def channelinfo(ctx: commands.Context):
    channel = ctx.channel
    await ctx.send(
        f"**Channel info**\n"
        f"Name: {channel.mention}\n"
        f"Type: {channel.type}\n"
        f"Category: {channel.category.name if channel.category else 'None'}\n"
        f"Created: {channel.created_at.strftime('%Y-%m-%d')}"
    )


@bot.hybrid_command(name="nick", description="Change your server nickname")
async def nick(ctx: commands.Context, nickname: str):
    """Change the invoking member's nickname."""
    if len(nickname) > 32:
        await ctx.send("⚠️ Nicknames must be 32 characters or fewer.")
        return

    try:
        await ctx.author.edit(nick=nickname)
        await ctx.send(f"✅ Your nickname is now **{discord.utils.escape_markdown(nickname)}**.")
    except discord.Forbidden:
        await ctx.send("🚫 I don't have permission to change your nickname.")
    except Exception:
        await ctx.send("⚠️ I couldn't change your nickname right now.")


@bot.hybrid_command(name="invite", description="Create a server invite link")
@commands.has_permissions(create_instant_invite=True)
@commands.bot_has_permissions(create_instant_invite=True)
async def create_invite(ctx: commands.Context, channel: discord.TextChannel = None, max_uses: int = 0, max_age_hours: int = 24, temporary: bool = False):
    """Create an invite link for this server, defaulting to a 24-hour expiry and unlimited uses."""
    target_channel = channel or ctx.channel

    # Discord caps invite max_age at 7 days (0 means never expires) and max_uses at 100 (0 means unlimited).
    max_age_seconds = min(max(max_age_hours, 0), 168) * 3600
    capped_max_uses = min(max(max_uses, 0), 100)

    try:
        invite = await target_channel.create_invite(
            max_age=max_age_seconds,
            max_uses=capped_max_uses,
            temporary=temporary,
            reason=f"Requested by {ctx.author}",
        )
    except discord.Forbidden:
        await ctx.send("🚫 I don't have permission to create an invite for that channel.")
        return
    except Exception:
        await ctx.send("⚠️ I couldn't create an invite right now. Try again later.")
        return

    expiry_text = "Never expires" if max_age_seconds == 0 else f"Expires in {max_age_seconds // 3600} hour(s)"
    uses_text = "Unlimited uses" if capped_max_uses == 0 else f"Max {capped_max_uses} use(s)"

    embed = discord.Embed(title="📨 Server Invite Created", description=invite.url, color=discord.Color.gold())
    embed.add_field(name="Channel", value=target_channel.mention, inline=True)
    embed.add_field(name="Expiry", value=expiry_text, inline=True)
    embed.add_field(name="Uses", value=uses_text, inline=True)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="create_channel", description="Create a new text channel")
@staff_only()
@commands.has_permissions(manage_channels=True)
async def create_channel(ctx: commands.Context, channel_name: str):
    if not channel_name or not channel_name.strip():
        await ctx.send("📣 Use `/create_channel <name>` to create a text channel.")
        return

    safe_name = channel_name.strip().lower().replace(" ", "-")
    category = ctx.channel.category
    new_channel = await ctx.guild.create_text_channel(safe_name, category=category)
    await ctx.send(f"📣 Created {new_channel.mention} in {category.mention if category else 'the server'}.")


@bot.hybrid_command(name="lockdown", description="Lock the current channel to staff only")
@staff_only()
@commands.has_permissions(manage_channels=True)
async def lockdown_channel(ctx: commands.Context):
    overwrites = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrites.send_messages = False
    overwrites.read_messages = False
    overwrites.read_message_history = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrites)
    await ctx.send(f"🔒 {ctx.channel.mention} has been locked down to staff only.")


@bot.hybrid_command(name="clear", description="Delete recent messages in this channel")
@staff_only()
@commands.has_permissions(manage_messages=True)
async def clear_messages(ctx: commands.Context, amount: int = 10):
    if amount <= 0:
        await ctx.send("🧹 Use `/clear <number>` with a positive number.")
        return

    deleted = await ctx.channel.purge(limit=amount)
    await ctx.send(f"🧹 Deleted {len(deleted)} messages.", delete_after=3)


@bot.hybrid_command(name="kick", description="Kick a member from the server")
@staff_only()
@commands.has_permissions(kick_members=True)
async def kick_member(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    await member.kick(reason=reason)
    await ctx.send(f"👢 {member.mention} was kicked. Reason: {reason}")


@bot.hybrid_command(name="ban", description="Ban a member from the server")
@staff_only()
@commands.has_permissions(ban_members=True)
async def ban_member(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    await member.ban(reason=reason)
    await ctx.send(f"🚫 {member.mention} was banned. Reason: {reason}")


@bot.hybrid_command(name="timeout", description="Time out a member")
@staff_only()
@commands.has_permissions(moderate_members=True)
async def timeout_member(ctx: commands.Context, member: discord.Member, minutes: int = 10, *, reason: str = "No reason provided"):
    if minutes <= 0:
        await ctx.send("⏱️ Use `/timeout @user <minutes> [reason]` with a positive number.")
        return

    duration = timedelta(minutes=minutes)
    await member.timeout(duration, reason=reason)
    await ctx.send(f"⏱️ {member.mention} was timed out for {minutes} minutes. Reason: {reason}")


@bot.hybrid_command(name="untimeout", description="Remove a member's timeout")
@staff_only()
@commands.has_permissions(moderate_members=True)
async def untimeout_member(ctx: commands.Context, member: discord.Member):
    await member.timeout(None, reason="Removed by moderator")
    await ctx.send(f"✅ {member.mention} is no longer timed out.")


@bot.hybrid_command(name="warn", description="Warn a member and time them out briefly")
@staff_only()
@commands.has_permissions(moderate_members=True)
async def warn_member(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    await member.timeout(timedelta(minutes=5), reason=f"Warning issued by {ctx.author}: {reason}")
    await ctx.send(f"⚠️ {member.mention} was warned and timed out for 5 minutes. Reason: {reason}")


@bot.hybrid_command(name="embed", description="Build and post a custom embed")
@staff_only()
async def embed_builder(
    ctx: commands.Context,
    title: str = "",
    description: str = "",
    color: str = "gold",
    footer: str = "",
    image: str = "",
    thumbnail: str = "",
    channel: discord.TextChannel = None,
):
    """Let staff build and post a custom embed."""
    if not title.strip() and not description.strip():
        await ctx.send(
            "Use: /embed title: <title> description: <text> color: <hex or name> "
            "footer: <text> image: <url> thumbnail: <url> channel: <#channel>\n"
            "Only title or description is required; the rest are optional."
        )
        return

    embed = discord.Embed(
        title=safe_discord_text(title, 250) or None,
        description=safe_discord_text(description, 4000) or None,
        color=parse_embed_color(color),
    )
    if footer.strip():
        embed.set_footer(text=safe_discord_text(footer, 250))
    if image.strip() and is_safe_embed_url(image):
        embed.set_image(url=image.strip())
    if thumbnail.strip() and is_safe_embed_url(thumbnail):
        embed.set_thumbnail(url=thumbnail.strip())
    embed.set_author(name=f"Posted by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

    target_channel = channel or ctx.channel
    try:
        await target_channel.send(embed=embed)
        if target_channel.id != ctx.channel.id:
            await ctx.send(f"✅ Embed sent to {target_channel.mention}.")
    except discord.Forbidden:
        await ctx.send("🚫 I don't have permission to send messages in that channel.")
    except Exception:
        await ctx.send("⚠️ I couldn't send that embed. Check the image/thumbnail URLs and try again.")


@bot.hybrid_command(name="report", description="Submit a moderation report")
async def report_member(ctx: commands.Context, member: discord.Member, *, reason: str):
    mod_channel = discord.utils.get(ctx.guild.text_channels, name="mod-logs") or discord.utils.get(ctx.guild.text_channels, name="moderation")
    if mod_channel:
        await mod_channel.send(
            f"📣 Report received by {ctx.author.mention} against {member.mention}\nReason: {reason}"
        )
    await ctx.send(f"📣 Your report against {member.mention} has been submitted.")


@bot.hybrid_command(name="ask", description="Ask a USF question")
async def ask(ctx: commands.Context, *, question: str):
    if not question.strip():
        await ctx.send("❓ Ask me a USF question like: `/ask When is the next USF football game?`")
        return

    await ctx.defer()
    try:
        embed = await build_ai_search_embed("USF Answer", question)
        await ctx.send(embed=embed)
    except Exception:
        await ctx.send("⚠️ I couldn’t fetch current USF search results right now. Try a shorter or more specific question.")


@bot.hybrid_command(name="search", description="Search current USF-related information")
async def search(ctx: commands.Context, *, query: str):
    if not query.strip():
        await ctx.send("🔎 Use `/search <topic>` to look up current USF-related information.")
        return

    await ctx.defer()
    try:
        embed = await build_ai_search_embed("USF Search Results", query)
        await ctx.send(embed=embed)
    except Exception:
        await ctx.send("⚠️ I couldn’t fetch current USF search results right now. Try a shorter or more specific question.")


async def send_usf_topic_answer(ctx: commands.Context, topic: str):
    """Answer a preset USF topic by combining live SearxNG context with a Groq-generated answer embed."""
    await ctx.defer()
    try:
        embed = await build_ai_search_embed(f"USF {topic}", f"USF {topic}")
        await ctx.send(embed=embed)
    except Exception:
        await ctx.send("⚠️ I couldn’t fetch the live USF results right now. Try a more specific question.")


@bot.hybrid_command(name="today", description="What's happening at USF today")
async def today(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "events and activities happening today")


@bot.hybrid_command(name="nextgame", description="Show the next USF NCAA game")
async def nextgame(ctx: commands.Context):
    """Show the next upcoming NCAA game, preferring USF when available."""
    await ctx.defer()
    try:
        current_year = datetime.now().year
        payloads = []
        for sport_slug in get_supported_ncaa_sport_slugs():
            for path in (
                f"/scoreboard/{sport_slug}/fbs/{current_year}/all-conf",
                f"/scoreboard/{sport_slug}/d1/{current_year}/all-conf",
                f"/schedule/{sport_slug}/fbs/{current_year}",
                f"/schedule/{sport_slug}/d1/{current_year}",
            ):
                payload = fetch_ncaa_json(path)
                if payload:
                    payloads.append(payload)

        for payload in payloads:
            usf_result = extract_next_usf_game(payload)
            if "I couldn’t find any upcoming USF NCAA game right now." not in usf_result and "USF" in usf_result:
                game = None
                for entry in payload.get("games", []) or payload.get("items", []) or payload.get("data", []) or []:
                    candidate = entry.get("game") if isinstance(entry, dict) and isinstance(entry.get("game"), dict) else entry
                    if not isinstance(candidate, dict):
                        continue
                    home = candidate.get("home") or candidate.get("homeTeam") or candidate.get("home_team") or {}
                    away = candidate.get("away") or candidate.get("awayTeam") or candidate.get("away_team") or {}
                    home_name = home.get("name") or home.get("displayName") or home.get("school") or ""
                    away_name = away.get("name") or away.get("displayName") or away.get("school") or ""
                    if any(is_usf_team_name(name) for name in (home_name, away_name)):
                        game = candidate
                        break

                if game is None:
                    await ctx.send(embed=build_game_embed("Next USF game", "USF Bulls", "Opponent", "Scheduled", "Check official schedule", away_score=None, home_score=None, description=usf_result))
                    return

                away_name = (game.get("away") or game.get("awayTeam") or {}).get("name") or (game.get("away") or game.get("awayTeam") or {}).get("displayName") or (game.get("away") or game.get("awayTeam") or {}).get("school") or "Away"
                home_name = (game.get("home") or game.get("homeTeam") or {}).get("name") or (game.get("home") or game.get("homeTeam") or {}).get("displayName") or (game.get("home") or game.get("homeTeam") or {}).get("school") or "Home"
                status = game.get("gameState") or game.get("status") or game.get("state") or "Scheduled"
                start_time = game.get("startDate") or game.get("startTime") or game.get("start_time") or game.get("date") or ""
                if start_time and "T" in start_time:
                    start_time = start_time.split("T", 1)[0]
                away_score = game.get("awayScore") or game.get("away_score")
                home_score = game.get("homeScore") or game.get("home_score")
                await ctx.send(embed=build_game_embed("Next USF game", away_name, home_name, str(status), str(start_time or "TBD"), away_score=away_score, home_score=home_score, description=usf_result))
                return

        for payload in payloads:
            generic_result = extract_next_any_game(payload)
            if "I couldn’t find any upcoming NCAA game right now." not in generic_result:
                await ctx.send(embed=build_game_embed("Next live NCAA game", "Opponent", "Opponent", "Scheduled", "Check official schedule", away_score=None, home_score=None, description=generic_result))
                return

        await ctx.send(embed=build_game_embed("Next live NCAA game", "No matchup found", "No matchup found", "Unavailable", "N/A", away_score=None, home_score=None, description="I couldn’t find any upcoming NCAA game right now."))
    except Exception:
        await ctx.send(embed=build_game_embed("Next live NCAA game", "No matchup found", "No matchup found", "Unavailable", "N/A", away_score=None, home_score=None, description="I couldn’t find any upcoming NCAA game right now."))


async def send_usf_sport_answer(ctx: commands.Context, sport_slug: str, division: str, fallback_topic: str, label: str = None):
    """Use NCAA data to show the real matchup, date, score, and status for USF sports.

    The historical interface passed a fallback topic before the display label. Keep that compatibility
    while preferring the newer embed-first output.
    """
    display_label = label or fallback_topic or sport_slug
    await ctx.defer()
    try:
        current_year = datetime.now().year
        payloads = []
        for path in (
            f"/scoreboard/{sport_slug}/{division}/{current_year}/all-conf",
            f"/scoreboard/{sport_slug}/{division}/{current_year}/1/all-conf",
            f"/schedule/{sport_slug}/{division}/{current_year}",
        ):
            payload = fetch_ncaa_json(path)
            if payload:
                payloads.append(payload)

        for payload in payloads:
            games = payload.get("games", []) or payload.get("items", []) or payload.get("data", []) or []
            for entry in games:
                candidate = entry.get("game") if isinstance(entry, dict) and isinstance(entry.get("game"), dict) else entry
                if not isinstance(candidate, dict):
                    continue

                home = candidate.get("home") or candidate.get("homeTeam") or candidate.get("home_team") or {}
                away = candidate.get("away") or candidate.get("awayTeam") or candidate.get("away_team") or {}
                home_name = home.get("name") or home.get("displayName") or home.get("school") or "Home"
                away_name = away.get("name") or away.get("displayName") or away.get("school") or "Away"
                if not any(is_usf_team_name(name) for name in (home_name, away_name)):
                    continue

                status = candidate.get("gameState") or candidate.get("status") or candidate.get("state") or "Scheduled"
                start_time = candidate.get("startDate") or candidate.get("startTime") or candidate.get("start_time") or candidate.get("date") or ""
                if start_time and "T" in start_time:
                    start_time = start_time.split("T", 1)[0]
                away_score = candidate.get("awayScore") or candidate.get("away_score")
                home_score = candidate.get("homeScore") or candidate.get("home_score")
                await ctx.send(embed=build_game_embed(display_label, away_name, home_name, str(status), str(start_time or "TBD"), away_score=away_score, home_score=home_score))
                return

        if fetch_ncaa_sport_summary is not None:
            summary = fetch_ncaa_sport_summary(sport_slug, division)
            if summary and "I couldn’t find any NCAA game data right now." not in summary and "I couldn’t find any USF-related NCAA game data right now." not in summary and "Away team vs Home team" not in summary and "away team" not in summary.lower():
                await ctx.send(safe_discord_text(f"**{display_label}**\n{summary}", 3900))
                return

        await ctx.send(embed=build_game_embed(display_label, "USF Bulls", "Opponent", "Unavailable", "TBD", away_score=None, home_score=None, description="I couldn’t find any current USF NCAA game data right now."))
    except Exception:
        await ctx.send(embed=build_game_embed(display_label, "USF Bulls", "Opponent", "Unavailable", "TBD", away_score=None, home_score=None, description="I couldn’t find any current USF NCAA game data right now."))


@bot.hybrid_command(name="football", description="USF football updates")
async def football(ctx: commands.Context):
    await send_usf_sport_answer(ctx, "football", "fbs", "football", "USF Football")


@bot.hybrid_command(name="basketball", description="USF basketball updates")
async def basketball(ctx: commands.Context):
    await send_usf_sport_answer(ctx, "basketball-men", "d1", "USF Basketball")


@bot.hybrid_command(name="baseball", description="USF baseball updates")
async def baseball(ctx: commands.Context):
    await send_usf_sport_answer(ctx, "baseball", "d1", "USF Baseball")


@bot.hybrid_command(name="softball", description="USF softball updates")
async def softball(ctx: commands.Context):
    await send_usf_sport_answer(ctx, "softball", "d1", "USF Softball")


@bot.hybrid_command(name="soccer", description="USF soccer updates")
async def soccer(ctx: commands.Context):
    await send_usf_sport_answer(ctx, "soccer-men", "d1", "USF Soccer")


@bot.hybrid_command(name="volleyball", description="USF volleyball updates")
async def volleyball(ctx: commands.Context):
    await send_usf_sport_answer(ctx, "volleyball", "d1", "USF Volleyball")


@bot.hybrid_command(name="sports", description="USF sports schedule and upcoming games", hidden=True)
async def sports(ctx: commands.Context, *, team: str = "all sports"):
    await send_usf_topic_answer(ctx, f"{team} sports schedule and upcoming games")


@bot.hybrid_command(name="calendar", description="USF academic calendar and upcoming events", hidden=True)
async def calendar(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "academic calendar, deadlines, and upcoming campus events")


@bot.hybrid_command(name="campus", description="Info about a USF campus", hidden=True)
async def campus(ctx: commands.Context, *, name: str = "all campuses"):
    await send_usf_topic_answer(ctx, f"{name} campus information")


@bot.hybrid_command(name="dining", description="USF dining locations, menus, and hours", hidden=True)
async def dining(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "dining locations, menus, and hours")


@bot.hybrid_command(name="parking", description="USF parking permits, rules, and availability", hidden=True)
async def parking(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "parking permits, rules, garages, and availability")


@bot.hybrid_command(name="admissions", description="USF admissions requirements and deadlines", hidden=True)
async def admissions(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "admissions requirements and application deadlines")


@bot.hybrid_command(name="financialaid", description="USF financial aid, FAFSA, and scholarships", hidden=True)
async def financialaid(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "financial aid, FAFSA, scholarships, and grants")


@bot.hybrid_command(name="academic", description="USF registration, drop dates, and exams", hidden=True)
async def academic(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "academic calendar, registration, drop dates, and exams")


@bot.hybrid_command(name="major", description="Info about a USF major or program", hidden=True)
async def major(ctx: commands.Context, *, name: str):
    await send_usf_topic_answer(ctx, f"the {name} major or program")


@bot.hybrid_command(name="transit", description="Bull Runner routes and transportation", hidden=True)
async def transit(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "Bull Runner routes and transportation")


@bot.hybrid_command(name="weather", description="Current weather near the Tampa campus", hidden=True)
async def weather(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "current weather near the Tampa campus")


@bot.hybrid_command(name="news", description="Latest USF news and announcements", hidden=True)
async def news(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "latest news and announcements")


@bot.hybrid_command(name="events", description="Student events, clubs, and campus activities", hidden=True)
async def events(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "student events, clubs, and campus activities")


@bot.hybrid_command(name="resources", description="USF counseling, tutoring, and student support", hidden=True)
async def resources(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "counseling, tutoring, health services, and student support")


def build_crisis_embed() -> discord.Embed:
    """Build a static, always-available embed with USF and national crisis resources."""
    embed = discord.Embed(
        title="🆘 Need Help Right Now?",
        description="You are not alone. If you or someone you know is struggling, help is available 24/7.",
        color=discord.Color.red(),
    )
    embed.add_field(name="Life-threatening emergency", value="Call 911 immediately", inline=False)
    embed.add_field(name="988 Suicide & Crisis Lifeline", value="Call or text 988", inline=False)
    embed.add_field(name="Crisis Text Line", value="Text HOME to 741741", inline=False)
    embed.add_field(name="USF Counseling Center (24/7)", value="(813) 974-2831", inline=False)
    embed.add_field(name="USF Police (non-emergency)", value="(813) 974-2628", inline=False)
    embed.set_footer(text="On-campus emergency? Dial 911. USF Counseling Center: usf.edu/student-affairs/counseling-center")
    return embed


@bot.hybrid_command(name="crisis", description="Get USF and national crisis support resources")
async def crisis(ctx: commands.Context):
    await ctx.send(embed=build_crisis_embed())


@bot.hybrid_command(name="bulls", description="A fun USF fact or trivia", hidden=True)
async def bulls(ctx: commands.Context):
    await send_usf_topic_answer(ctx, "a fun fact or trivia question about USF")


@bot.hybrid_command(name="sources", description="Official USF source links for a topic", hidden=True)
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
    if not message.guild:
        return

    if message.channel.id == NICKNAME_CHANNEL_ID:
        if not message.author.bot:
            try:
                await message.delete()
            except Exception:
                pass
        return

    if message.author.bot:
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
