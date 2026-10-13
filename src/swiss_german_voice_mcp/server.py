"""Swiss German voice MCP server.

Tools:
  speak_swiss_german       text in Swiss German spelling -> audio file in a cloned Swiss voice (Qwen3-TTS)
  transcribe_swiss_german  Swiss German speech -> Standard German text (Swiss German Whisper)
  list_swiss_voices        reference voices available for speak_swiss_german

Everything runs locally. See ``engine.py`` for the models, paths and environment variables.
"""

from __future__ import annotations

import logging
import sys

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]

from . import engine

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

    Keep it short. Every call loads the model (about 15 s), then needs roughly 1.5 s per second of audio.
    A few sentences per call work best.

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
    return f"{speech.path}\n{duration}, voice {speech.voice}, generated in {speech.elapsed_seconds:.0f} s."


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


def main() -> None:
    # stdout belongs to the MCP protocol, so logs go to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    mcp.run()


if __name__ == "__main__":
    main()
