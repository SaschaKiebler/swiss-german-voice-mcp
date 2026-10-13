"""Warm TTS: an ``audiocpp_server`` child process that keeps Qwen3-TTS loaded between calls.

Loading the 2.7 GB model takes about 15 s, generating a few sentences 2 to 5 s. The CLI pays the load on every
call, the server only on the first call and again after it sat idle for ``SWISS_VOICE_IDLE_UNLOAD_MIN`` minutes
(the process stays up, only the weights are dropped).

The server starts lazily on the first speak call, binds to 127.0.0.1 and is terminated when the MCP server exits.
Another MCP server instance (a second Claude session) finds it through ``/v1/models`` and reuses it instead of
loading a second copy. Every failure here raises ``WarmServerError`` and the caller falls back to the CLI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from .engine import Config, Voice

log = logging.getLogger(__name__)

MODEL_ID = "swiss-german-voice-tts"
STARTUP_TIMEOUT = 30.0


class WarmServerError(RuntimeError):
    """The warm server could not be started or did not answer. The caller falls back to the CLI."""


def enabled() -> bool:
    return os.environ.get("SWISS_VOICE_WARM", "on").strip().lower() not in {"off", "0", "false", "no"}


def port() -> int:
    return int(os.environ.get("SWISS_VOICE_SERVER_PORT") or "18765")


def idle_unload_ms() -> int:
    return int(float(os.environ.get("SWISS_VOICE_IDLE_UNLOAD_MIN") or "15") * 60_000)


def state_dir() -> Path:
    return Path(tempfile.gettempdir()) / "swiss-german-voice-mcp"


def server_config(cfg: Config) -> dict[str, Any]:
    return {
        "host": "127.0.0.1",
        "port": port(),
        "backend": cfg.backend,
        "threads": 4,
        "lazy_load": True,
        "idle_unload_ms": idle_unload_ms(),
        "models": [
            {
                "id": MODEL_ID,
                "family": "qwen3_tts",
                "path": str(cfg.tts_model),
                "task": "tts",
                "mode": "offline",
                # Keep the encoded reference of a few voices, so switching voices does not re-encode each time.
                "session_options": {"qwen3_tts.voice_prompt_cache_slots": "4"},
            }
        ],
    }


def speech_request(cfg: Config, voice: Voice, text: str, seed: int | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": MODEL_ID,
        "input": text,
        "voice_ref": str(voice.wav),
        "reference_text": voice.transcript,
        "language": cfg.tts_language,
    }
    if seed is not None:
        body["seed"] = seed
    return body


_proc: subprocess.Popen[bytes] | None = None
_disabled_reason = ""  # set after a failed start, so later calls go straight to the CLI


def _base_url() -> str:
    return f"http://127.0.0.1:{port()}"


async def _ready(client: httpx.AsyncClient) -> bool:
    """True if a server with our model id answers on the port, ours or one another session started."""
    try:
        response = await client.get(f"{_base_url()}/v1/models", timeout=2.0)
        return response.status_code == 200 and any(m.get("id") == MODEL_ID for m in response.json().get("data", []))
    except (httpx.HTTPError, ValueError):
        return False


def _log_tail(path: Path, lines: int = 5) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    kept = [line for line in text.splitlines() if line.strip() and "ggml_metal" not in line]
    return "\n".join(kept[-lines:])


async def _ensure(cfg: Config, server_bin: str) -> None:
    global _proc, _disabled_reason
    async with httpx.AsyncClient() as client:
        if await _ready(client):
            return
        directory = state_dir()
        directory.mkdir(parents=True, exist_ok=True)
        config_path = directory / "server.json"
        config_path.write_text(json.dumps(server_config(cfg), indent=2), encoding="utf-8")
        log_path = directory / "server.log"
        log.info("starting %s on port %d", server_bin, port())
        with open(log_path, "wb") as log_file:
            # Output goes to a file, never to a pipe nobody drains and never to our stdout (the MCP channel).
            _proc = subprocess.Popen(
                [server_bin, "--config", str(config_path), "--no-ui"],
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=directory,
            )
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if await _ready(client):
                return
            if _proc.poll() is not None:
                rc, _proc = _proc.returncode, None
                if await _ready(client):  # lost a start race against another session, use theirs
                    return
                _disabled_reason = f"audiocpp_server exited with code {rc}. {_log_tail(log_path)}".strip()
                raise WarmServerError(_disabled_reason)
            await asyncio.sleep(0.2)
        shutdown()
        _disabled_reason = f"audiocpp_server did not become ready within {STARTUP_TIMEOUT:.0f} s."
        raise WarmServerError(_disabled_reason)


async def synthesize(
    cfg: Config, server_bin: str, voice: Voice, text: str, wav_out: Path, timeout: float, seed: int | None
) -> None:
    """Generate ``text`` through the warm server and write the WAV to ``wav_out``."""
    if _disabled_reason:
        raise WarmServerError(_disabled_reason)
    await _ensure(cfg, server_bin)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as client:
            response = await client.post(f"{_base_url()}/v1/audio/speech", json=speech_request(cfg, voice, text, seed))
    except httpx.HTTPError as exc:
        raise WarmServerError(f"request failed: {exc!r}") from exc
    if response.status_code != 200:
        raise WarmServerError(f"HTTP {response.status_code}: {response.text[:300]}")
    if not response.content.startswith(b"RIFF"):
        raise WarmServerError(f"expected WAV, got {response.headers.get('content-type', 'unknown content')}")
    wav_out.write_bytes(response.content)


def shutdown() -> None:
    """Terminate the server this process started. A server started by another session is left alone."""
    global _proc
    proc, _proc = _proc, None
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
