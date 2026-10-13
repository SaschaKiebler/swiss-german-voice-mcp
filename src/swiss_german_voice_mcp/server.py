"""Swiss German voice MCP server.

Tools:
  speak_swiss_german       text in Swiss German spelling -> audio file in a cloned Swiss voice (Qwen3-TTS)
  transcribe_swiss_german  Swiss German speech -> Standard German text (Swiss German Whisper)
  list_swiss_voices        reference voices available for speak_swiss_german

Optional, only with SWISS_VOICE_TELEGRAM=on:
  send_swiss_voice_telegram  speak and send the result as a Telegram voice message (see ``telegram.py``)

Everything except the Telegram upload runs locally. See ``engine.py`` for the models, paths and environment
variables.
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import sys

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]

from . import engine, telegram, warm_server

log = logging.getLogger(__name__)

mcp = _Server("swiss-german-voice")


@mcp.tool()
async def speak_swiss_german(text: str, voice: str = "", output_path: str = "", seed: int | None = None) -> str:
    """Speak text in Swiss German with a locally cloned Swiss voice and save it as an audio file.

    The model reads exactly what it gets, so the spelling decides the dialect. Write the text in Swiss German
    (Zurich dialect) spelling, for example: "Grüezi mitenand! Das isch e churzi Nachricht. Mir händ hüt vill
    gschafft, und morn gaht's wiiter." Typical forms: isch, händ, chunnt, gaht, nöd, öppis, hüt, morn, öb, tönt,
    mer, vill, Chuchichäschtli. Standard German spelling produces Standard German with a Swiss accent instead.
    To translate from Standard German or English, first rewrite the content in Swiss German spelling yourself,
    then call this tool.

    The first call loads the model (about 20 s). After that it stays loaded and a few sentences take 3 to 5 s,
    until it is unloaded after 15 idle minutes. A few sentences per call work best.

    Args:
        text: What to say, in Swiss German spelling.
        voice: Reference voice from list_swiss_voices. Empty uses the default voice.
        output_path: Where to write the audio. The suffix picks the format: .ogg or .opus (Opus, fits Telegram
            voice notes), .mp3 or .wav. Empty writes an .ogg into a temp folder.
        seed: Fix it for a reproducible take. Omit it for a fresh one.

    Returns the path of the audio file on the first line, then duration and timing.
    """
    try:
        speech = await engine.synthesize(text, voice=voice, output_path=output_path, seed=seed)
    except engine.VoiceError as exc:
        return f"Error: {exc}"
    duration = f"{speech.audio_seconds:.1f} s audio" if speech.audio_seconds is not None else "audio"
    timing = f"generated in {speech.elapsed_seconds:.1f} s via {speech.via}"
    return f"{speech.path}\n{duration}, voice {speech.voice}, {timing}."


@mcp.tool()
async def transcribe_swiss_german(audio_path: str) -> str:
    """Transcribe a Swiss German recording into Standard German text.

    Uses a Whisper large-v3-turbo model fine-tuned on Swiss German. It understands the dialects and writes
    Standard German, so this also translates Swiss German speech into written Standard German. Accepts any
    format ffmpeg can read (ogg/oga voice notes, mp3, m4a, wav, ...). Place names are the most common errors.

    Args:
        audio_path: Absolute path of the audio file.

    Returns the transcript as one line of text.
    """
    try:
        text = await engine.transcribe(audio_path)
    except engine.VoiceError as exc:
        return f"Error: {exc}"
    return text or "(no speech recognized)"


@mcp.tool()
async def list_swiss_voices() -> str:
    """List the reference voices that speak_swiss_german can clone.

    A voice is a short WAV clip plus a .txt file with its exact transcript in the voices folder. Drop in a new
    pair to add a voice.
    """
    cfg = engine.Config.from_env()
    voices = engine.list_voices(cfg.voices_dir)
    if not voices:
        return f"No voices in {cfg.voices_dir}. Add <name>.wav plus <name>.txt (exact transcript)."
    lines = [f"Voices in {cfg.voices_dir}:"]
    for voice in voices:
        mark = " (default)" if voice.name == cfg.default_voice else ""
        said = f'"{voice.transcript}"' if voice.transcript else "missing transcript, not usable"
        lines.append(f"- {voice.name}{mark}: {said}")
    return "\n".join(lines)


async def send_swiss_voice_telegram(
    chat_id: str,
    text: str = "",
    audio_path: str = "",
    caption: str = "",
    reply_to: str = "",
    voice: str = "",
    seed: int | None = None,
) -> str:
    """Send a Swiss German voice message to a Telegram chat, shown as a real voice bubble with waveform.

    Pass text to speak it first (same rules as speak_swiss_german: write it in Swiss German spelling, keep it to
    a few sentences), or audio_path to send an existing .ogg. Use this instead of attaching an .ogg to a Telegram
    reply, which arrives as a file. Only chats on the Telegram plugin's allowlist are accepted.

    Args:
        chat_id: Telegram chat id, as given in the incoming message.
        text: What to say, in Swiss German spelling. Leave empty when passing audio_path.
        audio_path: Existing .ogg/.opus file to send instead of generating speech.
        caption: Optional text shown under the voice message.
        reply_to: Optional message id to reply to.
        voice: Reference voice from list_swiss_voices. Empty uses the default voice.
        seed: Fix it for a reproducible take.

    Returns the Telegram message id and the local path of the audio.
    """
    try:
        sent = await telegram.speak_and_send(chat_id, text, audio_path, caption, reply_to, voice, seed)
    except engine.VoiceError as exc:
        return f"Error: {exc}"
    duration = f"{sent.audio_seconds:.1f} s" if sent.audio_seconds is not None else "voice message"
    return f"Sent {duration} to chat {sent.chat_id} as message {sent.message_id} ({sent.via}).\n{sent.path}"


if telegram.enabled():
    mcp.tool()(send_swiss_voice_telegram)


def _terminate(signum: int, _frame: object) -> None:
    # The default SIGTERM/SIGHUP action skips atexit, which would orphan the warm audiocpp_server.
    warm_server.shutdown()
    os._exit(128 + signum)


def main() -> None:
    # stdout belongs to the MCP protocol, so logs go to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request to the warm server otherwise
    atexit.register(warm_server.shutdown)
    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGHUP, _terminate)
    try:
        mcp.run()
    finally:
        warm_server.shutdown()


if __name__ == "__main__":
    main()
