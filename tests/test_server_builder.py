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
        headers = request.headers
        assert headers["User-agent"].startswith("Mozilla/")
        assert headers.get("X-forwarded-for") == bot.SEARXNG_CLIENT_IP
        assert headers.get("X-real-ip") == bot.SEARXNG_CLIENT_IP
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


def test_is_prompt_too_large_error_only_flags_real_size_limits():
    assert bot.is_prompt_too_large_error("400 Bad Request: Must be 4000 or fewer in length.") is True
    assert bot.is_prompt_too_large_error("context too large for this model") is True
    assert bot.is_prompt_too_large_error("The request timed out while fetching search results.") is False
    assert bot.is_prompt_too_large_error("that question is a bit too long for the model") is False
    assert bot.is_prompt_too_large_error("The live search context too large for the model right now. Try a shorter search.") is False
    assert bot.is_prompt_too_large_error("The live search context is a bit too large for the model right now. Try a shorter search or ask again with a more specific USF question.") is False


def test_get_ai_provider_defaults_to_google_or_searxng_not_groq():
    assert bot.get_ai_provider() == "google"


def test_summarize_search_results_uses_searxng_snippets():
    answer = bot.summarize_search_results(
        "usf football",
        "USF Athletics — Upcoming Event: Football versus Delaware State on Sep 19, 2026 at 7 p.m.\nESPN — South Florida Bulls team page with current football updates.",
    )

    assert "USF" in answer
    assert "football" in answer.lower()
    assert "Delaware State" in answer
    assert "•" in answer


def test_normalize_search_snippets_deduplicates_and_trims():
    text = "USF Bulls football update and campus news " * 20
    snippets = [text, text, "USF athletics page — official schedule and team updates"]

    cleaned = bot.normalize_search_snippets(snippets, max_items=2, max_chars=80)

    assert len(cleaned) <= 2
    assert all(len(item) <= 80 for item in cleaned)
    assert len(set(cleaned)) == len(cleaned)


def test_extract_ncaa_game_summary_includes_score_when_available():
    payload = {
        "games": [
            {
                "homeTeam": {"name": "USF Bulls"},
                "awayTeam": {"name": "UCF Knights"},
                "status": "Final",
                "startTime": "2026-09-26T18:00:00Z",
                "homeScore": 27,
                "awayScore": 24,
            }
        ]
    }

    summary = bot.extract_ncaa_game_summary(payload)

    assert "27" in summary
    assert "24" in summary
    assert "USF Bulls" in summary
    assert "UCF Knights" in summary


def test_build_game_embed_includes_score_and_game_info():
    embed = bot.build_game_embed(
        "USF Football",
        "UCF Knights",
        "USF Bulls",
        "Final",
        "2026-09-26",
        away_score=24,
        home_score=27,
    )

    assert embed.title == "USF Football"
    assert any(field.name == "Score" and "24" in field.value and "27" in field.value for field in embed.fields)
    assert any(field.name == "Status" and field.value == "Final" for field in embed.fields)


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


def test_filter_bulls_connect_events_uses_inclusive_start_exclusive_end():
    start = bot.datetime(2026, 9, 24, tzinfo=bot.BULLS_CONNECT_TIMEZONE)
    events = [
        {"start": start - bot.timedelta(seconds=1), "summary": "Before"},
        {"start": start, "summary": "At start"},
        {"start": start + bot.timedelta(days=1) - bot.timedelta(seconds=1), "summary": "Before end"},
        {"start": start + bot.timedelta(days=1), "summary": "At end"},
    ]

    selected = bot.filter_bulls_connect_events(events, start, start + bot.timedelta(days=1))

    assert [event["summary"] for event in selected] == ["At start", "Before end"]


def test_month_calendar_paginates_events_and_marks_event_days():
    events = [
        {
            "start": bot.datetime(2026, 9, 1, 12, tzinfo=bot.BULLS_CONNECT_TIMEZONE),
            "summary": f"Event {index}",
            "location": "USF Tampa",
        }
        for index in range(17)
    ]

    embeds = bot.build_month_calendar_embeds(2026, 9, events)

    assert len(embeds) == 3
    assert all(len(embed.fields) <= 8 for embed in embeds)
    assert "1*" in embeds[0].description
    assert embeds[-1].fields[0].name == "Event 16"


def test_extract_next_usf_game_detects_live_ncaa_feed_usf_abbreviation():
    payload = {
        "games": [
            {
                "game": {
                    "title": "South Fla. vs Temple",
                    "home": {
                        "names": {"char6": "USF", "short": "South Fla.", "full": ""},
                    },
                    "away": {
                        "names": {"char6": "TEM", "short": "Temple", "full": ""},
                    },
                    "gameState": "pre",
                    "startDate": "2026-09-19",
                }
            }
        ]
    }

    result = bot.extract_next_usf_game(payload)

    assert "South Fla." in result or "USF" in result
    assert "Temple" in result
    assert "2026-09-19" in result


