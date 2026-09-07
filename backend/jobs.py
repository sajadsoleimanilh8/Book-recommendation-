"""Compatibility shim — this module now lives at `core.jobs`.

Kept so the flat imports this codebase uses (`import jobs`,
`from jobs import ...`) keep resolving while call sites are migrated.
See RESTRUCTURE-NOTES.md.

This aliases the module *object* rather than re-exporting its names. A
`from core.jobs import *` would create a second namespace: private names would
be missing, and `monkeypatch.setattr(jobs, ...)` would patch a copy the
application never reads.
"""

import sys

from core import jobs as _real

sys.modules[__name__] = _real
