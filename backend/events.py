"""Compatibility shim — this module now lives at `core.events`.

Kept so the flat imports this codebase uses (`import events`,
`from events import ...`) keep resolving while call sites are migrated.
See RESTRUCTURE-NOTES.md.

This aliases the module *object* rather than re-exporting its names. A
`from core.events import *` would create a second namespace: private names would
be missing, and `monkeypatch.setattr(events, ...)` would patch a copy the
application never reads.
"""

import sys

from core import events as _real

sys.modules[__name__] = _real
