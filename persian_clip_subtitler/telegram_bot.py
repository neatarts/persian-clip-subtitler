"""Optional Telegram intake: turn a bot into a clip-subtitling service.

One run: fetch pending updates -> download new clips -> run the pipeline
(transcribe + translate + burn subtitles) -> send the finished video and
the transcript back to the same chat.

This module is optional and separate from the core CLI. It needs nothing
except a bot token from the environment:

    TELEGRAM_BOT_TOKEN   (required; from @BotFather — never hardcode it)

Usage:
    TELEGRAM_BOT_TOKEN=<your-token> python -m persian_clip_subtitler.telegram_bot \\
        --model models/whisper-small --state-dir state --inbox inbox

State lives in the state directory so a scheduled run can resume it:
  state/offset.txt     next getUpdates offset
  state/seen.json      file_ids already downloaded (skip re-downloads)

Telegram only delivers files up to 50 MB to bots; larger files are
refused with a message to the sender.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.telegram.org"
MAX_FILE_SIZE = 50 * 1024 * 1024


def token() -> str:
    value = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not value:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    return value


def api_call(method: str, payload: dict | None = None, timeout: int = 60):
    url = f"{API}/bot{token()}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API {method} failed: HTTP {exc.code}: {detail}")


def api_download(file_path: str, target: Path) -> None:
    url = f"{API}/file/bot{token()}/{file_path}"
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=300) as response, target.open("wb") as out:
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)


def send_message(chat_id: int, text: str) -> None:
    api_call("sendMessage", {"chat_id": chat_id, "text": text})


def send_video(chat_id: int, video: Path, caption: str) -> None:
    boundary = "----pcs-boundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="video"; filename="{video.name}"\r\n'
        f"Content-Type: video/mp4\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    url = f"{API}/bot{token()}/sendVideo"
    request = urllib.request.Request(url, method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with video.open("rb") as handle:
        payload = body + handle.read() + tail
    request.data = payload
    with urllib.request.urlopen(request, timeout=300) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"sendVideo failed: {result}")


def extension_for(kind: str, mime_type: str | None) -> str:
    if kind in ("video", "video_note"):
        return "mp4"
    if kind == "voice":
        return "ogg"
    if mime_type:
        mime = mime_type.lower()
        if mime == "audio/mpeg":
            return "mp3"
        if mime in ("audio/mp4", "audio/x-m4a"):
            return "m4a"
        if mime == "audio/ogg":
            return "ogg"
        if mime == "audio/wav":
            return "wav"
    return "mp3"


def process_one(chat_id: int, kind: str, media: dict, inbox_dir: Path,
                work_root: Path, seen: set[str], model: str | None,
                language: str | None) -> str:
    file_id = media["file_id"]
    if file_id in seen:
        return "already_seen"
    size = media.get("file_size", 0) or 0
    if size > MAX_FILE_SIZE:
        send_message(chat_id, "That file is over 50 MB, which Telegram does not deliver to bots.")
        seen.add(file_id)
        return "too_large"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    ext = extension_for(kind, media.get("mime_type"))
    filename = f"{stamp}_{file_id}.{ext}"
    target = inbox_dir / filename
    file_info = api_call("getFile", {"file_id": file_id})
    api_download(file_info["result"]["file_path"], target)
    seen.add(file_id)
    send_message(chat_id, f"Received ({filename}). Transcribing and subtitling…")

    job_dir = work_root / target.stem
    cmd = [sys.executable, "-m", "persian_clip_subtitler",
           "--input", str(target), "--work-dir", str(job_dir),
           "--translate", "libretranslate"]
    if model:
        cmd += ["--model", model]
    if language:
        cmd += ["--language", language]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8")) \
        if (job_dir / "job.json").exists() else {"status": "failed"}
    if job.get("status") == "done":
        transcript = (Path(job["transcript"]).read_text(encoding="utf-8")
                      if job.get("transcript") else "")
        send_video(chat_id, Path(job["output"]),
                   "Your clip with Persian subtitles is ready.")
        if transcript.strip():
            send_message(chat_id, "Transcript:\n" + transcript.strip()[:3500])
        return "done"
    send_message(
        chat_id,
        "Transcription is done, but automatic translation did not complete. "
        "The transcript is ready for manual review.")
    return "needs_translation"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=Path("state"))
    parser.add_argument("--inbox", type=Path, default=Path("inbox"))
    parser.add_argument("--model", default=None,
                        help="faster-whisper model directory (or $PCS_MODEL_DIR)")
    parser.add_argument("--language", default=None,
                        help="transcription language, e.g. ar, en, fa")
    args = parser.parse_args()

    state_dir = args.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir = args.inbox
    inbox_dir.mkdir(parents=True, exist_ok=True)

    offset_path = state_dir / "offset.txt"
    offset = int(offset_path.read_text().strip()) if offset_path.exists() else 0
    seen_path = state_dir / "seen.json"
    seen = set(json.loads(seen_path.read_text()) if seen_path.exists() else [])

    updates = api_call("getUpdates", {"offset": offset, "limit": 100, "timeout": 0})
    results = updates.get("result", [])
    print(f"{len(results)} pending updates (offset {offset})")
    last_id = offset
    for update in results:
        last_id = max(last_id, update["update_id"] + 1)
        message = update.get("message") or {}
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        if not chat_id:
            continue
        media = None
        kind = None
        for candidate in ("video", "video_note", "audio", "voice"):
            if message.get(candidate):
                media, kind = message[candidate], candidate
                break
        if media is None:
            if chat.get("type") == "private" and message.get("text"):
                send_message(
                    chat_id,
                    "Hi! Send me a video, voice message or audio file and "
                    "I will send it back with Persian subtitles.")
            continue
        try:
            outcome = process_one(chat_id, kind, media, inbox_dir,
                                  state_dir / "jobs", seen, args.model, args.language)
            print(update["update_id"], kind, outcome)
        except Exception as exc:  # keep the queue moving; log and continue
            print(f"update {update['update_id']} failed: {exc}", file=sys.stderr)
            try:
                send_message(
                    chat_id,
                    "Something went wrong while processing that clip; "
                    "please try again later.")
            except Exception:
                pass
        time.sleep(1)

    offset_path.write_text(str(last_id))
    seen_path.write_text(json.dumps(sorted(seen), ensure_ascii=False))
    print("done; next offset", last_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
