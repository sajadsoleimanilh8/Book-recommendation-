"""Compatibility shim — this module now lives at `core.net_cache`.

Kept so the flat imports this codebase uses (`import net_cache`,
`from net_cache import ...`) keep resolving while call sites are migrated.
See RESTRUCTURE-NOTES.md.

This aliases the module *object* rather than re-exporting its names. A
`from core.net_cache import *` would create a second namespace: private names would
be missing, and `monkeypatch.setattr(net_cache, ...)` would patch a copy the
application never reads.
"""

import sys

from core import net_cache as _real

sys.modules[__name__] = _real
