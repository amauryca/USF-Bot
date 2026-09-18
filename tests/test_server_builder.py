import os

os.environ.setdefault("DISCORD_TOKEN", "test_discord_token")
os.environ.setdefault("GROQ_API_KEY", "test_groq_key")

import bot


def test_build_usf_context_prompt_includes_current_date_and_usf_context():
    prompt = bot.build_usf_context_prompt("When is the next USF football game?")

    assert "Current date:" in prompt
    assert "USF" in prompt
    assert "When is the next USF football game?" in prompt


def test_contains_slur_detects_common_offensive_terms():
    assert bot.contains_slur("you are a nigger") is True
    assert bot.contains_slur("this is a friendly message") is False


def test_get_groq_model_candidates_prefers_available_model():
    old_env = os.environ.get("GROQ_MODEL")
    os.environ.pop("GROQ_MODEL", None)
    bot.GROQ_MODEL = "groq/compound"

    try:
        assert bot.get_groq_model_candidates() == ["groq/compound"]
    finally:
        bot.GROQ_MODEL = "groq/compound"
        if old_env is not None:
            os.environ["GROQ_MODEL"] = old_env
        else:
            os.environ.pop("GROQ_MODEL", None)


def test_get_groq_model_candidates_uses_env_override():
    old_model = bot.GROQ_MODEL
    old_env = os.environ.get("GROQ_MODEL")
    os.environ["GROQ_MODEL"] = "custom/groq-model"
    bot.GROQ_MODEL = "groq/compound"

    try:
        assert bot.get_groq_model_candidates() == [
            "custom/groq-model",
            "groq/compound",
        ]
    finally:
        bot.GROQ_MODEL = old_model
        if old_env is not None:
            os.environ["GROQ_MODEL"] = old_env
        else:
            os.environ.pop("GROQ_MODEL", None)


def test_trim_text_for_model_truncates_long_text():
    long_text = "word " * 5000
    trimmed = bot.trim_text_for_model(long_text, 200)

    assert len(trimmed) <= 200
    assert trimmed.endswith("...")


def test_get_ai_provider_prefers_google_when_configured():
    old_provider = os.environ.get("AI_PROVIDER")
    os.environ["AI_PROVIDER"] = "google"

    try:
        assert bot.get_ai_provider() == "google"
    finally:
        if old_provider is not None:
            os.environ["AI_PROVIDER"] = old_provider
        else:
            os.environ.pop("AI_PROVIDER", None)


def test_build_command_pages_splits_large_command_lists():
    pages = bot.build_command_pages()

    assert len(pages) > 1
    assert all(len(page) <= 1900 for page in pages)
    assert "**General**" in pages[0]
    assert "**USF & Campus**" in pages[-1]
