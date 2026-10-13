import asyncio

import pytest

from swiss_german_voice_mcp import engine, warm_server


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    voices = tmp_path / "voices"
    voices.mkdir()
    (voices / "zh.wav").write_bytes(b"RIFF")
    (voices / "zh.txt").write_text("Grüezi.", encoding="utf-8")
    monkeypatch.setenv("SWISS_VOICE_VOICES_DIR", str(voices))
    monkeypatch.setenv("SWISS_VOICE_DEFAULT_VOICE", "zh")
    monkeypatch.setenv("SWISS_VOICE_TTS_MODEL", str(tmp_path / "model"))
    return engine.Config.from_env()


@pytest.mark.parametrize("value, expected", [("", True), ("on", True), ("off", False), ("0", False), (" No ", False)])
def test_enabled(monkeypatch, value, expected):
    monkeypatch.setenv("SWISS_VOICE_WARM", value)
    assert warm_server.enabled() is expected


def test_server_config(cfg, monkeypatch):
    monkeypatch.setenv("SWISS_VOICE_SERVER_PORT", "19000")
    monkeypatch.setenv("SWISS_VOICE_IDLE_UNLOAD_MIN", "0.5")
    conf = warm_server.server_config(cfg)
    assert conf["host"] == "127.0.0.1" and conf["port"] == 19000 and conf["idle_unload_ms"] == 30_000
    (model,) = conf["models"]
    assert model["id"] == warm_server.MODEL_ID
    assert model["family"] == "qwen3_tts" and model["path"] == str(cfg.tts_model)


def test_speech_request(cfg):
    voice = engine.get_voice(cfg)
    body = warm_server.speech_request(cfg, voice, "Hoi zäme", seed=3)
    assert body["input"] == "Hoi zäme" and body["voice_ref"] == str(voice.wav)
    assert body["reference_text"] == "Grüezi." and body["language"] == "German" and body["seed"] == 3
    assert "seed" not in warm_server.speech_request(cfg, voice, "x")


def test_generate_falls_back_to_cli(cfg, tmp_path, monkeypatch):
    async def broken(*args, **kwargs):
        raise warm_server.WarmServerError("audiocpp_server exited with code 1.\nbind failed")

    async def fake_run(cmd, timeout):
        out = cmd[cmd.index("--out") + 1]
        open(out, "wb").write(b"RIFF")
        return ""

    monkeypatch.setenv("AUDIOCPP_SERVER", "/x/audiocpp_server")
    monkeypatch.setenv("AUDIOCPP_CLI", "/x/audiocpp_cli")
    monkeypatch.setattr(warm_server, "synthesize", broken)
    monkeypatch.setattr(engine, "run", fake_run)
    wav = tmp_path / "out.wav"
    via = asyncio.run(engine._generate(cfg, engine.get_voice(cfg), "Hoi", wav, None))
    assert via == "CLI, warm server unavailable: audiocpp_server exited with code 1."
    assert wav.read_bytes() == b"RIFF"


def test_generate_uses_warm_server(cfg, tmp_path, monkeypatch):
    calls = []

    async def ok(cfg, server_bin, voice, text, wav_out, timeout, seed):
        calls.append((server_bin, text, seed))
        wav_out.write_bytes(b"RIFF")

    monkeypatch.setenv("AUDIOCPP_SERVER", "/x/audiocpp_server")
    monkeypatch.setattr(warm_server, "synthesize", ok)
    via = asyncio.run(engine._generate(cfg, engine.get_voice(cfg), "Hoi", tmp_path / "o.wav", 5))
    assert via == "warm server" and calls == [("/x/audiocpp_server", "Hoi", 5)]


def test_generate_cli_only_when_disabled(cfg, tmp_path, monkeypatch):
    async def must_not_run(*args, **kwargs):
        raise AssertionError("warm server used although disabled")

    async def fake_run(cmd, timeout):
        open(cmd[cmd.index("--out") + 1], "wb").write(b"RIFF")
        return ""

    monkeypatch.setenv("SWISS_VOICE_WARM", "off")
    monkeypatch.setenv("AUDIOCPP_CLI", "/x/audiocpp_cli")
    monkeypatch.setattr(warm_server, "synthesize", must_not_run)
    monkeypatch.setattr(engine, "run", fake_run)
    assert asyncio.run(engine._generate(cfg, engine.get_voice(cfg), "Hoi", tmp_path / "o.wav", None)) == "CLI"


def test_shutdown_without_process_is_noop():
    warm_server.shutdown()
