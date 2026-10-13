"""Local Swiss German speech chain.

Speaking: Qwen3-TTS 1.7B Base clones a reference voice through audio.cpp (Metal on Apple Silicon). The model
reads whatever text it gets, so Swiss German spelling yields Swiss German speech. Calls go to a warm
``audiocpp_server`` that keeps the model loaded (see ``warm_server.py``), with ``audiocpp_cli`` as fallback.

Hearing: a Swiss German fine-tune of Whisper large-v3-turbo runs through ``whisper-cli`` (whisper.cpp). It
understands Swiss German and writes Standard German.

Both run as subprocesses. Their stdout is always captured, because the MCP server talks JSON-RPC over its own
stdout and a stray line from a child process would corrupt the stream.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path

from . import warm_server

log = logging.getLogger(__name__)

# Output suffix -> ffmpeg encoder arguments. WAV is written by audio.cpp directly.
ENCODERS: dict[str, list[str]] = {
    "ogg": ["-c:a", "libopus", "-b:a", "48k"],
    "opus": ["-c:a", "libopus", "-b:a", "48k"],
    "mp3": ["-c:a", "libmp3lame", "-q:a", "3"],
    "wav": [],
}

TTS_TIMEOUT = float(os.environ.get("SWISS_VOICE_TTS_TIMEOUT", "900"))
ASR_TIMEOUT = float(os.environ.get("SWISS_VOICE_ASR_TIMEOUT", "300"))
FFMPEG_TIMEOUT = 120.0

# MCP clients such as Claude Desktop start servers with a minimal PATH that misses Homebrew.
_FALLBACK_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")
_NOISE = re.compile(r"ggml_metal|residency|^(family|task|mode|audio_out)=", re.IGNORECASE)

# One model in GPU memory per kind. Parallel calls queue instead of loading a second 2.7 GB copy.
_tts_lock = asyncio.Lock()
_asr_lock = asyncio.Lock()


class VoiceError(RuntimeError):
    """A failure with a message meant for the caller (missing model, unknown voice, failed run)."""


def _path(name: str, default: str) -> Path:
    return Path(os.path.expanduser(os.environ.get(name) or default))


@dataclass(frozen=True)
class Config:
    tts_model: Path
    voices_dir: Path
    default_voice: str
    tts_language: str
    backend: str
    asr_model: Path
    asr_language: str
    output_dir: Path

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            tts_model=_path("SWISS_VOICE_TTS_MODEL", "~/models/audio.cpp/Qwen3-TTS-12Hz-1.7B-Base"),
            voices_dir=_path("SWISS_VOICE_VOICES_DIR", "~/models/audio.cpp/voices"),
            default_voice=os.environ.get("SWISS_VOICE_DEFAULT_VOICE") or "zh_frau_46000",
            tts_language=os.environ.get("SWISS_VOICE_TTS_LANGUAGE") or "German",
            backend=os.environ.get("SWISS_VOICE_BACKEND") or "metal",
            asr_model=_path(
                "SWISS_VOICE_ASR_MODEL", "~/models/whisper.cpp/ggml-whisper-large-v3-turbo-swiss-german.bin"
            ),
            asr_language=os.environ.get("SWISS_VOICE_ASR_LANGUAGE") or "de",
            output_dir=_path("SWISS_VOICE_OUTPUT_DIR", os.path.join(tempfile.gettempdir(), "swiss-german-voice")),
        )


def find_binary(name: str, env_var: str) -> str:
    explicit = os.environ.get(env_var)
    if explicit:
        return os.path.expanduser(explicit)
    found = shutil.which(name)
    if found:
        return found
    for directory in _FALLBACK_BIN_DIRS:
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise VoiceError(f"{name} not found. Install it or set {env_var} to its full path.")


# --- voices -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Voice:
    name: str
    wav: Path
    transcript: str  # exact words spoken in ``wav``; empty if the .txt next to it is missing


def list_voices(voices_dir: Path) -> list[Voice]:
    """Every ``<name>.wav`` in ``voices_dir``, with the transcript from ``<name>.txt``."""
    if not voices_dir.is_dir():
        return []
    voices = []
    for wav in sorted(voices_dir.glob("*.wav")):
        txt = wav.with_suffix(".txt")
        transcript = txt.read_text(encoding="utf-8").strip() if txt.is_file() else ""
        voices.append(Voice(wav.stem, wav, transcript))
    return voices


def get_voice(cfg: Config, name: str = "") -> Voice:
    name = name.strip() or cfg.default_voice
    voices = list_voices(cfg.voices_dir)
    for voice in voices:
        if voice.name == name:
            if not voice.transcript:
                raise VoiceError(
                    f"Voice '{name}' has no transcript. Put the exact words of {voice.wav.name} "
                    f"into {voice.wav.with_suffix('.txt')}."
                )
            return voice
    available = ", ".join(v.name for v in voices) or f"none in {cfg.voices_dir}"
    raise VoiceError(f"Unknown voice '{name}'. Available: {available}.")


# --- subprocess plumbing ----------------------------------------------------------------------------------------


def _tail(output: bytes, lines: int = 8) -> str:
    text = output.decode("utf-8", errors="replace")
    kept = [line for line in text.splitlines() if line.strip() and not _NOISE.search(line)]
    return "\n".join(kept[-lines:]) or "(no output)"


async def run(cmd: list[str], timeout: float) -> str:
    """Run ``cmd`` with captured output and return its stdout. Kills the child on timeout or cancellation."""
    tool = Path(cmd[0]).name
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        raise VoiceError(f"{tool} timed out after {timeout:.0f} s.") from None
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    if proc.returncode != 0:
        raise VoiceError(f"{tool} failed with exit code {proc.returncode}:\n{_tail(stderr + stdout)}")
    return stdout.decode("utf-8", errors="replace")


# --- speaking ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Speech:
    path: Path
    voice: str
    audio_seconds: float | None
    elapsed_seconds: float
    via: str  # "warm server", or "CLI" plus the reason the warm server was skipped


def resolve_output(cfg: Config, requested: str = "") -> tuple[Path, str]:
    """Target path and format. The suffix of ``requested`` picks the format, default is an .ogg in the output dir."""
    if not requested.strip():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return cfg.output_dir / f"gsw-{stamp}-{uuid.uuid4().hex[:6]}.ogg", "ogg"
    path = Path(requested.strip()).expanduser()
    fmt = path.suffix.lower().lstrip(".")
    if not fmt:
        return path.with_suffix(".ogg"), "ogg"
    if fmt not in ENCODERS:
        raise VoiceError(f"Unsupported audio format '.{fmt}'. Use one of: {', '.join('.' + f for f in ENCODERS)}.")
    return path, fmt


def tts_command(cli: str, cfg: Config, voice: Voice, text: str, wav_out: Path, seed: int | None = None) -> list[str]:
    # Built as a list on purpose: no shell, so quotes and umlauts in the text need no escaping.
    cmd = [
        cli,
        "--task", "tts",
        "--family", "qwen3_tts",
        "--model", str(cfg.tts_model),
        "--backend", cfg.backend,
        "--voice-ref", str(voice.wav),
        "--reference-text", voice.transcript,
        "--text", text,
        "--language", cfg.tts_language,
        "--out", str(wav_out),
    ]  # fmt: skip
    if seed is not None:
        cmd += ["--seed", str(seed)]
    return cmd


def wav_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / wav.getframerate()
    except (wave.Error, OSError, EOFError, ZeroDivisionError):
        return None


async def _generate(cfg: Config, voice: Voice, text: str, wav: Path, seed: int | None) -> str:
    """Write ``text`` as speech to ``wav``. Warm server first, CLI as fallback. Returns which path was taken."""
    via = "CLI"
    if warm_server.enabled():
        try:
            server_bin = find_binary("audiocpp_server", "AUDIOCPP_SERVER")
            await warm_server.synthesize(cfg, server_bin, voice, text, wav, TTS_TIMEOUT, seed)
            return "warm server"
        except (warm_server.WarmServerError, VoiceError) as exc:
            log.warning("warm server unavailable, falling back to the CLI: %s", exc)
            via = f"CLI, warm server unavailable: {str(exc).splitlines()[0][:200]}"
    cli = find_binary("audiocpp_cli", "AUDIOCPP_CLI")
    stdout = await run(tts_command(cli, cfg, voice, text, wav, seed), TTS_TIMEOUT)
    if not wav.is_file():
        raise VoiceError(f"audiocpp_cli finished but wrote no audio:\n{_tail(stdout.encode())}")
    return via


async def synthesize(text: str, voice: str = "", output_path: str = "", seed: int | None = None) -> Speech:
    cfg = Config.from_env()
    text = text.strip()
    if not text:
        raise VoiceError("Text is empty.")
    if not cfg.tts_model.exists():
        raise VoiceError(f"TTS model not found at {cfg.tts_model}. Set SWISS_VOICE_TTS_MODEL.")
    ref = get_voice(cfg, voice)
    out, fmt = resolve_output(cfg, output_path)
    ffmpeg = find_binary("ffmpeg", "FFMPEG") if fmt != "wav" else ""
    out.parent.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="swiss-voice-") as tmp:
        wav = out if fmt == "wav" else Path(tmp) / "speech.wav"
        async with _tts_lock:
            via = await _generate(cfg, ref, text, wav, seed)
        audio_seconds = wav_seconds(wav)
        if fmt != "wav":
            await run([ffmpeg, "-y", "-loglevel", "error", "-i", str(wav), *ENCODERS[fmt], str(out)], FFMPEG_TIMEOUT)

    return Speech(
        path=out,
        voice=ref.name,
        audio_seconds=audio_seconds,
        elapsed_seconds=time.monotonic() - started,
        via=via,
    )


# --- hearing ----------------------------------------------------------------------------------------------------


def clean_transcript(stdout: str) -> str:
    return " ".join(line.strip() for line in stdout.splitlines() if line.strip())


async def transcribe(audio_path: str) -> str:
    cfg = Config.from_env()
    src = Path(audio_path.strip()).expanduser()
    if not src.is_file():
        raise VoiceError(f"Audio file not found: {src}")
    if not cfg.asr_model.is_file():
        raise VoiceError(f"Whisper model not found at {cfg.asr_model}. Set SWISS_VOICE_ASR_MODEL.")
    whisper = find_binary("whisper-cli", "WHISPER_CLI")
    ffmpeg = find_binary("ffmpeg", "FFMPEG")

    with tempfile.TemporaryDirectory(prefix="swiss-voice-") as tmp:
        # whisper.cpp wants 16 kHz mono PCM. Converting always also covers Telegram's .oga voice notes.
        wav = Path(tmp) / "input.wav"
        await run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(src), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
             str(wav)],
            FFMPEG_TIMEOUT,
        )  # fmt: skip
        async with _asr_lock:
            stdout = await run(
                [whisper, "-m", str(cfg.asr_model), "-l", cfg.asr_language, "-nt", "-np", str(wav)], ASR_TIMEOUT
            )
    return clean_transcript(stdout)
