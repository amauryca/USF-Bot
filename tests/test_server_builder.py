import os

os.environ.setdefault("DISCORD_TOKEN", "test_discord_token")
os.environ.setdefault("GROQ_API_KEY", "test_groq_key")

import pytest

import bot


def test_parse_server_blueprint_handles_json():
    raw = '''{
      "name": "Pixel Forge",
      "description": "A gaming community",
      "roles": ["Admin", "Moderator", "Member"],
      "categories": [
        {"name": "The Hub", "channels": ["general", "announcements"]},
        {"name": "Gaming", "channels": ["matchmaking", "screenshots"]}
      ]
    }'''

    blueprint = bot.parse_server_blueprint(raw)

    assert blueprint["name"] == "Pixel Forge"
    assert blueprint["roles"] == ["Admin", "Moderator", "Member"]
    assert blueprint["categories"][0]["channels"][0] == "general"


def test_parse_server_blueprint_rejects_missing_name():
    with pytest.raises(ValueError):
        bot.parse_server_blueprint('{"description": "bad blueprint"}')


def test_parse_server_blueprint_supports_advanced_fields():
    raw = '''{
      "name": "Pixel Forge",
      "description": "A gaming community",
      "roles": ["Admin", "Moderator", "Member"],
      "categories": [
        {
          "name": "The Hub",
          "channels": ["general", "announcements"],
          "voice_channels": ["Lobby", "Match Chat"]
        }
      ],
      "welcome_message": "Welcome to Pixel Forge!"
    }'''

    blueprint = bot.parse_server_blueprint(raw)

    assert blueprint["categories"][0]["voice_channels"] == ["Lobby", "Match Chat"]
    assert blueprint["welcome_message"] == "Welcome to Pixel Forge!"
