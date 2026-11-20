"""Orchestration: video -> transcript -> Persian captions -> subtitled video.

Steps:
  1. Transcribe with faster-whisper (chunked, resumable) -> ``transcript/``.
  2. Group transcript segments into caption-sized pieces (max 2 lines,
     20 chars/line) -> ``segments.json`` (captions) and ``segments_src.json``
     (raw transcript segments, the file a translator works from).
  3. Translation. One of:
     - ``--translate-file segments_fa.json``: your own Persian translation
       (human- or LLM-written, recommended for quality);
     - ``--translate libretranslate``: automatic translation, no API key;
     - nothing: the run stops here with status ``needs_translation``
       (exit code 2). Translate ``segments_src.json`` (same order, same
       count), save it as ``segments_fa.json`` and re-run with
       ``--skip-transcribe``.
  4. Build the Persian SRT and burn the captions in as rounded-box
     overlays -> ``clip-subtitled.mp4``.
  5. Write ``job.json`` with all paths and the final status.

Outputs in ``--work-dir``: ``transcript/``, ``segments.json``,
``segments_src.json``, ``segments_fa.json``, ``subs-fa.srt``,
``clip-subtitled.mp4``, ``job.json``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import captions as captions_mod
from .render import CaptionStyle, burn_captions
from .translate import LibreTranslateTranslator, load_translation_file

DEFAULT_MODEL_DIR = os.environ.get("PCS_MODEL_DIR", "")


def run_transcribe(input_path: Path, transcript_dir: Path, model_dir: Path,
                   language: str | None, chunk_seconds: int = 60) -> None:
    """Run the bundled chunked transcriber as a subprocess."""
    here = Path(__file__).resolve().parent
    cmd = [
        sys.executable, str(here / "transcribe.py"),
        "--input", str(input_path),
        "--model", str(model_dir),
        "--output-dir", str(transcript_dir),
        "--chunk-seconds", str(chunk_seconds),
    ]
    if language:
        cmd += ["--language", language]
    print("transcribing:", " ".join(cmd), flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"transcription failed (exit {result.returncode})")


def _group_tail_words(words: list[dict]) -> list[dict]:
    """Group trailing words into segments (same rule as the transcriber:
    a new segment after a >1 s gap or past 30 s)."""
    segments: list[dict] = []
    for word in words:
        text = (word.get("text") or "").strip()
        if not text:
            continue
        if (not segments or word["start"] - segments[-1]["end"] > 1
                or word["end"] - segments[-1]["start"] > 30):
            segments.append({"start": float(word["start"]),
                             "end": float(word["end"]), "text": text})
        else:
            segments[-1]["end"] = float(word["end"])
            segments[-1]["text"] += word["text"]
    for seg in segments:
        seg["text"] = seg["text"].strip()
    return segments


def load_segments(transcript_dir: Path) -> tuple[list[dict], str | None]:
    """Read all chunk receipts, return ([{start, end, text}], language).

    The last chunk's ``pending_words`` are the uncommitted tail of the
    transcript; they are grouped into segments too, so the final seconds
    of speech are not lost. (An earlier version ignored them.)
    """
    segments: list[dict] = []
    language: str | None = None
    receipts = sorted(transcript_dir.glob("chunk-*.json"))
    for receipt_path in receipts:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        language = language or receipt.get("language")
        for seg in receipt.get("segments", []):
            text = (seg.get("text") or "").strip()
            if text:
                segments.append({
                    "start": float(seg["start"]),
                    "end": float(seg["end"]),
                    "text": text,
                })
    if receipts:
        last = json.loads(receipts[-1].read_text(encoding="utf-8"))
        segments.extend(_group_tail_words(last.get("pending_words", [])))
    if not segments:
        raise SystemExit("no transcript segments found")
    return segments, language


def run(input_path: Path, work_dir: Path, model_dir: Path,
        language: str | None = None,
        translate: str | None = None,
        translate_file: Path | None = None,
        translate_endpoint: str | None = None,
        skip_transcribe: bool = False,
        chunk_seconds: int = 60,
        style: CaptionStyle | None = None) -> dict:
    """Run the full pipeline. Returns the job dict written to job.json.

    Exit code contract: returns a dict with ``status == "needs_translation"``
    when no Persian translation is available yet; the CLI maps that to
    exit code 2.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    transcript_dir = work_dir / "transcript"

    if not skip_transcribe:
        run_transcribe(input_path, transcript_dir, model_dir, language, chunk_seconds)
    segments, detected_lang = load_segments(transcript_dir)
    caps = captions_mod.split_segments(segments)
    (work_dir / "segments.json").write_text(
        json.dumps(caps, ensure_ascii=False, indent=2), encoding="utf-8")
    (work_dir / "segments_src.json").write_text(
        json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(segments)} transcript segments -> {len(caps)} captions")

    fa_path = translate_file or (work_dir / "segments_fa.json")
    if not fa_path.exists() and translate == "libretranslate":
        source = language or detected_lang or "auto"
        print(f"auto-translating {len(caps)} captions ({source} -> fa)...")
        try:
            auto = LibreTranslateTranslator(translate_endpoint).translate(caps, source)
        except RuntimeError as exc:
            print(f"auto-translation failed ({exc}); falling back to manual translation.")
            auto = None
        if auto:
            fa_path.write_text(
                json.dumps(auto, ensure_ascii=False, indent=2), encoding="utf-8")
            print("auto-translation ok:", fa_path)
    if not fa_path.exists():
        print("STATUS needs_translation: no Persian translation yet.")
        print(f"Translate {work_dir / 'segments_src.json'} (same order, same count),")
        print(f"save as {fa_path}, then re-run with --skip-transcribe.")
        job = {
            "status": "needs_translation",
            "input": str(input_path),
            "captions": len(caps),
            "segments": len(segments),
            "segments_json": str(work_dir / "segments_src.json"),
            "translate_to": str(fa_path),
        }
        (work_dir / "job.json").write_text(
            json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        return job

    fa_items = load_translation_file(fa_path)
    fa_captions = captions_mod.pair_translation(caps, segments, fa_items)
    srt_path = work_dir / "subs-fa.srt"
    captions_mod.write_srt(fa_captions, srt_path)
    print("wrote", srt_path)

    output_path = work_dir / "clip-subtitled.mp4"
    burn_captions(input_path, fa_captions, output_path, work_dir / "overlays", style)
    job = {
        "status": "done",
        "input": str(input_path),
        "captions": len(fa_captions),
        "transcript": str(transcript_dir / "transcript.txt"),
        "segments_json": str(work_dir / "segments.json"),
        "translation": str(fa_path),
        "srt": str(srt_path),
        "output": str(output_path),
    }
    (work_dir / "job.json").write_text(
        json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    print("STATUS done:", output_path)
    return job
