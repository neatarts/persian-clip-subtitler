"""Download a video from a URL with yt-dlp (optional dependency).

Only used for ``pcs --url ...``. The core pipeline works on local files
and never needs this module.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def download_video(url: str, out_dir: Path) -> Path:
    """Download the best mp4 for ``url`` into ``out_dir``; returns the file path."""
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        raise SystemExit(
            "yt-dlp is not installed; install it (pip install yt-dlp) "
            "or pass a local video file instead of --url."
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    template = str(out_dir / "%(title)s [%(id)s].%(ext)s")
    cmd = [ytdlp, "--format", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
           "--output", template, url]
    print("downloading:", url, flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"yt-dlp download failed (exit {result.returncode})")
    videos = sorted(out_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    if not videos:
        raise SystemExit("yt-dlp finished but no mp4 file was produced")
    return videos[-1]
