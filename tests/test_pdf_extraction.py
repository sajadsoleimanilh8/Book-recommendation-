"""PDF extraction — section 29's deferred third format.

EPUB and TXT shipped in the Phase 4 upload slice; PDF was listed by the spec
and deliberately held back, because queuing PDFs with no extractor would have
left files sitting at `uploaded` forever behind a pipeline that could not read
them. This is that slice.

The interesting case is not a PDF that works, it is a PDF that *looks* like it
works. A scan is images of words: valid pages, valid structure, zero
characters. Without a guard it extracts ~nothing, chunks into nothing, embeds
nothing, and is still marked `ready` — a book in someone's library that
silently cannot answer one question about itself. That is the F-66 shape, a
path reporting success because the failure has no voice, so it gets its own
exception (`NoTextLayer`) and its own message.

The PDFs here are built by hand rather than with a writer library: a test
dependency to produce fixtures is a poor trade when the format's minimum is
a catalogue, a page tree, a content stream and an xref table.
"""

from __future__ import annotations

import sys
import uuid
from io import BytesIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from conftest import database_reachable

    HAVE_DB = database_reachable()
except Exception:  # pragma: no cover
    HAVE_DB = False

needs_db = pytest.mark.skipif(not HAVE_DB, reason="database unavailable")

PROSE = "The lighthouse keeper watched the grey sea. "


# -- fixture builder ------------------------------------------------------


