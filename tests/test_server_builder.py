import os

os.environ.setdefault("DISCORD_TOKEN", "test_discord_token")
os.environ.setdefault("GOOGLE_API_KEY", "test_google_key")
os.environ.setdefault("GOOGLE_MODEL", "gemini-2.5-flash")

import bot


def test_build_usf_context_prompt_includes_current_date_and_usf_context():
    prompt = bot.build_usf_context_prompt("When is the next USF football game?")

    assert "Current date:" in prompt
    assert "USF" in prompt
    assert "When is the next USF football game?" in prompt


def test_contains_slur_detects_common_offensive_terms():
    assert bot.contains_slur("you are a nigger") is True
    assert bot.contains_slur("this is a friendly message") is False


def test_trim_text_for_model_truncates_long_text():
    long_text = "word " * 5000
    trimmed = bot.trim_text_for_model(long_text, 200)

    assert len(trimmed) <= 200
    assert trimmed.endswith("...")


def test_get_ai_provider_prefers_google_when_configured():
    assert bot.get_ai_provider() == "google"


def test_build_usf_context_prompt_is_usf_first_and_positive():
    prompt = bot.build_usf_context_prompt("What is the best school?")
    lower_prompt = prompt.lower()

    assert "USF" in prompt
    assert "official usf assistant" in lower_prompt
    assert "helpful" in lower_prompt


def test_build_command_pages_splits_large_command_lists():
    pages = bot.build_command_pages()

    assert len(pages) > 1
    assert all(len(page) <= 1900 for page in pages)
    assert "**General**" in pages[0]
    assert "**USF & Campus**" in pages[-1]
