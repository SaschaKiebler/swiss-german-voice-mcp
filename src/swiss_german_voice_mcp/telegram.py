"""Optional Telegram extension: send speech as a real Telegram voice message.

Off by default. With ``SWISS_VOICE_TELEGRAM=on`` the server registers one more tool, ``send_swiss_voice_telegram``.

Why it exists: the Claude Code Telegram plugin sends an .ogg as a document, a file the recipient has to download.
Telegram shows a voice bubble with waveform and inline playback only for ``sendVoice``, which this module calls.

Secrets: the bot token is never stored in this repository or in the MCP client config. It is read at call time
from ``TELEGRAM_BOT_TOKEN`` or from the Telegram plugin's state file (``~/.claude/channels/telegram/.env``, mode
0600) and redacted from every message this module produces. Outbound chats are limited to the plugin's allowlist
(``access.json``), the same gate the plugin applies to its own replies.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import engine
from .engine import VoiceError

API_URL = "https://api.telegram.org"

# httpx logs every request URL at INFO, and Bot API URLs carry the token.
logging.getLogger("httpx").setLevel(logging.WARNING)
VOICE_SUFFIXES = {".ogg", ".oga", ".opus"}  # sendVoice needs OGG/Opus, anything else arrives as a document


def enabled() -> bool:
    return os.environ.get("SWISS_VOICE_TELEGRAM", "").strip().lower() in {"on", "1", "true", "yes"}


def state_dir() -> Path:
    """The Telegram plugin's state directory, resolved the way the plugin resolves it."""
    explicit = os.environ.get("TELEGRAM_STATE_DIR")
    if explicit:
        return Path(explicit).expanduser()
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
    return config / "channels" / "telegram"


def bot_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token
    try:
        for line in (state_dir() / ".env").read_text(encoding="utf-8").splitlines():
            match = re.match(r"^(\w+)=(.*)$", line)
            if match and match[1] == "TELEGRAM_BOT_TOKEN" and match[2].strip():
                return match[2].strip()
    except OSError:
        pass
    raise VoiceError("No Telegram bot token. Set TELEGRAM_BOT_TOKEN or configure the Telegram plugin.")


def allowed_chats() -> set[str]:
    explicit = os.environ.get("SWISS_VOICE_TELEGRAM_ALLOWED_CHATS")
    if explicit:
        return {chat.strip() for chat in explicit.split(",") if chat.strip()}
    try:
        access = json.loads((state_dir() / "access.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(chat) for chat in access.get("allowFrom", [])} | {str(chat) for chat in access.get("groups", {})}


def check_chat(chat_id: str) -> None:
    if chat_id not in allowed_chats():
        raise VoiceError(
            f"Chat {chat_id} is not allowlisted. Add it with /telegram:access or SWISS_VOICE_TELEGRAM_ALLOWED_CHATS."
        )


def check_sendable(path: Path) -> None:
    if path.suffix.lower() not in VOICE_SUFFIXES:
        raise VoiceError(f"Telegram voice messages must be OGG/Opus ({', '.join(sorted(VOICE_SUFFIXES))}).")
    if not path.is_file():
        raise VoiceError(f"Audio file not found: {path}")
    # Same rule as the plugin: its state directory (token, access list) is never sent, only its inbox.
    real, state = path.resolve(), state_dir().resolve()
    if real.is_relative_to(state) and not real.is_relative_to(state / "inbox"):
        raise VoiceError("Refusing to send a file from the Telegram channel state directory.")


def redact(text: str, token: str) -> str:
    return text.replace(token, "<bot-token>") if token else text


async def probe_seconds(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe") or next(
        (p for p in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe") if os.access(p, os.X_OK)), None
    )
    if not ffprobe:
        return None
    try:
        out = await engine.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], 30
        )
        return float(out.strip())
    except (VoiceError, ValueError):
        return None


async def send_voice(
    chat_id: str, ogg: Path, caption: str = "", reply_to: int | None = None, seconds: float | None = None
) -> int:
    """Upload ``ogg`` with sendVoice and return the Telegram message id."""
    token = bot_token()
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    if seconds:
        data["duration"] = str(max(1, round(seconds)))
    if reply_to is not None:
        data["reply_parameters"] = json.dumps({"message_id": reply_to, "allow_sending_without_reply": True})
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            with ogg.open("rb") as audio:
                response = await client.post(
                    f"{API_URL}/bot{token}/sendVoice", data=data, files={"voice": (ogg.name, audio, "audio/ogg")}
                )
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # httpx puts the request URL, and with it the token, into its messages. Drop the chain, redact the text.
        raise VoiceError(redact(f"Telegram request failed: {exc}", token)) from None
    if not payload.get("ok"):
        reason = payload.get("description") or f"HTTP {response.status_code}"
        raise VoiceError(redact(f"Telegram refused the voice message: {reason}", token))
    return int(payload["result"]["message_id"])


@dataclass(frozen=True)
class Sent:
    chat_id: str
    message_id: int
    path: Path
    audio_seconds: float | None
    via: str  # how the audio was made, "existing file" if it was passed in


async def speak_and_send(
    chat_id: str,
    text: str = "",
    audio_path: str = "",
    caption: str = "",
    reply_to: str = "",
    voice: str = "",
    seed: int | None = None,
) -> Sent:
    chat_id = chat_id.strip()
    if bool(text.strip()) == bool(audio_path.strip()):
        raise VoiceError("Pass either text (to speak) or audio_path (an existing .ogg), not both or neither.")
    try:
        reply_id = int(reply_to) if reply_to.strip() else None
    except ValueError:
        raise VoiceError(f"reply_to must be a Telegram message id, got {reply_to!r}.") from None
    # Fail fast on access and token before spending 3 to 20 s on speech.
    check_chat(chat_id)
    bot_token()

    if text.strip():
        speech = await engine.synthesize(text, voice=voice, seed=seed)
        path, seconds, via = speech.path, speech.audio_seconds, speech.via
    else:
        path = Path(audio_path.strip()).expanduser()
        check_sendable(path)
        seconds, via = await probe_seconds(path), "existing file"

    message_id = await send_voice(chat_id, path, caption=caption, reply_to=reply_id, seconds=seconds)
    return Sent(chat_id=chat_id, message_id=message_id, path=path, audio_seconds=seconds, via=via)
