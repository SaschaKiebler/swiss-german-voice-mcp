# swiss-german-voice-mcp

An [MCP](https://modelcontextprotocol.io) server that gives Claude (or any MCP client) a Swiss German voice and Swiss German ears, fully local on a Mac:

- **Speak:** text in Swiss German spelling becomes an audio file in a cloned Swiss voice (Qwen3-TTS 1.7B Base via [audio.cpp](https://github.com/0xShug0/audio.cpp), Metal).
- **Listen:** a Swiss German recording becomes Standard German text (Whisper large-v3-turbo fine-tuned on Swiss German, via [whisper.cpp](https://github.com/ggml-org/whisper.cpp)).

There is no Swiss German TTS model. The trick is voice cloning from a Swiss reference clip plus dialect spelling: the model reads what it gets, so `Mir händ hüt vill gschafft` comes out as Swiss German. The calling LLM does the translation into dialect spelling, guided by the tool description.

No API key, no cloud.

## Tools

| Tool | What it does |
| --- | --- |
| `speak_swiss_german` | Text in Swiss German spelling → `.ogg` (Opus, fits Telegram voice notes), `.mp3` or `.wav`. Optional voice and seed. |
| `transcribe_swiss_german` | Any audio file ffmpeg can read → Standard German transcript. |
| `list_swiss_voices` | Reference voices available for cloning, with their transcripts. |

## Requirements

- macOS on Apple Silicon (other platforms work with `SWISS_VOICE_BACKEND=cpu`/`cuda`, untested)
- Python **3.10+** and [`uv`](https://docs.astral.sh/uv/)
- Homebrew packages:

  ```bash
  brew tap 0xShug0/audio-cpp
  brew install audio-cpp whisper.cpp ffmpeg
  ```

- Models (about 4.3 GB on disk):
  - **Qwen3-TTS 1.7B Base** as GGUF, e.g. `qwen3-tts-12hz-1.7b-base-q8_0_v2.gguf` from the Hugging Face repo `audio-cpp/audio.cpp-gguf`, or `audiocpp_model_manager install qwen3_tts_1_7b_base_q8_0`. Put the folder at `~/models/audio.cpp/Qwen3-TTS-12Hz-1.7B-Base/`.
  - **Swiss German Whisper** in ggml format: [`Flurin17/whisper-large-v3-turbo-swiss-german`](https://huggingface.co/Flurin17/whisper-large-v3-turbo-swiss-german) converted for whisper.cpp, at `~/models/whisper.cpp/ggml-whisper-large-v3-turbo-swiss-german.bin`. License CC-BY-NC-4.0, so non-commercial use only.
- At least one reference voice (see below).

Other locations work too, see [Configuration](#configuration).

## Install

```bash
cd swiss-german-voice-mcp
uv sync
```

This exposes a console script `swiss-german-voice-mcp` that runs the server over stdio.

## Use with Claude Code

```bash
claude mcp add swiss-german-voice -- uv --directory /absolute/path/to/swiss-german-voice-mcp run swiss-german-voice-mcp
```

## Use with Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "swiss-german-voice": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/swiss-german-voice-mcp", "run", "swiss-german-voice-mcp"]
    }
  }
}
```

Claude Desktop starts servers with a minimal `PATH`. The server falls back to `/opt/homebrew/bin` and `/usr/local/bin` for `audiocpp_cli`, `whisper-cli` and `ffmpeg`, so Homebrew installs are found without extra config. If `uv` itself is not found, use its absolute path as `command`.

## Voices

A voice is a pair of files in the voices folder (default `~/models/audio.cpp/voices/`):

```
zh_frau_46000.wav   # 5 to 15 s of clean speech from one speaker
zh_frau_46000.txt   # the exact words spoken in the clip, in dialect spelling
```

Drop in a new pair to add a voice, no restart needed. The clip decides timbre and accent, so a Zurich speaker gives Zurich colouring, a St. Gallen speaker St. Gallen colouring. Only clone voices you have the right to use.

## Speed

Every `speak_swiss_german` call loads the model from scratch (about 15 s on an M1 Pro), then generates at roughly 1.5 s of compute per second of audio. A three-sentence message takes 20 to 30 s. Calls are serialized, so parallel requests queue instead of loading a second copy into GPU memory. Transcription takes 2 to 3 s per clip.

## Configuration

All optional, set as environment variables (in Claude Desktop via an `"env"` block next to `"args"`).

| Variable | Default |
| --- | --- |
| `SWISS_VOICE_TTS_MODEL` | `~/models/audio.cpp/Qwen3-TTS-12Hz-1.7B-Base` |
| `SWISS_VOICE_VOICES_DIR` | `~/models/audio.cpp/voices` |
| `SWISS_VOICE_DEFAULT_VOICE` | `zh_frau_46000` |
| `SWISS_VOICE_TTS_LANGUAGE` | `German` |
| `SWISS_VOICE_BACKEND` | `metal` |
| `SWISS_VOICE_ASR_MODEL` | `~/models/whisper.cpp/ggml-whisper-large-v3-turbo-swiss-german.bin` |
| `SWISS_VOICE_ASR_LANGUAGE` | `de` |
| `SWISS_VOICE_OUTPUT_DIR` | `$TMPDIR/swiss-german-voice` |
| `SWISS_VOICE_TTS_TIMEOUT` / `SWISS_VOICE_ASR_TIMEOUT` | `900` / `300` seconds |
| `AUDIOCPP_CLI`, `WHISPER_CLI`, `FFMPEG` | looked up on `PATH`, then Homebrew dirs |

## Development

```bash
uv run pytest
uvx ruff check . && uvx ruff format --check .
```

The tests cover voice lookup, output paths, command building and subprocess handling without loading any model.