def build_pdf(pages, *, with_text=True):
    """A minimal valid PDF, one entry in `pages` per page.

    `with_text=False` emits the same page geometry with a filled rectangle
    and no text operators — ink on the page, no characters, which is what a
    scanned book is to any reader that is not an OCR engine.
    """
    objects = []
    kids = []
    font_num = 3 + len(pages) * 2

    for i, text in enumerate(pages):
        page_num = 3 + i * 2
        content_num = page_num + 1
        kids.append(f"{page_num} 0 R")

        if with_text:
            escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            stream = (b"BT /F1 12 Tf 72 720 Td ("
                      + escaped.encode("latin-1", "replace") + b") Tj ET\n")
        else:
            stream = b"0.5 0.5 0.5 rg 72 600 400 120 re f\n"

        objects.append((
            page_num,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents "
            + str(content_num).encode() + b" 0 R /Resources << /Font << /F1 "
            + str(font_num).encode() + b" 0 R >> >> >>",
        ))
        objects.append((
            content_num,
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
            + stream + b"endstream",
        ))

    objects.insert(0, (1, b"<< /Type /Catalog /Pages 2 0 R >>"))
    objects.insert(1, (2, b"<< /Type /Pages /Kids [" + " ".join(kids).encode()
                       + b"] /Count " + str(len(pages)).encode() + b" >>"))
    objects.append((font_num,
                    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    objects.sort(key=lambda o: o[0])

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num, body in objects:
        offsets[num] = len(out)
        out += str(num).encode() + b" 0 obj\n" + body + b"\nendobj\n"

    xref_at = len(out)
    highest = max(offsets)
    out += b"xref\n0 " + str(highest + 1).encode() + b"\n0000000000 65535 f \n"
    for num in range(1, highest + 1):
        out += (f"{offsets[num]:010d} 00000 n \n".encode() if num in offsets
                else b"0000000000 65535 f \n")
    out += (b"trailer\n<< /Size " + str(highest + 1).encode()
            + b" /Root 1 0 R >>\nstartxref\n" + str(xref_at).encode() + b"\n%%EOF\n")
    return bytes(out)


def test_the_fixture_builder_makes_a_readable_pdf():
    """If the builder is wrong, every test below passes or fails for the
    wrong reason."""
    from pypdf import PdfReader

    pdf = build_pdf([PROSE * 10, PROSE * 10])
    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) == 2
    assert "lighthouse keeper" in (reader.pages[0].extract_text() or "")


# -- extraction -----------------------------------------------------------


def test_text_is_extracted_from_every_page_in_order():
    from services.extraction import extract_pdf

    pdf = build_pdf([f"Page one. {PROSE * 20}",
                     f"Page two. {PROSE * 20}",
                     f"Page three. {PROSE * 20}"])
    text = extract_pdf(pdf)

    assert text.index("Page one") < text.index("Page two") < text.index("Page three")
    assert len(text) > 2000


def test_pages_are_joined_the_same_way_epub_spine_items_are():
    """The chunker sees one shape regardless of the format a book arrived
    in, so the separator is shared rather than chosen per extractor."""
    from services.extraction import PAGE_SEPARATOR, extract_pdf

    text = extract_pdf(build_pdf([PROSE * 20, PROSE * 20]))
    assert PAGE_SEPARATOR in text


def test_a_scan_is_refused_rather_than_stored_as_a_husk():
    """**The case this slice exists to get right.** Valid PDF, valid pages,
    no text layer. Storing the result would mark a book `ready` that cannot
    answer anything."""
    from services.extraction import NoTextLayer, extract_pdf

    scan = build_pdf([""] * 12, with_text=False)
    with pytest.raises(NoTextLayer) as caught:
        extract_pdf(scan)

    message = str(caught.value)
    # The message has to tell the reader what to do, not just what failed.
    assert "scan" in message.lower()
    assert "OCR" in message
    assert "12 pages" in message


def test_the_scan_guard_is_an_average_not_a_per_page_rule():
    """Real books have blank versos and photographic plates. A document that
    is mostly text must not be refused because some pages carry none."""
    from services.extraction import extract_pdf

    pages = [PROSE * 40] + [""] * 6      # one dense page, six empty
    text = extract_pdf(build_pdf(pages))
    assert len(text) > 1000


def test_a_mostly_empty_document_is_still_refused():
    """The other side of the average: a few stray characters across many
    pages is a scan with noise, not a book."""
    from services.extraction import NoTextLayer, extract_pdf

    pages = ["x"] * 30                   # 30 pages, one character each
    with pytest.raises(NoTextLayer):
        extract_pdf(build_pdf(pages))


def test_a_corrupt_pdf_fails_with_a_readable_reason():
    from services.extraction import extract_pdf

    with pytest.raises(ValueError) as caught:
        extract_pdf(b"%PDF-1.4\nthis is not a pdf body at all\n")
    assert "PDF" in str(caught.value)


def test_a_password_protected_pdf_says_so():
    """Not a failure to report as a bug — a file the reader can fix."""
    from pypdf import PdfWriter

    from services.extraction import extract_pdf

    writer = PdfWriter(clone_from=BytesIO(build_pdf([PROSE * 20])))
    writer.encrypt("a-real-password")
    buf = BytesIO()
    writer.write(buf)

    with pytest.raises(ValueError) as caught:
        extract_pdf(buf.getvalue())
    assert "password" in str(caught.value).lower()


def test_an_empty_password_pdf_is_read_rather_than_refused():
    """Plenty of PDFs are "encrypted" with an empty user password purely to
    set permission flags. Refusing those would turn a readable book away."""
    from pypdf import PdfWriter

    from services.extraction import extract_pdf

    writer = PdfWriter(clone_from=BytesIO(build_pdf([PROSE * 20, PROSE * 20])))
    writer.encrypt("", owner_password="owner-only")
    buf = BytesIO()
    writer.write(buf)

    assert "lighthouse" in extract_pdf(buf.getvalue())


def test_dispatch_routes_pdf():
    from services.extraction import extract_text

    assert "lighthouse" in extract_text("pdf", build_pdf([PROSE * 20]))


# -- the upload gate ------------------------------------------------------


def test_pdf_is_no_longer_deferred():
    import main  # noqa: F401  (import order: main must load first)
    from api import library

    assert "pdf" in library.SUPPORTED_FORMATS
    assert not library.DEFERRED_FORMATS, (
        "DEFERRED_FORMATS is empty now; if a format is deferred again, the "
        "upload route's message needs to name it"
    )


def test_content_validation_accepts_a_pdf_and_rejects_a_renamed_file():
    import main  # noqa: F401
    from api import library

    assert library._validate_content("pdf", build_pdf([PROSE * 20])) is None

    # Extension-first, content-checked — the convention the EPUB and TXT
    # branches already follow.
    bad = library._validate_content("pdf", b"PK\x03\x04 this is a zip")
    assert bad and "PDF" in bad


def test_a_header_offset_by_junk_is_still_accepted():
    """Some producers emit bytes before the header and every reader
    tolerates it, so the marker is looked for in the opening bytes rather
    than only at offset 0."""
    import main  # noqa: F401
    from api import library

    pdf = build_pdf([PROSE * 20])
    assert library._validate_content("pdf", b"\n\n" + pdf) is None


# -- end to end -----------------------------------------------------------


@needs_db
def test_a_pdf_upload_goes_all_the_way_to_ready():
    """Upload, extract, chunk, embed, and then actually answer a question
    about it — the §29 pipeline for the format that was missing from it."""
    import time

    from fastapi.testclient import TestClient

    import main

    pages = [f"Chapter {i}. {PROSE * 30}" for i in range(1, 4)]
    pdf = build_pdf(pages)

    with TestClient(main.app) as client:
        email = f"pdf_{uuid.uuid4().hex[:8]}@example.com"
        token = client.post(
            "/api/auth/register",
            json={"email": email, "password": "Str0ng!Passw0rd1"},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        res = client.post(
            "/api/library/upload",
            headers=headers,
            files={"file": ("The Keeper.pdf", pdf, "application/pdf")},
            data={"attests_ownership": "true"},
        )
        if res.status_code == 429:
            pytest.skip("upload rate limit reached")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["file_format"] == "pdf"

        status = {}
        for _ in range(40):
            time.sleep(2)
            status = client.get(f"/api/library/jobs/{body['job_id']}").json()
            if status.get("status") in ("done", "succeeded", "failed", "error"):
                break
        assert status.get("status") in ("done", "succeeded"), status
        result = status.get("result") or {}
        assert result.get("chunks", 0) > 0
        assert result.get("upload_status") == "ready"

        book_id = body["book_id"]
        asked = client.post(
            f"/api/books/{book_id}/ask",
            headers=headers,
            json={"question": "What does the keeper watch?"},
        )
        assert asked.status_code == 200, asked.text
        assert asked.json()["citations"], "a PDF-sourced book cited nothing"


@needs_db
def test_a_scanned_pdf_upload_fails_loudly_instead_of_going_ready():
    """The end-to-end half of the scan guard: the row must not end up
    `ready`, and the job must say why."""
    import time

    from fastapi.testclient import TestClient

    import main

    scan = build_pdf([""] * 10, with_text=False)

    with TestClient(main.app) as client:
        email = f"scan_{uuid.uuid4().hex[:8]}@example.com"
        token = client.post(
            "/api/auth/register",
            json={"email": email, "password": "Str0ng!Passw0rd1"},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        res = client.post(
            "/api/library/upload",
            headers=headers,
            files={"file": ("A Scan.pdf", scan, "application/pdf")},
            data={"attests_ownership": "true"},
        )
        if res.status_code == 429:
            pytest.skip("upload rate limit reached")
        assert res.status_code == 200, res.text
        body = res.json()

        status = {}
        for _ in range(30):
            time.sleep(2)
            status = client.get(f"/api/library/jobs/{body['job_id']}").json()
            if status.get("status") in ("done", "succeeded", "failed", "error"):
                break

        # The job fails, and the reason reaches the client.
        assert status.get("status") in ("failed", "error"), status
        assert "scan" in str(status.get("error", "")).lower()

        # And the book is not pretending to be usable.
        listed = client.get("/api/library", headers=headers).json()["books"]
        row = next(b for b in listed if b["book_id"] == body["book_id"])
        assert row["upload_status"] != "ready", row
