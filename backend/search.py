"""Compatibility shim — this module now lives at `services.search`.

Kept so the flat imports this codebase uses (`from search import
search_books, search_chunks`) keep resolving while call sites are
migrated. See RESTRUCTURE-NOTES.md.

Aliases the module *object* rather than re-exporting names, for the
reasons in RESTRUCTURE-NOTES 5.1.
"""

import sys

from services import search as _real

sys.modules[__name__] = _real
