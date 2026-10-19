from __future__ import annotations

import json

import pytest

from seekpeek.cli import main
from tests.conftest import build_client, error_response, json_response, search_payload


@pytest.fixture
def no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


@pytest.fixture
def with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-cli-test")


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert capsys.readouterr().out.startswith("seekpeek ")


def test_help_mentions_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])

    out = capsys.readouterr().out
    assert "doctor" in out
    assert "stdio" in out


def test_doctor_without_key_fails_loudly(no_key: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor"]) == 1

    out = capsys.readouterr().out
    assert "(missing)" in out
    assert "platform.deepseek.com" in out


def test_doctor_offline_with_key_succeeds(with_key: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor", "--offline"]) == 0

    out = capsys.readouterr().out
    assert "sk-cli…test" in out
    assert "Offline mode" in out


def test_search_without_key_explains_itself(no_key: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["search", "anything"]) == 1

    err = capsys.readouterr().err
    assert "DEEPSEEK_API_KEY" in err


def test_search_prints_markdown(with_key: None, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(
        "seekpeek.cli.DeepSeekClient", _fake_client(lambda request: json_response(request, search_payload()))
    )

    assert main(["search", "DeepSeek V4", "--count", "1"]) == 0

    out = capsys.readouterr().out
    assert "Query: `DeepSeek V4` — 1 result" in out


def test_search_json_output_is_machine_readable(with_key: None, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(
        "seekpeek.cli.DeepSeekClient", _fake_client(lambda request: json_response(request, search_payload()))
    )

    assert main(["search", "q", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0]["title"] == "Result 1"


def test_api_error_becomes_one_readable_line(with_key: None, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(
        "seekpeek.cli.DeepSeekClient",
        _fake_client(lambda request: error_response(request, 402, "Insufficient Balance")),
    )

    assert main(["search", "q"]) == 1

    err = capsys.readouterr().err
    assert "insufficient balance" in err
    assert "top_up" in err


def test_fetch_several_urls_uses_the_batch_path(with_key: None, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def handler(request):
        url = json.loads(request.content)["url"]
        if url.endswith("/dead"):
            return error_response(request, 404, "no content")
        return json_response(request, {"title": "Page", "content": "hello"})

    monkeypatch.setattr("seekpeek.cli.DeepSeekClient", _fake_client(handler))

    assert main(["fetch", "https://a.dev/live", "https://a.dev/dead"]) == 1

    out = capsys.readouterr().out
    assert "fetch_many (1/2 succeeded)" in out


def _fake_client(handler):
    """Build a real client around a fake transport, matching the CLI's constructor call."""

    def factory(settings, **kwargs):
        return build_client(settings, handler)

    return factory