def test_extract_next_usf_game_returns_latest_game_when_no_upcoming_match_exists():
    payload = {
        "games": [
            {
                "game": {
                    "title": "Delaware St. South Fla.",
                    "home": {"names": {"char6": "USF", "short": "South Fla.", "full": ""}},
                    "away": {"names": {"char6": "DEL", "short": "Delaware St.", "full": ""}},
                    "gameState": "final",
                    "startDate": "2026-09-19",
                }
            }
        ]
    }

    result = bot.extract_next_usf_game(payload)

    assert "Latest USF game" in result
    assert "South Fla." in result or "USF" in result


def test_extract_next_any_game_finds_non_usf_matchup():
    payload = {
        "games": [
            {
                "game": {
                    "title": "Georgia vs Alabama",
                    "home": {"names": {"char6": "BAMA", "short": "Alabama", "full": ""}},
                    "away": {"names": {"char6": "UGA", "short": "Georgia", "full": ""}},
                    "gameState": "pre",
                    "startDate": "2026-09-21",
                }
            }
        ]
    }

    result = bot.extract_next_any_game(payload)

    assert "Georgia" in result
    assert "Alabama" in result
    assert "2026-09-21" in result


class FakeUnlockRole:
    def __init__(self, role_id, position=1, *, permissions=0, managed=False, default=False):
        self.id = role_id
        self.position = position
        self.permissions = type("Permissions", (), {"value": permissions})()
        self.managed = managed
        self._default = default

    def is_default(self):
        return self._default

    def __le__(self, other):
        return self.position <= other.position


class FakeUnlockMember:
    def __init__(self, member_id, roles=None, *, manage_roles=False, top_role=None):
        self.id = member_id
        self.roles = list(roles or [])
        self.guild_permissions = type("GuildPermissions", (), {"manage_roles": manage_roles})()
        self.top_role = top_role or FakeUnlockRole(999, position=10)
        self.added_roles = []

    async def add_roles(self, role, reason=None):
        self.roles.append(role)
        self.added_roles.append(role)


class FakeUnlockGuild:
    def __init__(self, guild_id, roles, bot_member):
        self.id = guild_id
        self.roles = roles
        self.me = bot_member

    def get_role(self, role_id):
        return next((role for role in self.roles if role.id == role_id), None)

    def get_member(self, member_id):
        return self.me if self.me.id == member_id else None


class FakeUnlockResponse:
    def __init__(self):
        self.messages = []

    def is_done(self):
        return bool(self.messages)

    async def send_message(self, content, *, ephemeral=False):
        self.messages.append((content, ephemeral))


def make_unlock_interaction(*, guild_id=100, channel_id=400, member_roles=None, system_role=None, bot_manage_roles=True, bot_position=10):
    scavenger = FakeUnlockRole(200, position=1)
    system_access = system_role or FakeUnlockRole(300, position=5)
    bot_member = FakeUnlockMember(900, manage_roles=bot_manage_roles, top_role=FakeUnlockRole(901, position=bot_position))
    guild = FakeUnlockGuild(guild_id, [scavenger, system_access], bot_member)
    member = FakeUnlockMember(500, member_roles if member_roles is not None else [scavenger])
    response = FakeUnlockResponse()
    interaction = type("Interaction", (), {
        "guild": guild,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "user": member,
        "response": response,
    })()
    return interaction, scavenger, system_access


def configure_unlock_environment(monkeypatch):
    monkeypatch.setenv("DISCORD_GUILD_ID", "100")
    monkeypatch.setenv("SCAVENGER_ROLE_ID", "200")
    monkeypatch.setenv("SYSTEM_ACCESS_ROLE_ID", "300")
    monkeypatch.setenv("ACCESS_CHANNEL_ID", "400")
    monkeypatch.setenv("SYSTEM_ACCESS_CODE", "Dummy-Answer")


def test_unlock_code_normalization_removes_punctuation_whitespace_and_case():
    assert bot.normalize_unlock_code("  DuMmY - An swer! ") == "dummyanswer"


def test_unlock_is_slash_only_and_disables_when_settings_are_incomplete(monkeypatch):
    import asyncio

    command = bot.bot.tree.get_command("unlock")
    assert command is not None
    assert bot.bot.get_command("unlock") is None

    configure_unlock_environment(monkeypatch)
    monkeypatch.setenv("ACCESS_CHANNEL_ID", "not-an-id")
    interaction = type("Interaction", (), {"response": FakeUnlockResponse()})()
    asyncio.run(bot.handle_unlock_submission(interaction, "dummyanswer"))
    assert "not configured" in interaction.response.messages[-1][0]
    assert interaction.response.messages[-1][1] is True


