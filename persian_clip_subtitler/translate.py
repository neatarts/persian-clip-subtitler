"""Pluggable translation of captions into Persian.

Backends:
- :class:`LibreTranslateTranslator` — free, no API key. Uses a
  LibreTranslate instance (public one by default; point it at your own
  instance with ``--translate-endpoint`` or ``PCS_TRANSLATE_ENDPOINT``).
  Quality is decent for short captions; a human- or LLM-reviewed
  translation is always better.
- :func:`load_translation_file` — bring your own translation as a JSON
  file (see ``examples/segments_fa.example.json``). This is the
  recommended path when subtitle quality matters.

To add a new backend (e.g. an LLM API), subclass :class:`Translator` and
implement :meth:`Translator.translate`; then wire it up in ``cli.py``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_ENDPOINT = "https://libretranslate.com/translate"


class Translator:
    """Translate a list of captions into Persian.

    Input and output are lists of ``{"text": ...}`` dicts, same order and
    length. ``source`` is the source language code (``"ar"``, ``"en"``,
    ``"fa"``, ...); ``"fa"`` means "already Persian".
    """

    def translate(self, captions: list[dict], source: str) -> list[dict]:
        raise NotImplementedError


class LibreTranslateTranslator(Translator):
    """Translate via a LibreTranslate-compatible HTTP endpoint."""

    def __init__(self, endpoint: str | None = None, timeout: int = 30) -> None:
        self.endpoint = endpoint or os.environ.get(
            "PCS_TRANSLATE_ENDPOINT", DEFAULT_ENDPOINT)
        self.timeout = timeout

    def translate(self, captions: list[dict], source: str) -> list[dict]:
        if source == "fa":
            return [{"text": cap["text"]} for cap in captions]
        translated: list[dict] = []
        for cap in captions:
            payload = json.dumps({
                "q": cap["text"].replace("\n", " "),
                "source": source, "target": "fa", "format": "text",
            }).encode("utf-8")
            request = urllib.request.Request(
                self.endpoint, data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"LibreTranslate request failed: {exc}") from exc
            text = (data.get("translatedText") or "").strip()
            if not text:
                raise RuntimeError("LibreTranslate returned an empty translation")
            translated.append({"text": text})
        return translated


def load_translation_file(path: Path) -> list[dict]:
    """Load a translation JSON file (list of ``{"start", "end", "text"}``)."""
    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError(f"translation file must contain a JSON list: {path}")
    return items
