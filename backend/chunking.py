"""Compatibility shim — this module now lives at `ml.chunking`.

Kept so the flat imports this codebase uses (`from chunking import ...`) keep
resolving while call sites are migrated. See RESTRUCTURE-NOTES.md.

Aliases the module *object* rather than re-exporting names, for the
reasons in RESTRUCTURE-NOTES 5.1.
"""

import sys

from ml import chunking as _real

sys.modules[__name__] = _real
