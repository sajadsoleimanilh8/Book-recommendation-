"""Text-to-speech audiobook generation — the AudiobookEngine.

Extracted verbatim from `engine.py` in the Phase C restructure, with one
necessary import-path fix, not a logic change (RESTRUCTURE-NOTES B-6):
`AUDIO_DIR` is computed from `__file__`. This module moved one directory
deeper than `engine.py` was, so `parents[1]` replaces `parent` to keep
resolving to the same `backend/audio_outputs` — the identical directory
`main.py:191` computes independently for the streaming route, and which
that comment there still calls out must match. Confirmed unchanged:
str(AUDIO_DIR) is identical before and after this move.

Takes a `Recommender` reference for `_boost_book`, hence the
TYPE_CHECKING import below rather than a real one — importing
`services.recommendation` for real would be circular, since
`Recommender` constructs this class.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

from services.providers.gutenberg import GutenbergClient

if TYPE_CHECKING:
    from services.recommendation import Recommender

import logging

log = logging.getLogger(__name__)


class AudiobookEngine:
    def __init__(self, recommender: "Recommender"):
        self.recommender = recommender

    # F-04 / F-06: the audio destination is derived here, never supplied by
    # the caller. book_id is a validated positive int, so f"{book_id}.mp3"
    # cannot contain a separator or traversal sequence. The resolved-parent
    # assertion below is defence in depth, not the primary control.
    AUDIO_DIR = Path(__file__).resolve().parents[1] / "audio_outputs"

    def generate(
        self,
        book_name: str,
        book_id: int,
        lang: str = "en",
        chunk_chars: int = 4_000,
    ) -> Dict[str, Any]:
        try:
            from gtts import gTTS
        except ImportError:
            log.error("gTTS library not found. Please install it: pip install gTTS")
            return {"ok": False, "error": "gTTS not installed"}

        try:
            book_id = int(book_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "book_id must be an integer"}
        if book_id < 1:
            return {"ok": False, "error": "book_id must be a positive integer"}

        self.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        output_path = (self.AUDIO_DIR / f"{book_id}.mp3").resolve()
        if output_path.parent != self.AUDIO_DIR:
            log.error(f"Refusing to write outside audio_outputs: {output_path}")
            return {"ok": False, "error": "Invalid output location"}

        log.info(f"Fetching '{book_name}' from Gutenberg…")
        try:
            text = GutenbergClient.get_text(book_name)
        except Exception as e:
            log.error(f"Failed to get text: {e}")
            return {"ok": False, "error": str(e)}

        chunks = self._smart_chunks(text, chunk_chars)
        log.info(f"Converting {len(chunks)} chunks to audio…")

        # F-06: temp files used to be written to the process CWD as
        # _tmp_{i}.mp3, so two concurrent generations overwrote each other's
        # fragments and produced corrupt audio. Each run now gets its own
        # private directory.
        tmp_dir = Path(tempfile.mkdtemp(prefix="digikitab_tts_"))
        try:
            parts: List[Path] = []
            for i, chunk in enumerate(chunks):
                part = tmp_dir / f"{i:05d}.mp3"
                gTTS(text=chunk, lang=lang).save(str(part))
                parts.append(part)

            # Assemble into the temp dir first, then move into place, so an
            # interrupted run cannot leave a truncated file where a previous
            # good recording was.
            staged = tmp_dir / "assembled.mp3"
            with staged.open("wb") as out:
                for part in parts:
                    if part.exists():
                        out.write(part.read_bytes())

            os.replace(staged, output_path)

            self._boost_book(book_name, boost=0.3)
            log.info(f"Audiobook saved: {output_path.name}")

            return {
                "ok": True,
                # Relative name only — never leak absolute server paths.
                "output_file": output_path.name,
                "book_id": book_id,
                "chunks": len(chunks),
                "characters": len(text),
                "message": f"Audiobook saved for book {book_id}",
            }
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _smart_chunks(text: str, max_chars: int) -> List[str]:
        sentences = re.split(r'(?<=[.!?])\s+', text)
        chunks: List[str] = []
        current_chunk: str = ""

        for sent in sentences:
            if len(current_chunk) + len(sent) <= max_chars:
                if not current_chunk:
                    current_chunk = sent
                else:
                    current_chunk += " " + sent
            else:
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())
                current_chunk = sent

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks or [text[:max_chars]] if text else []

    def _boost_book(self, book_name: str, boost: float = 0.2):
        try:
            mask = self.recommender.df["title"].str.lower().str.contains(
                book_name.lower(), na=False
            )
            if mask.any():
                self.recommender.df.loc[mask, "comment_score"] += boost
                log.info(f"Boosted '{book_name}' by {boost}")
        except Exception:
            pass
