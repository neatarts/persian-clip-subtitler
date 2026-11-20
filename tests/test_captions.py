"""Unit tests for the pure caption logic (no model, no network, no ffmpeg).

Run:  pytest tests/ -v
"""

import json

import pytest

from persian_clip_subtitler import captions as cap


def test_srt_timestamp():
    assert cap.srt_timestamp(0) == "00:00:00,000"
    assert cap.srt_timestamp(61.5) == "00:01:01,500"
    assert cap.srt_timestamp(3723.456) == "01:02:03,456"


def test_split_segment_line_limits():
    seg = {"start": 0.0, "end": 20.0,
           "text": "این یک جمله آزمایشی نسبتا طولانی است که باید به چند کپشن تقسیم شود"}
    out = cap.split_segment(seg)
    assert out, "a non-empty segment must produce captions"
    for c in out:
        lines = c["text"].split("\n")
        assert len(lines) <= cap.MAX_LINES_PER_CAPTION
        for line in lines:
            assert len(line) <= cap.MAX_CHARS_PER_LINE
    # Text is preserved (whitespace aside).
    assert " ".join(c["text"].replace("\n", " ") for c in out).split() == seg["text"].split()


def test_split_segment_timing_is_monotonic_and_bounded():
    seg = {"start": 10.0, "end": 30.0,
           "text": " ".join(["کلمه"] * 60)}
    out = cap.split_segment(seg)
    assert out[0]["start"] == pytest.approx(10.0)
    assert out[-1]["end"] == pytest.approx(30.0)
    for prev, cur in zip(out, out[1:]):
        assert cur["start"] >= prev["end"] - 1e-9
        assert cur["end"] - cur["start"] <= cap.MAX_CAPTION_SECONDS + 1e-9


def test_split_segment_single_long_word_does_not_crash():
    seg = {"start": 0.0, "end": 5.0, "text": "averyveryverylongwordthatexceedsthelimit"}
    out = cap.split_segment(seg)
    assert len(out) == 1
    assert out[0]["text"] == seg["text"]


def test_write_srt_format(tmp_path):
    caps = [
        {"start": 0.0, "end": 2.5, "text": "سلام دنیا"},
        {"start": 3.0, "end": 5.0, "text": "خط اول\nخط دوم"},
    ]
    path = cap.write_srt(caps, tmp_path / "subs.srt")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("1\n00:00:00,000 --> 00:00:02,500\nسلام دنیا\n")
    assert "2\n00:00:03,000 --> 00:00:05,000\nخط اول\nخط دوم\n" in text


def test_pair_translation_per_segment():
    segments = [
        {"start": 0.0, "end": 10.0, "text": "hello world this is a test"},
        {"start": 10.0, "end": 20.0, "text": "another segment here"},
    ]
    captions = cap.split_segments(segments)
    fa = [
        {"start": 0.0, "end": 10.0, "text": "سلام دنیا این یک آزمایش است"},
        {"start": 10.0, "end": 20.0, "text": "یک بخش دیگر اینجا"},
    ]
    out = cap.pair_translation(captions, segments, fa)
    assert out
    # All translated words survive, in order.
    assert " ".join(c["text"].replace("\n", " ") for c in out).split() == \
        " ".join(item["text"] for item in fa).split()


def test_pair_translation_per_caption():
    segments = [{"start": 0.0, "end": 10.0, "text": "hello world"}]
    captions = cap.split_segments(segments)
    fa = [{"text": "سلام دنیا"} for _ in captions]
    out = cap.pair_translation(captions, segments, fa)
    assert [c["text"] for c in out] == ["سلام دنیا"] * len(captions)
    assert out[0]["start"] == captions[0]["start"]


def test_pair_translation_count_mismatch():
    segments = [{"start": 0.0, "end": 10.0, "text": "hello"}]
    captions = cap.split_segments(segments)
    with pytest.raises(ValueError, match="count mismatch"):
        cap.pair_translation(captions, segments, [{"text": "a"}, {"text": "b"}, {"text": "c"}])


def test_load_translation_file_rejects_non_list(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"text": "nope"}), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON list"):
        cap.load_translation_file(bad)


def test_load_segments_includes_pending_tail_words(tmp_path):
    """The last chunk's pending_words are the transcript tail; they must
    become segments too (regression: an earlier version dropped them)."""
    from persian_clip_subtitler.pipeline import load_segments

    transcript_dir = tmp_path / "transcript"
    transcript_dir.mkdir()
    receipt = {
        "start_sample": 0,
        "end_sample": 96000,
        "language": "fa",
        "segments": [{"start": 0.0, "end": 3.72, "text": "راه ایمان"}],
        "pending_words": [
            {"start": 3.72, "end": 4.26, "text": " زبان"},
            {"start": 4.26, "end": 4.76, "text": " خودت"},
        ],
    }
    (transcript_dir / "chunk-0000000000000000.json").write_text(
        json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    segments, lang = load_segments(transcript_dir)
    assert lang == "fa"
    assert [s["text"] for s in segments] == ["راه ایمان", "زبان خودت"]
    assert segments[-1]["end"] == pytest.approx(4.76)


def test_render_caption_smoke(tmp_path):
    """Render one caption PNG with the bundled font (needs Pillow + raqm)."""
    from PIL import ImageFont

    from persian_clip_subtitler.render import CaptionStyle, render_caption

    try:
        ImageFont.truetype(str(CaptionStyle().font_path), 27,
                           layout_engine=ImageFont.Layout.RAQM)
    except Exception:
        pytest.skip("Pillow without raqm support; cannot shape Persian text")
    out = tmp_path / "cap.png"
    w, h = render_caption("سلام دنیا\nخط دوم", out)
    assert out.exists() and out.stat().st_size > 0
    assert w > 0 and h > 0
