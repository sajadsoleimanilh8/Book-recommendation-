"""Section 29's pipeline for one already-stored upload: Extract -> Chunk ->
Embed -> READY. Runs inside `jobs.LIBRARY_REGISTRY`, off the request thread.

Deliberately synchronous: `JobRegistry` submits plain callables to its own
`ThreadPoolExecutor`, the same way `AudiobookEngine.generate` already does.

Every step advances `book.upload_status` and commits before the next runs,
so a failure partway through leaves an honest, resumable record of how far
it got — the same discipline `enrichment_status`/`is_complete` already
apply elsewhere (F-17): never let a partial result look like success, and
never leave a failure looking like nothing happened.
"""

from __future__ import annotations

import logging

from db import SessionLocal
from models import Book, BookText
from scripts.chunk_pass import chunk_one
from scripts.embed_pass import run as embed_run
from services.extraction import extract_text

log = logging.getLogger("services.library_ingest")

EMBED_BACKEND = "minilm"
# One book's chunks, never in the thousands — generous headroom over any
# realistic chapter count, not a real ceiling.
EMBED_LIMIT = 10_000


def process_upload(book_id: int, uploads_dir) -> dict:
    """Extract, chunk and embed one uploaded book.

    Returns a plain dict shaped for `JobRegistry` (`{"ok": bool, ...}`).
    `uploads_dir` is passed in rather than imported from `main`, so this
    module has no import-time dependency on `main` finishing its own
    module-level assignments first — the same ordering hazard `api/library.py`
    already works around for `main.UPLOADS_DIR`.
    """
    with SessionLocal() as session:
        book = session.get(Book, book_id)
        if book is None:
            return {"ok": False, "error": f"book {book_id} not found"}
        if book.owner_id is None:
            # Structural guard against a caller mistake, not expected to
            # trigger: this pipeline must never run over a catalogue row —
            # chunk_one's visibility would still come out right (user_id is
            # read from the row, not trusted from the caller), but a
            # catalogue book reaching this function at all means something
            # upstream picked the wrong book.
            return {"ok": False, "error": f"book {book_id} is not a private upload"}
        if not book.storage_path or not book.file_format:
            return {"ok": False, "error": f"book {book_id} has no stored file"}

        owner_id = book.owner_id
        file_format = book.file_format
        storage_path = book.storage_path

        try:
            content = (uploads_dir / storage_path).read_bytes()
            text = extract_text(file_format, content)
        except Exception as exc:
            log.warning(f"extraction failed for book {book_id}: {type(exc).__name__}: {exc}")
            book.upload_status = "extraction_failed"
            session.commit()
            return {"ok": False, "error": f"extraction failed: {type(exc).__name__}: {exc}"}

        if not text.strip():
            book.upload_status = "extraction_failed"
            session.commit()
            return {"ok": False, "error": "extracted text was empty"}

        existing = session.get(BookText, book_id)
        if existing is not None:
            existing.source = file_format
            existing.content = text
            existing.char_count = len(text)
            existing.is_complete = True
        else:
            session.add(
                BookText(
                    book_id=book_id,
                    source=file_format,
                    content=text,
                    char_count=len(text),
                    # Unlike the catalogue's Gutenberg excerpt (F-17: opening
                    # chapters only), this genuinely is the whole book —
                    # extract_text has no length cap for an upload.
                    is_complete=True,
                )
            )
        book.upload_status = "extracted"
        session.commit()

        try:
            chunk_count = chunk_one(session, book_id, text, origin="text", user_id=owner_id)
        except Exception as exc:
            log.warning(f"chunking failed for book {book_id}: {type(exc).__name__}: {exc}")
            book.upload_status = "chunking_failed"
            session.commit()
            return {"ok": False, "error": f"chunking failed: {type(exc).__name__}: {exc}"}

        book.upload_status = "chunked"
        session.commit()

        if chunk_count == 0:
            # Real content, but nothing survived chunking (e.g. entirely
            # front-matter-shaped, or too short) — not a failure, just
            # nothing to search. Honest terminal state, not a stall.
            book.upload_status = "ready"
            session.commit()
            return {"ok": True, "book_id": book_id, "chunks": 0, "upload_status": "ready"}

    # A fresh session and connection for the embedding step, matching how
    # `embed_pass` is normally invoked (its own CLI run) rather than holding
    # the extraction/chunking transaction open across a GPU batch encode.
    try:
        embed_result = embed_run(
            limit=EMBED_LIMIT, redo=False, backend_name=EMBED_BACKEND, book_ids=[book_id]
        )
    except Exception as exc:
        log.warning(f"embedding failed for book {book_id}: {type(exc).__name__}: {exc}")
        with SessionLocal() as session:
            book = session.get(Book, book_id)
            if book is not None:
                book.upload_status = "embedding_failed"
                session.commit()
        return {"ok": False, "error": f"embedding failed: {type(exc).__name__}: {exc}"}

    with SessionLocal() as session:
        book = session.get(Book, book_id)
        if book is None:
            return {"ok": False, "error": f"book {book_id} vanished during embedding"}
        if embed_result.get("failed", 0) > 0:
            book.upload_status = "embedding_failed"
            session.commit()
            return {
                "ok": False,
                "error": f"embedding failed for {embed_result['failed']} chunk(s)",
            }
        book.upload_status = "ready"
        session.commit()

    return {
        "ok": True,
        "book_id": book_id,
        "chunks": chunk_count,
        "embedded": embed_result.get("embedded", 0),
        "upload_status": "ready",
    }
