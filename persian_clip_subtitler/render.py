"""Render Persian captions as rounded-box PNG overlays and burn them onto a video.

Style: white bold Vazirmatn text on a semi-transparent dark rounded box,
tight around the text, centred horizontally in the lower part of the frame.
ffmpeg/libass cannot draw rounded corners, so each caption is rendered
with Pillow (raqm shaping, required for correct Persian letter joining)
and overlaid with ffmpeg at its time range.

The bundled Vazirmatn font is licensed under the SIL Open Font License 1.1
(see ``fonts/OFL.txt``).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_FONT = HERE / "fonts" / "Vazirmatn-Bold.ttf"


@dataclass
class CaptionStyle:
    """Visual style of the burned-in captions."""

    font_path: Path = DEFAULT_FONT
    font_size: int = 27
    pad_x: int = 30          # horizontal padding inside the box
    pad_y: int = 14          # vertical padding inside the box
    radius: int = 16         # corner radius of the box
    box_alpha: int = 165     # 0 (transparent) .. 255 (opaque); ~65% opaque
    bottom_margin: int = 120  # px from the video bottom edge to the box bottom
    leading: int = 6         # extra px between the two lines of a caption


def _font(style: CaptionStyle):
    from PIL import ImageFont

    return ImageFont.truetype(
        str(style.font_path), style.font_size,
        layout_engine=ImageFont.Layout.RAQM,
    )


def render_caption(text: str, out_path: Path, style: CaptionStyle | None = None) -> tuple[int, int]:
    """Render one caption to a transparent PNG; returns ``(width, height)``."""
    from PIL import Image, ImageDraw

    style = style or CaptionStyle()
    font = _font(style)
    lines = text.split("\n")
    ascent, descent = font.getmetrics()
    line_h = ascent + descent
    tmp = Image.new("RGBA", (10, 10))
    probe = ImageDraw.Draw(tmp)
    widths = []
    for line in lines:
        box = probe.textbbox((0, 0), line, font=font)
        widths.append(box[2] - box[0])
    text_w = max(widths)
    text_h = line_h * len(lines) + style.leading * (len(lines) - 1)
    w = text_w + style.pad_x * 2
    h = text_h + style.pad_y * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=style.radius, fill=(0, 0, 0, style.box_alpha)
    )
    for i, line in enumerate(lines):
        cy = style.pad_y + line_h * i + style.leading * i + line_h // 2
        draw.text((w // 2, cy), line, font=font, fill=(255, 255, 255, 255), anchor="mm")
    img.save(out_path)
    return w, h


def probe_height(video: Path) -> int:
    """Video frame height in pixels (via ffprobe)."""
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=height", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True,
    )
    return int(out.stdout.strip())


def burn_captions(input_video: Path, captions: list[dict], output_video: Path,
                  work_dir: Path, style: CaptionStyle | None = None,
                  crf: int = 20, preset: str = "veryfast") -> Path:
    """Burn rounded-box captions onto a video; returns the output path.

    ``captions`` is a list of ``{"start", "end", "text"}`` dicts.
    The audio stream is copied untouched; video is re-encoded with libx264.
    """
    style = style or CaptionStyle()
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    work_dir.mkdir(parents=True, exist_ok=True)
    vh = probe_height(input_video)
    cmd = [ffmpeg, "-y", "-i", str(input_video)]
    sizes = []
    for i, cap in enumerate(captions):
        png = work_dir / f"cap-{i:03d}.png"
        w, h = render_caption(cap["text"], png, style)
        sizes.append((w, h))
        cmd += ["-i", str(png)]

    # filter chain: [0:v][1:v]overlay...[v0]; [v0][2:v]overlay...[v1]; ...
    parts = []
    prev = "0:v"
    for i, cap in enumerate(captions):
        w, h = sizes[i]
        y = vh - style.bottom_margin - h
        out_label = f"v{i}" if i < len(captions) - 1 else "vout"
        parts.append(
            f"[{prev}][{i + 1}:v]overlay=x=(W-w)/2:y={y}:"
            f"enable='between(t,{cap['start']:.3f},{cap['end']:.3f})'[{out_label}]"
        )
        prev = out_label
    cmd += [
        "-filter_complex", ";".join(parts),
        "-map", "[vout]", "-map", "0:a",
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
        "-pix_fmt", "yuv420p", "-c:a", "copy", str(output_video),
    ]
    print(f"burning {len(captions)} rounded captions onto {input_video} -> {output_video}",
          flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr[-3000:], file=sys.stderr)
        raise SystemExit(f"ffmpeg burn-in failed (exit {result.returncode})")
    print("wrote", output_video)
    return output_video


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captions", type=Path, required=True,
                        help="JSON list of {start, end, text}")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=None,
                        help="overlay PNG directory (default: <output-dir>/overlays)")
    parser.add_argument("--font-size", type=int, default=27)
    parser.add_argument("--bottom-margin", type=int, default=120)
    parser.add_argument("--box-alpha", type=int, default=165)
    args = parser.parse_args()

    caps = json.loads(args.captions.read_text(encoding="utf-8"))
    work = args.work_dir or args.output.parent / "overlays"
    style = CaptionStyle(font_size=args.font_size,
                         bottom_margin=args.bottom_margin,
                         box_alpha=args.box_alpha)
    burn_captions(args.input, caps, args.output, work, style)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
