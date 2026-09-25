"""Personal library — POST/GET /api/library/* — sections 29/30, Phase 4.

Slice 2a of Phase 4, per the handoff's agreed order: this is the upload
endpoint and its attestation, and nothing past it. It validates a file,
records the reader's explicit ownership claim, and stores the file with a
`books` row pointing at it — `upload_status="uploaded"`. It does not
extract, chunk, embed, or search the content; those are later slices in the
same order (TXT+EPUB extraction next, then a background job to run it, then
private search wired into the AI Librarian). A book sits at "uploaded"
until that pipeline exists to move it further, which is honest rather than
a stall — the same distinction F-17/F-53 already draw elsewhere in this
project between "not built yet" and "silently broken".

Copyright posture (OI-4, extended 2026-09-22): extraction and retrieval are
permitted, rendering is not. Nothing here — nor anything section 29 will
build on top of it — ever serves this file's pages back to anyone. The
reader gets a private reading assistant grounded in their own file, not a
reader for it.
"""

from __future__ import annotations

import logging
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, File, Form, Request, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

import main
import ratelimit
from auth import CurrentUser, SessionDep
from models import Book, owned_by

log = logging.getLogger("api.library")

router = APIRouter()

# Section 29 lists EPUB, PDF and TXT as the initial formats. PDF is accepted
# by the spec but its extraction pass is explicitly the *next* slice, not
# this one — queuing PDFs now would mean files sitting at "uploaded" behind
# a pipeline that does not exist, with nothing telling the reader that
# nothing is coming. Rejected here with a clear reason instead, and this is
# the one line to remove once PDF extraction ships.
SUPPORTED_FORMATS = {"epub", "txt"}
DEFERRED_FORMATS = {"pdf"}

# Generous for text-primary content (even an image-heavy EPUB rarely nears
# this), bounded against one caller filling the disk. A documented,
# easily-adjusted constant, not a config surface — nothing else in this
# project makes upload limits configurable per deployment yet either.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# An EPUB's `mimetype` entry is exactly "application/epub+zip" (20 bytes);
# anything much larger is not one, and is not worth decompressing to find out.
MAX_MIMETYPE_BYTES = 64

# Display only — see `storage_path` for why this never reaches the disk.
# Bounded so a pathological multipart header cannot become a pathological row.
MAX_FILENAME_CHARS = 255

# Section 30. Versioned so a future change to the wording does not
# retroactively reinterpret an attestation someone already made.
ATTESTATION_VERSION = "v1"
ATTESTATION_TEXT = (
    "I own a legal copy of this book and have the right to upload it for "
    "my own private use."
)


def _validate_content(fmt: str, content: bytes) -> str | None:
    """Confirm the bytes actually look like `fmt`. Returns an error message,
    or None if the content passes.

    Extension-first, content-checked: this project has already paid once
    (services/search.py's embedding-space mismatch, F-39) for trusting a
    label over the thing itself. Deliberately not a full parse — verifying
    the file is genuinely well-formed EPUB/OPF is extraction's job, the next
    slice; this only rules out the obvious mismatch (binary garbage saved as
    .txt, a renamed non-zip saved as .epub) before anything is stored.
    """
    if fmt == "txt":
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            return "The file's content is not valid UTF-8 text."
        return None

    if fmt == "epub":
        if not zipfile.is_zipfile(BytesIO(content)):
            return "The file does not look like a valid EPUB (not a zip archive)."
        try:
            with zipfile.ZipFile(BytesIO(content)) as zf:
                names = zf.namelist()
                if "mimetype" in names:
                    # The spec fixes this entry at exactly 20 bytes. Checked
                    # before reading, because `zf.read` decompresses whatever
                    # the archive claims — a crafted entry could expand to
                    # gigabytes (a zip bomb) inside this request.
                    if zf.getinfo("mimetype").file_size > MAX_MIMETYPE_BYTES:
                        return "The file's internal mimetype entry is not an EPUB's."
                    declared = zf.read("mimetype").strip()
                    if declared != b"application/epub+zip":
                        return (
                            "The file's internal mimetype does not match EPUB "
                            f"(found {declared[:64]!r})."
                        )
                elif not any(n.lower().endswith(".opf") for n in names):
                    # Not spec-conformant (mimetype should be the first
                    # entry), but some tools produce EPUBs missing it. Only
                    # reject outright if there is no OPF anywhere either —
                    # at that point it is a zip file, not a book.
                    return "The file is a zip archive but does not look like an EPUB."
        except zipfile.BadZipFile:
            return "The file does not look like a valid EPUB (corrupt archive)."
        return None

    return f"Unrecognised format {fmt!r}."


