"""Compatibility shim — this module now lives at `services.store`.

Kept so the flat imports this codebase uses (`import store`) keep
resolving while call sites are migrated. See RESTRUCTURE-NOTES.md.

This aliases the module *object* rather than re-exporting its names, for
the reasons in RESTRUCTURE-NOTES 5.1: main.py reaches every function as
`store.<name>`, and an alias keeps that reading the module the
application actually populates.
"""

import sys

from services import store as _real

sys.modules[__name__] = _real
