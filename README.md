# persian-clip-subtitler

Burn styled Persian subtitles into a video — from a file or a URL, in one command.

```
pcs --input clip.mp4 --work-dir job-001 --model models/whisper-small --translate libretranslate
```

The tool transcribes the audio with [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
translates the transcript into Persian, and burns the subtitles into the video as
rounded-box overlays (white bold [Vazirmatn](https://github.com/rastikerdar/vazirmatn) text on a
semi-transparent dark box, max 20 characters per line, 2 lines per caption) — the
style that works well for vertical short-form video.

It also ships an optional Telegram bot intake: send the bot a clip, get the
subtitled video back.

> **Reading this in Persian?** See [README-fa.md](README-fa.md) (short version).

## What it does

| Step | How |
| --- | --- |
| 1. Transcribe | faster-whisper (local model, CPU), chunked and resumable — a long job can be interrupted and continued |
| 2. Caption | Transcript segments are split into caption-sized pieces (≤ 20 chars/line, ≤ 2 lines, ≤ ~7 s) with proportional timing |
| 3. Translate | Your own JSON translation (recommended), or automatic via LibreTranslate (free, no key) |
| 4. Render | Each caption is drawn with Pillow + raqm and overlaid with ffmpeg |
| 5. Output | `clip-subtitled.mp4` + a Persian `.srt` sidecar, plus `job.json` with every path and the final status |

If no translation is available, the run stops cleanly after step 2 with status
`needs_translation` (exit code 2) and tells you exactly which file to translate.
Translate it, re-run with `--skip-transcribe`, and the render continues.

## Install

Requirements: Python 3.11+, `ffmpeg` + `ffprobe` on your `PATH`.

```bash
git clone https://github.com/neatarts/persian-clip-subtitler.git
cd persian-clip-subtitler
pip install -e .
```

Download a Whisper model (once):

```bash
pip install huggingface-hub
huggingface-cli download Systran/faster-whisper-small --local-dir models/whisper-small
```

Notes:

- **Pillow needs raqm** for correct Persian letter joining. The PyPI wheels ship
  with it; if your Pillow was built without raqm, `render_caption` will fail
  with a clear error — reinstall Pillow from PyPI.
- Transcription runs on CPU (`int8`); a 3-minute clip takes roughly a minute
  on a modern laptop. GPU is not required.

## Usage

### Basic: local file, automatic translation

```bash
pcs --input clip.mp4 --work-dir job-001 \
    --model models/whisper-small \
    --translate libretranslate
```

### From a URL

```bash
pcs --url https://www.youtube.com/watch?v=VIDEO_ID --work-dir job-002 \
    --model models/whisper-small \
    --translate libretranslate
```

(needs `yt-dlp`, included in `requirements.txt`.)

### Bring your own translation (best quality)

```bash
# 1. Transcribe only — stops after step 2 and writes segments_src.json
pcs --input clip.mp4 --work-dir job-003 --model models/whisper-small

# 2. Translate job-003/segments_src.json into Persian (same order, same count),
#    save it as job-003/segments_fa.json (see examples/segments_fa.example.json)

# 3. Resume — reuses the transcript, renders the video
pcs --input clip.mp4 --work-dir job-003 --model models/whisper-small --skip-transcribe
```

The translation file may be **per-segment** (same length as `segments_src.json`)
or **per-caption** (same length as `segments.json`); the tool detects which.

### Tweak the subtitle style

```bash
pcs --input clip.mp4 --work-dir job-004 --model models/whisper-small \
    --translate libretranslate \
    --font-size 30 --bottom-margin 150 --box-alpha 190
```

### Other languages

```bash
pcs --input talk.mp4 --work-dir job-005 --model models/whisper-small \
    --language en --translate libretranslate
```

`--language` is the source language (`ar`, `en`, `fa`, …). Auto-detect is the
default. If the source is already Persian, translation is skipped and the
transcript is used as-is.

## Configuration

| Flag / variable | Meaning | Default |
| --- | --- | --- |
| `--model` / `PCS_MODEL_DIR` | faster-whisper model directory | — (required) |
| `--language` | transcription language | auto-detect |
| `--translate` | auto-translation backend | none (`libretranslate`) |
| `--translate-file` | your own Persian translation JSON | `segments_fa.json` in the work dir |
| `--translate-endpoint` / `PCS_TRANSLATE_ENDPOINT` | LibreTranslate-compatible endpoint | `https://libretranslate.com/translate` |
| `--chunk-seconds` | transcription chunk length (5–120 s) | 60 |
| `--font-size`, `--bottom-margin`, `--box-alpha` | subtitle style | 27 / 120 / 165 |

## Optional: Telegram bot intake

Send the bot a video, voice message or audio file; it replies with the
subtitled video and the transcript. Token comes from the environment only —
never hardcode it.

```bash
export TELEGRAM_BOT_TOKEN="..."   # from @BotFather
python -m persian_clip_subtitler.telegram_bot \
    --model models/whisper-small --state-dir state --inbox inbox
```

Run it on a schedule (cron / GitHub Actions) or as a long-running process.
Telegram delivers files up to 50 MB to bots.

## How it works

```
video.mp4 ──▶ faster-whisper (chunked, resumable) ──▶ transcript/
        ──▶ split into captions (20 chars/line) ──▶ segments.json
        ──▶ Persian translation (file or LibreTranslate)
        ──▶ subs-fa.srt + Pillow rounded-box PNGs
        ──▶ ffmpeg overlay ──▶ clip-subtitled.mp4
```

- **Transcription** (`persian_clip_subtitler/transcribe.py`): decodes audio with
  ffmpeg to 16 kHz mono PCM, transcribes in 60 s windows with 2 s overlap,
  and writes one atomic JSON receipt per chunk. Re-running reuses finished
  chunks, so an interrupted job resumes instead of restarting.
- **Captions** (`captions.py`): pure functions — splitting, SRT writing and
  translation pairing. Fully unit-tested, no model needed.
- **Translation** (`translate.py`): a small `Translator` interface. Adding a
  new backend (e.g. an LLM API) is one subclass.
- **Rendering** (`render.py`): Pillow draws each caption (Vazirmatn Bold,
  raqm shaping) onto a transparent PNG with a rounded dark box; ffmpeg
  overlays each PNG only during its caption's time range. Audio is copied
  untouched; video is re-encoded with libx264 (`-crf 20 -preset veryfast`).

## Project structure

```
persian-clip-subtitler/
├── persian_clip_subtitler/
│   ├── cli.py            # `pcs` entry point
│   ├── pipeline.py       # orchestration (transcribe → translate → burn)
│   ├── transcribe.py     # chunked, resumable faster-whisper transcription
│   ├── captions.py       # caption splitting, SRT, translation pairing
│   ├── translate.py      # pluggable translators (LibreTranslate, file)
│   ├── render.py         # rounded-box caption rendering + ffmpeg burn-in
│   ├── download.py       # optional yt-dlp URL download
│   ├── telegram_bot.py   # optional Telegram intake (token from env only)
│   └── fonts/            # Vazirmatn Bold/Regular (SIL OFL 1.1)
├── tests/                # pytest unit tests (no model, no network)
├── examples/             # sample Persian translation JSON
└── .github/workflows/    # CI: ruff + pytest + CLI smoke test
```

## Contributing

```bash
pip install -r requirements-dev.txt
ruff check persian_clip_subtitler tests
pytest tests/ -v
```

Issues and pull requests are welcome. Please keep the core free of
provider-specific code: new translation backends go behind the `Translator`
interface, and no secrets, tokens or personal data belong in the repo.

## License

MIT — see [LICENSE](LICENSE). The bundled Vazirmatn font is licensed under
the SIL Open Font License 1.1 (see `persian_clip_subtitler/fonts/OFL.txt`).