def _book_to_dict(b: Book) -> Dict[str, Any]:
    return {
        "book_id": b.id,
        "title": b.title,
        "author": b.author,
        "file_format": b.file_format,
        "source_filename": b.source_filename,
        "file_size_bytes": b.file_size_bytes,
        "upload_status": b.upload_status,
        "attested_at": b.attested_at.isoformat() if b.attested_at else None,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


@router.post("/api/library/upload")
def upload_book(
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    file: UploadFile = File(...),
    attests_ownership: bool = Form(...),
):
    """Accept a private upload. Requires sign-in — `owner_id` has no
    meaning for an anonymous caller — and the rate limit goes before any
    work is done, same as audiobook generation and chat.

    A plain `def`, like every other route here, so FastAPI runs it in the
    threadpool. The work below — a synchronous DB commit, up to 50 MB of
    disk writes, a zip parse — would otherwise block the event loop for
    every other request while it ran.
    """
    try:
        ratelimit.hit_all(
            ratelimit.caller_keys("upload", request, user.id),
            ratelimit.UPLOAD_LIMIT,
            ratelimit.UPLOAD_WINDOW,
        )
    except ratelimit.RateLimited as limited:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"error": {"message": str(limited)}},
            headers={"Retry-After": str(limited.retry_after)},
        )

    # Section 30: the explicit claim comes before any file handling. A
    # reader who has not made it gets turned away before their upload is
    # even read, not after it is already stored.
    if not attests_ownership:
        return main.error_response(
            "Uploading requires confirming you own a legal copy of this "
            "book. Set attests_ownership to proceed.",
        )

    original_name = (file.filename or "")[:MAX_FILENAME_CHARS]
    ext = Path(original_name).suffix.lstrip(".").lower()

    if ext in DEFERRED_FORMATS:
        return main.error_response(
            "PDF uploads are part of the plan (section 29) but extraction "
            "for PDF is not built yet — upload EPUB or TXT for now.",
        )
    if ext not in SUPPORTED_FORMATS:
        return main.error_response(
            f"Unsupported file type {('.' + ext) if ext else '(none)'!r}. "
            "Upload an EPUB or TXT file.",
        )

    # Read at most one byte past the limit. Reading the whole thing first
    # and checking afterwards would cap the disk but not memory: a 5 GB
    # upload would be buffered in full before being refused.
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        return main.error_response("The uploaded file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            content={
                "error": {
                    "message": f"File exceeds the {MAX_UPLOAD_BYTES} byte limit."
                }
            },
        )

    mismatch = _validate_content(ext, content)
    if mismatch is not None:
        return main.error_response(mismatch)

    # Server-generated identity for both the durable row and the file on
    # disk. Never derived from `original_name` — F-04/F-05 already found
    # exactly this class of bug (arbitrary file overwrite, path traversal)
    # for a different feature, and the fix there was the same one applied
    # here before it could recur: a caller-supplied string is never part of
    # a filesystem path.
    external_id = uuid.uuid4().hex
    disk_filename = f"{external_id}.{ext}"
    # Referenced through `main` at call time, not aliased at module load —
    # `api.library` is imported by `main.py` itself before this constant is
    # assigned, the same ordering `audio.py` already works around for
    # `main.AUDIO_DIR`.
    main.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    disk_path = main.UPLOADS_DIR / disk_filename

    disk_path.write_bytes(content)

    title = Path(original_name).stem.strip() or "Untitled upload"
    now = datetime.now(timezone.utc)

    book = Book(
        source="upload",
        external_id=external_id,
        owner_id=user.id,
        title=title,
        upload_status="uploaded",
        source_filename=original_name,
        file_format=ext,
        storage_path=disk_filename,
        file_size_bytes=len(content),
        attested_at=now,
        attestation_version=ATTESTATION_VERSION,
    )
    try:
        session.add(book)
        session.commit()
    except Exception:
        # No half-committed state: a row that failed to save should not
        # leave an orphaned file with nothing pointing at it.
        disk_path.unlink(missing_ok=True)
        raise
    session.refresh(book)

    log.info(f"upload: user={user.id} book_id={book.id} format={ext} bytes={len(content)}")

    return {
        "ok": True,
        "message": f"'{title}' uploaded and awaiting processing.",
        "attestation_version": ATTESTATION_VERSION,
        **_book_to_dict(book),
    }


@router.get("/api/library")
def list_my_library(user: CurrentUser, session: SessionDep):
    """This reader's own uploads, and nobody else's — `owned_by` is
    structural, the same guarantee `catalogue_only` gives the other way.
    """
    books = session.scalars(
        owned_by(select(Book), user.id).order_by(Book.created_at.desc())
    ).all()
    return {"ok": True, "count": len(books), "books": [_book_to_dict(b) for b in books]}
