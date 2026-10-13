import os
import wave
from pathlib import Path

import pytest

from swiss_german_voice_mcp import engine


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    voices = tmp_path / "voices"
    voices.mkdir()
    (voices / "zh_frau.wav").write_bytes(b"RIFF")
    (voices / "zh_frau.txt").write_text("Grüezi mitenand.\n", encoding="utf-8")
    (voices / "no_text.wav").write_bytes(b"RIFF")
    monkeypatch.setenv("SWISS_VOICE_VOICES_DIR", str(voices))
    monkeypatch.setenv("SWISS_VOICE_DEFAULT_VOICE", "zh_frau")
    monkeypatch.setenv("SWISS_VOICE_OUTPUT_DIR", str(tmp_path / "out"))
    return engine.Config.from_env()


def test_list_voices_reads_transcripts(cfg):
    voices = {v.name: v for v in engine.list_voices(cfg.voices_dir)}
    assert set(voices) == {"no_text", "zh_frau"}
    assert voices["zh_frau"].transcript == "Grüezi mitenand."
    assert voices["no_text"].transcript == ""


def test_list_voices_missing_dir(tmp_path):
    assert engine.list_voices(tmp_path / "nope") == []


def test_get_voice_default_and_errors(cfg):
    assert engine.get_voice(cfg).name == "zh_frau"
    assert engine.get_voice(cfg, " zh_frau ").name == "zh_frau"
    with pytest.raises(engine.VoiceError, match="no transcript"):
        engine.get_voice(cfg, "no_text")
    with pytest.raises(engine.VoiceError, match="Available: no_text, zh_frau"):
        engine.get_voice(cfg, "bern")


def test_resolve_output(cfg, tmp_path):
    path, fmt = engine.resolve_output(cfg)
    assert fmt == "ogg" and path.parent == cfg.output_dir and path.name.startswith("gsw-")
    assert engine.resolve_output(cfg, str(tmp_path / "memo.MP3")) == (tmp_path / "memo.MP3", "mp3")
    assert engine.resolve_output(cfg, str(tmp_path / "memo")) == (tmp_path / "memo.ogg", "ogg")
    assert engine.resolve_output(cfg, "~/memo.wav")[0] == Path(os.path.expanduser("~/memo.wav"))
    with pytest.raises(engine.VoiceError, match="Unsupported"):
        engine.resolve_output(cfg, str(tmp_path / "memo.flac"))


def test_tts_command(cfg):
    voice = engine.get_voice(cfg)
    cmd = engine.tts_command("audiocpp_cli", cfg, voice, 'Er seit "hoi"', Path("/x/out.wav"), seed=7)
    assert cmd[cmd.index("--text") + 1] == 'Er seit "hoi"'
    assert cmd[cmd.index("--reference-text") + 1] == "Grüezi mitenand."
    assert cmd[cmd.index("--voice-ref") + 1] == str(voice.wav)
    assert cmd[-2:] == ["--seed", "7"]
    assert "--metrics" not in cmd
    assert "--seed" not in engine.tts_command("audiocpp_cli", cfg, voice, "x", Path("/x/out.wav"))


def test_wav_seconds(tmp_path):
    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\0\0" * 36000)
    assert engine.wav_seconds(path) == 1.5
    assert engine.wav_seconds(tmp_path / "missing.wav") is None


def test_clean_transcript():
    assert engine.clean_transcript("\n Grüezi miteinander,\n das ist ein Test.\n") == (
        "Grüezi miteinander, das ist ein Test."
    )


def test_find_binary_prefers_env(monkeypatch):
    monkeypatch.setenv("FFMPEG", "~/bin/ffmpeg")
    assert engine.find_binary("ffmpeg", "FFMPEG") == os.path.expanduser("~/bin/ffmpeg")


def test_find_binary_missing(monkeypatch):
    monkeypatch.delenv("NOPE_BIN", raising=False)
    with pytest.raises(engine.VoiceError, match="NOPE_BIN"):
        engine.find_binary("definitely-not-installed-xyz", "NOPE_BIN")


async def _run(cmd, timeout):
    return await engine.run(cmd, timeout)


def test_run_reports_failure_and_timeout():
    import asyncio

    assert asyncio.run(_run(["/bin/echo", "hoi"], 5)) == "hoi\n"
    with pytest.raises(engine.VoiceError, match="exit code 3"):
        asyncio.run(_run(["/bin/sh", "-c", "echo kaputt >&2; exit 3"], 5))
    with pytest.raises(engine.VoiceError, match="timed out"):
        asyncio.run(_run(["/bin/sleep", "5"], 0.2))
