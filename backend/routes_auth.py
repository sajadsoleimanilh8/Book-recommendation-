"""Compatibility shim — this module now lives at `api.auth`.

Kept so the flat import this codebase uses (`from routes_auth import
router`) keeps resolving while call sites are migrated. See
RESTRUCTURE-NOTES.md.

Aliases the module *object* rather than re-exporting names, for the
reasons in RESTRUCTURE-NOTES 5.1.
"""

import sys

from api import auth as _real

sys.modules[__name__] = _real
