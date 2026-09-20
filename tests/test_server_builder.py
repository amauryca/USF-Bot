import os

os.environ.setdefault("DISCORD_TOKEN", "test_discord_token")
os.environ.setdefault("AI_PROVIDER", "groq")
os.environ.setdefault("GROQ_API_KEY", "test_groq_key")
os.environ.setdefault("GROQ_MODEL", "groq/compound")

import bot


def test_build_usf_context_prompt_includes_current_date_and_usf_context():
    prompt = bot.build_usf_context_prompt("When is the next USF football game?")

    assert "Current date:" in prompt
    assert "USF" in prompt
    assert "When is the next USF football game?" in prompt


def test_contains_slur_detects_common_offensive_terms():
    assert bot.contains_slur("you are a nigger") is True
    assert bot.contains_slur("this is a friendly message") is False


def test_fetch_search_snippets_prefers_searxng_when_configured(monkeypatch):
    monkeypatch.setattr(bot, "SEARXNG_URL", "https://searxng.local")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"results":[{"title":"USF official page","content":"Updated campus information for students"}]}'

    def fake_urlopen(request, timeout):
        return FakeResponse()

    monkeypatch.setattr(bot, "urlopen", fake_urlopen)
    result = bot.fetch_search_snippets("USF campus events")

    assert "USF official page" in result
    assert "Updated campus information" in result


def test_trim_text_for_model_truncates_long_text():
    long_text = "word " * 5000
    trimmed = bot.trim_text_for_model(long_text, 200)

    assert len(trimmed) <= 200
    assert trimmed.endswith("...")


def test_get_ai_provider_defaults_to_groq():
    assert bot.get_ai_provider() == "groq"


def test_build_usf_context_prompt_is_usf_first_and_positive():
    prompt = bot.build_usf_context_prompt("What is the best school?")
    lower_prompt = prompt.lower()

    assert "USF" in prompt
    assert "official usf assistant" in lower_prompt
    assert "helpful" in lower_prompt


def test_extract_ncaa_game_summary_handles_usf_games():
    payload = {
        "games": [
            {
                "homeTeam": {"name": "USF Bulls"},
                "awayTeam": {"name": "UCF Knights"},
                "status": "Scheduled",
                "startTime": "2026-09-26T18:00:00Z",
            }
        ]
    }

    summary = bot.extract_ncaa_game_summary(payload)

    assert "USF Bulls" in summary
    assert "UCF Knights" in summary
    assert "2026-09-26" in summary


def test_build_command_pages_splits_large_command_lists():
    pages = bot.build_command_pages()
    all_text = "\n".join(pages)

    assert len(pages) > 1
    assert all(len(page) <= 1900 for page in pages)
    assert "**General**" in pages[0]
    assert "USF Bot Command Guide" in all_text
    assert "!ask" in all_text
    assert "!search" in all_text
    assert "!today" in all_text
    assert "!football" in all_text
    assert "!nextgame" in all_text
    assert "!create_channel" in all_text
    assert "!lockdown" in all_text
    assert "!ban" in all_text
    assert "!campus" not in all_text
    assert "!dining" not in all_text
    assert "!parking" not in all_text
    assert "!admissions" not in all_text


def test_get_supported_ncaa_sport_slugs_includes_core_usf_sports():
    sport_slugs = bot.get_supported_ncaa_sport_slugs()

    assert "football" in sport_slugs
    assert "basketball-men" in sport_slugs
    assert "baseball" in sport_slugs
    assert "softball" in sport_slugs
    assert "soccer-men" in sport_slugs
    assert "volleyball" in sport_slugs


def test_send_usf_sport_answer_uses_ncaa_only(monkeypatch):
    class FakeCtx:
        def __init__(self):
            self.sent = None

        async def send(self, message):
            self.sent = message

    ctx = FakeCtx()
    fallback_called = {"value": False}

    monkeypatch.setattr(
        bot,
        "fetch_ncaa_sport_summary",
        lambda *args, **kwargs: "USF Bulls vs UCF Knights — Scheduled (2026-09-26)",
    )

    async def fake_fallback(*args, **kwargs):
        fallback_called["value"] = True

    monkeypatch.setattr(bot, "send_usf_topic_answer", fake_fallback)

    import asyncio
    asyncio.run(bot.send_usf_sport_answer(ctx, "football", "fbs", "football", "USF Football"))

    assert "USF Football" in ctx.sent
    assert "USF Bulls" in ctx.sent
    assert fallback_called["value"] is False


def test_get_next_usf_game_uses_ncaa_payload_for_upcoming_match():
    payload = {
        "games": [
            {
                "homeTeam": {"name": "UCF Knights"},
                "awayTeam": {"name": "USF Bulls"},
                "status": "Scheduled",
                "startTime": "2026-09-26T18:00:00Z",
            },
            {
                "homeTeam": {"name": "FSU Seminoles"},
                "awayTeam": {"name": "Miami Hurricanes"},
                "status": "Scheduled",
                "startTime": "2026-09-19T18:00:00Z",
            },
        ]
    }

    result = bot.extract_next_usf_game(payload)

    assert "USF Bulls" in result
    assert "UCF Knights" in result
    assert "2026-09-26" in result
