"""One-shot CLI passes — data preparation, enrichment and evaluation.

Not application code: nothing under backend/ imports these. They are run
as `python -m scripts.<name>` from backend/, which keeps backend/ as the
single import root so their `from db import ...` / `from models import
...` lines resolve unchanged.

The test suite does import three of them directly (test_book_vectors,
test_chunk_pass, test_embed_pass, test_front_matter), so conftest.py
puts this directory on sys.path as well — see RESTRUCTURE-NOTES B-7.
"""
