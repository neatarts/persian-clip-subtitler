"""Command-line interface: ``pcs`` / ``python -m persian_clip_subtitler``.

Heavy dependencies (faster-whisper, Pillow) are imported lazily inside the
pipeline, so ``--help`` works on a bare Python install.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pcs",
        description="Burn styled Persian subtitles into a video: "
                    "Whisper transcription -> Persian translation -> "
                    "rounded-box subtitle overlay.",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", type=Path, help="local video/audio file")
    src.add_argument("--url", help="video URL to download first (needs yt-dlp)")
    parser.add_argument("--output", type=Path, default=None,
                        help="output video path (default: <work-dir>/clip-subtitled.mp4)")
    parser.add_argument("--work-dir", type=Path, required=True,
                        help="working directory for transcript, segments, SRT and overlays")
    parser.add_argument("--model", type=Path, default=None,
                        help="local faster-whisper model directory "
                             "(default: $PCS_MODEL_DIR)")
    parser.add_argument("--language", default=None,
                        help="transcription language, e.g. ar, en, fa; default auto-detect")
    parser.add_argument("--translate", default=None, choices=["libretranslate"],
                        help="automatic translation backend (no API key)")
    parser.add_argument("--translate-file", type=Path, default=None,
                        help="your own Persian translation JSON "
                             "(same order/count as segments_src.json)")
    parser.add_argument("--translate-endpoint", default=None,
                        help="LibreTranslate-compatible endpoint URL "
                             "(default: $PCS_TRANSLATE_ENDPOINT or the public instance)")
    parser.add_argument("--skip-transcribe", action="store_true",
                        help="reuse an existing transcript/ in the work dir")
    parser.add_argument("--chunk-seconds", type=int, default=60,
                        help="transcription chunk length in seconds (5-120, default 60)")
    parser.add_argument("--font-size", type=int, default=27,
                        help="subtitle font size in px (default 27)")
    parser.add_argument("--bottom-margin", type=int, default=120,
                        help="px from the video bottom edge to the subtitle box (default 120)")
    parser.add_argument("--box-alpha", type=int, default=165,
                        help="subtitle box opacity 0-255 (default 165)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.url:
        from .download import download_video

        input_path = download_video(args.url, args.work_dir / "download")
    else:
        input_path = args.input
        if not input_path.exists():
            print(f"input not found: {input_path}", file=sys.stderr)
            return 1

    model_dir = args.model or os.environ.get("PCS_MODEL_DIR")
    if not model_dir and not args.skip_transcribe:
        print("No Whisper model directory given. Set --model or $PCS_MODEL_DIR, e.g.:\n"
              "  huggingface-cli download Systran/faster-whisper-small "
              "--local-dir models/whisper-small\n"
              "  pcs --input clip.mp4 --work-dir job-001 --model models/whisper-small",
              file=sys.stderr)
        return 1

    from .pipeline import run
    from .render import CaptionStyle

    style = CaptionStyle(font_size=args.font_size,
                         bottom_margin=args.bottom_margin,
                         box_alpha=args.box_alpha)
    job = run(
        input_path=input_path,
        work_dir=args.work_dir,
        model_dir=Path(model_dir) if model_dir else Path(),
        language=args.language,
        translate=args.translate,
        translate_file=args.translate_file,
        translate_endpoint=args.translate_endpoint,
        skip_transcribe=args.skip_transcribe,
        chunk_seconds=args.chunk_seconds,
        style=style,
    )
    if args.output and job.get("status") == "done":
        default_output = Path(job["output"])
        if default_output.resolve() != args.output.resolve():
            args.output.parent.mkdir(parents=True, exist_ok=True)
            default_output.replace(args.output)
            print("moved output to", args.output)
    if job.get("status") == "needs_translation":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
