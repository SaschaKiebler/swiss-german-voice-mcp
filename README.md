# swiss-german-voice-mcp

An [MCP](https://modelcontextprotocol.io) server that gives Claude (or any MCP client) a Swiss German voice and Swiss German ears, fully local on a Mac:

- **Speak:** text in Swiss German spelling becomes an audio file in a cloned Swiss voice (Qwen3-TTS 1.7B Base via [audio.cpp](https://github.com/0xShug0/audio.cpp), Metal).
- **Listen:** a Swiss German recording becomes Standard German text (Whisper large-v3-turbo fine-tuned on Swiss German, via [whisper.cpp](https://github.com/ggml-org/whisper.cpp)).

There is no Swiss German TTS model. The trick is voice cloning from a Swiss reference clip plus dialect spelling: the model reads what it gets, so `Mir händ hüt vill gschafft` comes out as Swiss German. The calling LLM does the translation into dialect spelling, guided by the tool description.

No API key, no cloud.

🔊 **Hear it:** *Ballade vom Computer us Züri*, a poem about this repo, spoken by the server itself. GitHub starts videos muted, so turn the sound on in the player. Text, translation and how it was made: [examples/](examples/).

https://github.com/user-attachments/assets/33e415e5-5e36-4c81-aa52-4144c22cd0f8

## Tools

| Tool | What it does |
| --- | --- |
| `speak_swiss_german` | Text in Swiss German spelling → `.ogg` (Opus, fits Telegram voice notes), `.mp3` or `.wav`. Optional voice and seed. |
| `transcribe_swiss_german` | Any audio file ffmpeg can read → Standard German transcript. |
| `list_swiss_voices` | Reference voices available for cloning, with their transcripts. |
| `send_swiss_voice_telegram` | Optional, see [Telegram extension](#telegram-extension-optional). Speaks text and sends it as a real Telegram voice message. |

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
- At least one reference voice. Two ship in [`voices/`](voices), see [Voices](#voices).

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

Claude Desktop starts servers with a minimal `PATH`. The server falls back to `/opt/homebrew/bin` and `/usr/local/bin` for `audiocpp_cli`, `audiocpp_server`, `whisper-cli` and `ffmpeg`, so Homebrew installs are found without extra config. If `uv` itself is not found, use its absolute path as `command`.

## Voices

A voice is a pair of files in the voices folder (default `~/models/audio.cpp/voices/`):

```
zh_frau_46000.wav   # 5 to 15 s of clean speech from one speaker
zh_frau_46000.txt   # the exact words spoken in the clip, in dialect spelling
```

Drop in a new pair to add a voice, no restart needed. The clip decides timbre and accent, so a Zurich speaker gives Zurich colouring, a St. Gallen speaker St. Gallen colouring. Only clone voices you have the right to use.

### Bundled voices

The [`voices/`](voices) folder holds the two voices this server was built with, so you can start without recording your own:

| Voice | Dialect | Clip | Transcript |
| --- | --- | --- | --- |
| `zh_frau_46000` (default) | Zurich | 6.0 s | S noie Reglement fürd Pflägfinanzierig giltet einheitlich fürd Gmeinde Schoofisse und Üürike. |
| `sg_frau_sofia_40000` | St. Gallen | 7.3 s | Isch de Chefarzt vo de Gerontologie in Mäls au für dVisite in Büttehart zueständig? |

Copy them to the default location:

```bash
mkdir -p ~/models/audio.cpp/voices
cp voices/*.wav voices/*.txt ~/models/audio.cpp/voices/
```

Or point the server at the repo folder instead, e.g. `claude mcp add swiss-german-voice -e SWISS_VOICE_VOICES_DIR=/absolute/path/to/swiss-german-voice-mcp/voices -- uv --directory ...`.

Both clips are synthetic speech from the FHNW dataset [`i4ds/swiss-german-city-sentences_v2`](https://huggingface.co/datasets/i4ds/swiss-german-city-sentences_v2) (train split, rows 46000 and 40000), 24 kHz mono WAV. The voice names carry the row number. The dataset is published under `license: other`, check its terms before commercial use.

## Speed

Loading the 2.7 GB TTS model takes about 15 s, generating a few sentences only 2 to 5 s. So the server keeps the model warm:

- The first `speak_swiss_german` call starts an `audiocpp_server` child process on `127.0.0.1:18765`, which loads the model. That call takes about 20 s.
- Every following call goes to the loaded model and takes about as long as the audio itself (measured on an M1 Pro: 2.7 s for 2.9 s of audio).
- After 15 minutes without a call the server drops the model again (about 3.5 GB of RAM) and reloads it on the next call.
- The child process is terminated when the MCP server exits. A second MCP server instance, e.g. another Claude session, reuses a running one instead of loading a second copy.
- If the warm server cannot start or fails, the call falls back to `audiocpp_cli`, which loads the model per call (about 20 s). The tool result says which path was used.

Calls within one MCP server are serialized. Transcription needs 2 to 3 s per clip and loads its model per call, which is fast enough that it needs no warm mode.

## Telegram extension (optional)

Off by default. Set `SWISS_VOICE_TELEGRAM=on` and the server registers one more tool, `send_swiss_voice_telegram(chat_id, text | audio_path, caption, reply_to, voice, seed)`.

It exists because the [Claude Code Telegram plugin](https://github.com/anthropics/claude-plugins-official) sends an `.ogg` as a document, a file the recipient has to download. Telegram shows a voice bubble with waveform and inline playback only for the Bot API method `sendVoice`, which this tool calls. Incoming voice messages need no extension: download them with the plugin and pass the path to `transcribe_swiss_german`.

```bash
claude mcp add -s user swiss-german-voice -e SWISS_VOICE_TELEGRAM=on -- uv --directory /absolute/path/to/swiss-german-voice-mcp run swiss-german-voice-mcp
```

How the bot token and the recipients are handled:

- **Token.** Read at call time from `TELEGRAM_BOT_TOKEN`, or else from the Telegram plugin's state file `~/.claude/channels/telegram/.env` (respects `TELEGRAM_STATE_DIR` and `CLAUDE_CONFIG_DIR`). If the plugin is set up, no extra configuration is needed and the token exists in exactly one place. It is never written to disk, never logged (httpx request logging is turned down because Bot API URLs contain the token) and redacted from every error message.
- **Recipients.** Only chats on the plugin's allowlist (`access.json`, `allowFrom` plus `groups`) are accepted, the same gate the plugin applies to its own replies. Without the plugin, list chat ids in `SWISS_VOICE_TELEGRAM_ALLOWED_CHATS`.
- **Files.** `audio_path` must be `.ogg`, `.oga` or `.opus`, and nothing from the plugin's state directory except its `inbox/` is sent.

## Keeping secrets out of git

The code never contains credentials, and three layers make sure none slip in by accident:

1. `.gitignore` excludes `.env`, `*.env`, `*.pem`, `*.key` and `secrets/`.
2. A pre-commit hook runs `scripts/check_secrets.py` on the staged content and refuses the commit if it finds a secret file, a token shape (Telegram, Anthropic/OpenAI, GitHub, Hugging Face, AWS, private keys) or, verbatim, the bot token configured on this machine. Findings name the file, never the value. Enable it once per clone:

   ```bash
   git config core.hooksPath .githooks
   ```

3. `tests/test_no_secrets.py` runs the same scan over every file git would pick up, so `uv run pytest` fails as well.

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
| `SWISS_VOICE_WARM` | `on` (`off` always uses the CLI) |
| `SWISS_VOICE_SERVER_PORT` | `18765` |
| `SWISS_VOICE_IDLE_UNLOAD_MIN` | `15` |
| `SWISS_VOICE_TELEGRAM` | `off` (`on` registers `send_swiss_voice_telegram`) |
| `SWISS_VOICE_TELEGRAM_ALLOWED_CHATS` | the plugin's `access.json` allowlist |
| `TELEGRAM_BOT_TOKEN` | read from the plugin's `.env` |
| `AUDIOCPP_CLI`, `AUDIOCPP_SERVER`, `WHISPER_CLI`, `FFMPEG` | looked up on `PATH`, then Homebrew dirs |

## Development

```bash
uv run pytest
uvx ruff check . && uvx ruff format --check .
git config core.hooksPath .githooks   # once per clone, see above
```

The tests cover voice lookup, output paths, command building, subprocess handling, the warm-server fallback, the Telegram gatekeeping (with a mocked Bot API) and the secret scan, without loading any model or contacting Telegram. The warm server writes its config and log to `$TMPDIR/swiss-german-voice-mcp/`.

## License

The code is under the [MIT License](LICENSE): use it, change it, ship it, sell it, as long as the copyright notice stays in.

Not covered by that license: the clips in [`voices/`](voices), which stay under the terms of their [source dataset](#bundled-voices), and the models you download yourself (the Swiss German Whisper fine-tune is CC-BY-NC-4.0).
