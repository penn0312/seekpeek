from __future__ import annotations

import pytest

from seekpeek.config import DEFAULT_BASE_URL, ConfigError, Settings


def test_defaults_without_env() -> None:
    settings = Settings.from_env({})

    assert settings.api_key == ""
    assert settings.base_url == DEFAULT_BASE_URL
    assert settings.timeout == 60.0
    assert settings.max_retries == 2
    assert settings.search_qps == 5.0
    assert settings.fetch_qps == 5.0


def test_env_overrides() -> None:
    settings = Settings.from_env(
        {
            "DEEPSEEK_API_KEY": "  sk-abc  ",
            "DEEPSEEK_BASE_URL": "https://gateway.internal/",
            "SEEKPEEK_TIMEOUT": "12.5",
            "SEEKPEEK_MAX_RETRIES": "0",
            "SEEKPEEK_SEARCH_QPS": "1",
            "SEEKPEEK_FETCH_QPS": "2.5",
        }
    )

    assert settings.api_key == "sk-abc"
    assert settings.base_url == "https://gateway.internal"
    assert settings.timeout == 12.5
    assert settings.max_retries == 0
    assert settings.search_qps == 1.0
    assert settings.fetch_qps == 2.5


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"SEEKPEEK_TIMEOUT": "soon"}, "must be a number"),
        ({"SEEKPEEK_MAX_RETRIES": "1.5"}, "whole number"),
        ({"SEEKPEEK_SEARCH_QPS": "-1"}, "must not be negative"),
    ],
)
def test_invalid_env_values_are_rejected(env: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(env)


def test_require_api_key_explains_how_to_set_it() -> None:
    with pytest.raises(ConfigError) as excinfo:
        Settings.from_env({}).require_api_key()

    message = str(excinfo.value)
    assert "DEEPSEEK_API_KEY" in message
    assert "platform.deepseek.com" in message


def test_describe_masks_the_key() -> None:
    described = dict(Settings.from_env({"DEEPSEEK_API_KEY": "sk-1234567890abcdef"}).describe())

    assert described["DEEPSEEK_API_KEY"] == "sk-123…cdef"
    assert "sk-1234567890abcdef" not in str(described)


def test_describe_flags_a_missing_key() -> None:
    assert dict(Settings.from_env({}).describe())["DEEPSEEK_API_KEY"] == "(missing)"
