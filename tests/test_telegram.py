import asyncio
import importlib
import json

import httpx
import pytest

from swiss_german_voice_mcp import engine, telegram

# Deliberately not token-shaped, so the secret scanner stays quiet about this file.
FAKE_TOKEN = "fake-token-for-tests"


@pytest.fixture
def state(tmp_path, monkeypatch):
    state = tmp_path / "telegram"
    (state / "inbox").mkdir(parents=True)
    (state / ".env").write_text(f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\n", encoding="utf-8")
    (state / "access.json").write_text(json.dumps({"allowFrom": ["111"], "groups": {"-222": {}}}), encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_STATE_DIR", str(state))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SWISS_VOICE_TELEGRAM_ALLOWED_CHATS", raising=False)
    return state


@pytest.mark.parametrize("value, expected", [("", False), ("off", False), ("on", True), ("TRUE", True)])
def test_enabled_is_opt_in(monkeypatch, value, expected):
    monkeypatch.setenv("SWISS_VOICE_TELEGRAM", value)
    assert telegram.enabled() is expected


def test_bot_token_sources(state, monkeypatch):
    assert telegram.bot_token() == FAKE_TOKEN
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-env")
    assert telegram.bot_token() == "from-env"
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    (state / ".env").unlink()
    with pytest.raises(engine.VoiceError, match="No Telegram bot token"):
        telegram.bot_token()


def test_allowed_chats(state, monkeypatch):
    assert telegram.allowed_chats() == {"111", "-222"}
    telegram.check_chat("111")
    with pytest.raises(engine.VoiceError, match="not allowlisted"):
        telegram.check_chat("999")
    monkeypatch.setenv("SWISS_VOICE_TELEGRAM_ALLOWED_CHATS", "999, 1000")
    assert telegram.allowed_chats() == {"999", "1000"}


def test_check_sendable(state, tmp_path):
    ok = tmp_path / "memo.ogg"
    ok.write_bytes(b"OggS")
    telegram.check_sendable(ok)
    with pytest.raises(engine.VoiceError, match="OGG/Opus"):
        telegram.check_sendable(state / ".env")
    disguised = state / "token.ogg"
    disguised.write_bytes(b"OggS")
    with pytest.raises(engine.VoiceError, match="state directory"):
        telegram.check_sendable(disguised)
    inbox = state / "inbox" / "incoming.oga"
    inbox.write_bytes(b"OggS")
    telegram.check_sendable(inbox)


_REAL_CLIENT = httpx.AsyncClient


def _mock_client(monkeypatch, handler):
    def factory(*args, **kwargs):
        return _REAL_CLIENT(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(telegram.httpx, "AsyncClient", factory)


def test_send_voice_request(state, tmp_path, monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    _mock_client(monkeypatch, handler)
    ogg = tmp_path / "memo.ogg"
    ogg.write_bytes(b"OggS-audio")
    message_id = asyncio.run(telegram.send_voice("111", ogg, caption="Hoi", reply_to=7, seconds=2.6))
    assert message_id == 42
    assert seen["url"] == f"https://api.telegram.org/bot{FAKE_TOKEN}/sendVoice"
    for part in (b'name="chat_id"', b"111", b'name="caption"', b"Hoi", b'name="duration"', b'"message_id": 7'):
        assert part in seen["body"]
    assert b"OggS-audio" in seen["body"]


def test_send_voice_errors_never_contain_token(state, tmp_path, monkeypatch):
    ogg = tmp_path / "memo.ogg"
    ogg.write_bytes(b"OggS")

    def refused(request):
        return httpx.Response(400, json={"ok": False, "description": f"Bad Request for bot{FAKE_TOKEN}"})

    _mock_client(monkeypatch, refused)
    with pytest.raises(engine.VoiceError) as refused_exc:
        asyncio.run(telegram.send_voice("111", ogg))
    assert FAKE_TOKEN not in str(refused_exc.value) and "<bot-token>" in str(refused_exc.value)

    def offline(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    _mock_client(monkeypatch, offline)
    with pytest.raises(engine.VoiceError) as offline_exc:
        asyncio.run(telegram.send_voice("111", ogg))
    assert FAKE_TOKEN not in str(offline_exc.value)
    assert offline_exc.value.__cause__ is None and offline_exc.value.__suppress_context__


def test_speak_and_send_checks_before_speaking(state, monkeypatch):
    async def must_not_speak(*args, **kwargs):
        raise AssertionError("synthesized although the request was invalid")

    monkeypatch.setattr(engine, "synthesize", must_not_speak)
    with pytest.raises(engine.VoiceError, match="not allowlisted"):
        asyncio.run(telegram.speak_and_send("999", text="Hoi"))
    with pytest.raises(engine.VoiceError, match="either text"):
        asyncio.run(telegram.speak_and_send("111", text="Hoi", audio_path="/x.ogg"))
    with pytest.raises(engine.VoiceError, match="either text"):
        asyncio.run(telegram.speak_and_send("111"))
    with pytest.raises(engine.VoiceError, match="reply_to"):
        asyncio.run(telegram.speak_and_send("111", text="Hoi", reply_to="abc"))


def _tool_names(monkeypatch, value):
    monkeypatch.setenv("SWISS_VOICE_TELEGRAM", value)
    from swiss_german_voice_mcp import server

    server = importlib.reload(server)
    return {tool.name for tool in asyncio.run(server.mcp.list_tools())}


def test_tool_only_registered_when_enabled(monkeypatch):
    assert "send_swiss_voice_telegram" not in _tool_names(monkeypatch, "off")
    assert "send_swiss_voice_telegram" in _tool_names(monkeypatch, "on")
    _tool_names(monkeypatch, "off")
