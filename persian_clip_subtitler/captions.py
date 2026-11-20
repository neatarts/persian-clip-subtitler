"""Split transcript segments into caption-sized pieces and write SRT files.

Caption rules (the style this project is built around):
- at most 20 characters per line,
- at most 2 lines per caption,
- at most ~7 seconds per caption.

Timing inside a segment is split proportionally to character count, because
word-level timestamps are not always available.
"""

from __future__ import annotations

import json
from pathlib import Path

MAX_CHARS_PER_LINE = 20
MAX_LINES_PER_CAPTION = 2
MAX_CAPTION_SECONDS = 7.0


def srt_timestamp(seconds: float) -> str:
    """Format seconds as an SRT timestamp (HH:MM:SS,mmm)."""
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def split_segment(seg: dict) -> list[dict]:
    """Split one long transcript segment into caption-sized pieces.

    ``seg`` is ``{"start": float, "end": float, "text": str}``.
    Returns a list of captions with the same keys, each at most two lines
    of at most ``MAX_CHARS_PER_LINE`` characters. Times are split
    proportionally to character offsets in the joined text, so the last
    caption ends exactly at the segment end.
    """
    words = seg["text"].split()
    if not words:
        return []
    total_chars = len(" ".join(words))
    duration = max(0.1, seg["end"] - seg["start"])
    # Character offsets of every word in the joined text. (An earlier
    # version tracked the position by counting appended characters and
    # lost one char per line break, so the last caption ended early.)
    starts: list[int] = []
    ends: list[int] = []
    pos = 0
    for word in words:
        starts.append(pos)
        pos += len(word)
        ends.append(pos)
        pos += 1  # the space after the word
    captions: list[dict] = []
    lines: list[str] = []
    line_len = 0
    cap_start_idx = 0

    def flush(last_idx: int) -> None:
        nonlocal lines, line_len, cap_start_idx
        if not lines:
            return
        captions.append({
            "start": seg["start"] + (starts[cap_start_idx] / total_chars) * duration,
            "end": seg["start"] + (ends[last_idx] / total_chars) * duration,
            "text": "\n".join(lines),
        })
        lines, line_len = [], 0
        cap_start_idx = last_idx + 1

    for i, word in enumerate(words):
        word_len = len(word)
        # New line if the current line would exceed the limit.
        if lines and line_len + 1 + word_len > MAX_CHARS_PER_LINE:
            if len(lines) >= MAX_LINES_PER_CAPTION:
                flush(i - 1)
            else:
                lines.append("")
                line_len = 0
        if not lines:
            lines.append("")
            line_len = 0
        if line_len:
            lines[-1] += " "
            line_len += 1
        lines[-1] += word
        line_len += word_len
        # Flush when the caption is full or too long in time.
        cap_start = seg["start"] + (starts[cap_start_idx] / total_chars) * duration
        cap_end = seg["start"] + (ends[i] / total_chars) * duration
        if cap_end - cap_start >= MAX_CAPTION_SECONDS and len(lines) >= MAX_LINES_PER_CAPTION:
            flush(i)
    flush(len(words) - 1)
    return captions


def split_segments(segments: list[dict]) -> list[dict]:
    """Split every transcript segment into captions."""
    captions: list[dict] = []
    for seg in segments:
        captions.extend(split_segment(seg))
    return captions


def write_srt(captions: list[dict], path: Path) -> Path:
    """Write captions (``{"start", "end", "text"}``) as an SRT file."""
    parts: list[str] = []
    for i, cap in enumerate(captions, 1):
        parts.append(
            f"{i}\n{srt_timestamp(cap['start'])} --> {srt_timestamp(cap['end'])}\n{cap['text']}\n"
        )
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def pair_translation(captions: list[dict], segments: list[dict],
                     fa_items: list[dict]) -> list[dict]:
    """Pair a Persian translation file with source captions.

    The translation file may be either:
    - per-segment: same length and order as the transcript ``segments``
      (each item is split into captions the same way as the source). Items
      may carry their own ``start``/``end`` or borrow the source timings.
    - per-caption: same length and order as ``captions`` (paired by index;
      items only need ``text``).

    Returns the Persian captions with source timings. Raises ``ValueError``
    when the counts match neither form.
    """
    has_times = all(
        isinstance(item, dict) and "start" in item and "end" in item
        for item in fa_items
    )
    if has_times and len(fa_items) == len(segments):
        fa_captions: list[dict] = []
        for seg in fa_items:
            fa_captions.extend(split_segment(seg))
        return fa_captions
    if len(fa_items) == len(captions):
        return [
            {"start": cap["start"], "end": cap["end"],
             "text": (item["text"] if isinstance(item, dict) else str(item)).strip()}
            for cap, item in zip(captions, fa_items)
        ]
    if len(fa_items) == len(segments):
        # Per-segment translation without timings: borrow the source timings.
        fa_captions = []
        for src, item in zip(segments, fa_items):
            text = item["text"] if isinstance(item, dict) else str(item)
            fa_captions.extend(split_segment(
                {"start": src["start"], "end": src["end"], "text": text}))
        return fa_captions
    raise ValueError(
        f"translation count mismatch: {len(fa_items)} items vs "
        f"{len(segments)} segments / {len(captions)} captions"
    )


def load_translation_file(path: Path) -> list[dict]:
    """Load a translation JSON file (list of ``{"start", "end", "text"}``)."""
    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError(f"translation file must contain a JSON list: {path}")
    return items