def test_unlock_tree_check_only_allows_unlock_in_configured_terminal(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    configure_unlock_environment(monkeypatch)

    def interaction(command_name, channel_id):
        return SimpleNamespace(
            command=SimpleNamespace(name=command_name),
            channel_id=channel_id,
            guild_id=100,
            user=SimpleNamespace(),
            response=FakeUnlockResponse(),
        )

    allowed = interaction("unlock", 400)
    assert asyncio.run(bot.nickname_channel_check(allowed)) is True

    wrong_command = interaction("ping", 400)
    assert asyncio.run(bot.nickname_channel_check(wrong_command)) is False
    assert wrong_command.response.messages[-1][1] is True

    wrong_channel = interaction("unlock", 401)
    assert asyncio.run(bot.nickname_channel_check(wrong_channel)) is False
    assert wrong_channel.response.messages[-1][1] is True


def test_unlock_rejects_wrong_guild_or_channel_privately(monkeypatch):
    import asyncio
    import pytest

    configure_unlock_environment(monkeypatch)
    for guild_id, channel_id in ((101, 400), (100, 401)):
        interaction, _, _ = make_unlock_interaction(guild_id=guild_id, channel_id=channel_id)
        asyncio.run(bot.handle_unlock_submission(interaction, "test"))
        assert interaction.response.messages[-1][1] is True
        assert "decoded" in interaction.response.messages[-1][0]


def test_unlock_requires_scavenger_role(monkeypatch):
    import asyncio

    configure_unlock_environment(monkeypatch)
    interaction, _, _ = make_unlock_interaction(member_roles=[])
    asyncio.run(bot.handle_unlock_submission(interaction, "dummyanswer"))
    assert "Scavenger" in interaction.response.messages[-1][0]
    assert interaction.response.messages[-1][1] is True


def test_unlock_wrong_answer_is_private_and_not_echoed(monkeypatch):
    import asyncio

    configure_unlock_environment(monkeypatch)
    interaction, _, _ = make_unlock_interaction()
    asyncio.run(bot.handle_unlock_submission(interaction, "incorrect-answer"))
    content, ephemeral = interaction.response.messages[-1]
    assert content == "No match. Archivist access remains locked."
    assert ephemeral is True
    assert "incorrect-answer" not in content


def test_unlock_already_authorized_does_not_require_scavenger(monkeypatch):
    import asyncio

    configure_unlock_environment(monkeypatch)
    system_role = FakeUnlockRole(300, position=5)
    interaction, _, _ = make_unlock_interaction(member_roles=[system_role], system_role=system_role)
    asyncio.run(bot.handle_unlock_submission(interaction, "anything"))
    assert interaction.response.messages[-1] == ("You already have the Archivist role.", True)


def test_unlock_success_assigns_only_system_access(monkeypatch):
    import asyncio

    configure_unlock_environment(monkeypatch)
    interaction, scavenger, system_role = make_unlock_interaction()
    asyncio.run(bot.handle_unlock_submission(interaction, " DUMMY answer! "))
    assert interaction.user.added_roles == [system_role]
    assert scavenger in interaction.user.roles
    assert interaction.response.messages[-1] == (
        "Access restored. The System channels are now available.", True
    )


def test_unlock_denies_unsafe_role_or_missing_manage_roles(monkeypatch):
    import asyncio
    import pytest

    configure_unlock_environment(monkeypatch)
    cases = [
        {"system_role": FakeUnlockRole(300, position=5, permissions=1)},
        {"system_role": FakeUnlockRole(300, position=5, managed=True)},
        {"bot_manage_roles": False},
        {"bot_position": 5},
    ]
    for case in cases:
        interaction, _, _ = make_unlock_interaction(**case)
        asyncio.run(bot.handle_unlock_submission(interaction, "dummyanswer"))
        assert interaction.response.messages[-1][1] is True
        assert any(word in interaction.response.messages[-1][0] for word in ("misconfigured", "cannot assign"))


def test_unlock_cooldown_is_concurrent_and_expires(monkeypatch):
    import asyncio

    configure_unlock_environment(monkeypatch)
    bot._unlock_attempts.clear()

    async def exercise():
        answers = await asyncio.gather(*(bot.check_unlock_cooldown(50, now=100) for _ in range(6)))
        retry_after = await bot.check_unlock_cooldown(50, now=161)
        other_user_answers = await asyncio.gather(*(bot.check_unlock_cooldown(51, now=100) for _ in range(5)))
        return answers, retry_after, other_user_answers

    answers, retry_after, other_user_answers = asyncio.run(exercise())
    assert sum(answer is None for answer in answers) == 5
    assert sum(answer is not None for answer in answers) == 1
    assert retry_after is None
    assert list(bot._unlock_attempts[50]) == [161]
    assert all(answer is None for answer in other_user_answers)
    assert len(bot._unlock_attempts[51]) == 5
    bot._prune_unlock_attempts(222)
    assert 50 not in bot._unlock_attempts
