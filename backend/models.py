"""Compatibility shim — the SQLAlchemy schema now lives at `db.models`.

Kept so the flat imports this codebase uses (`import models`,
`from models import Book`) keep resolving while call sites are migrated.
`alembic/env.py` relies on `import models` for its side effect of
registering every table on the metadata, so this must alias the real
module object rather than copy names out of it.

See RESTRUCTURE-NOTES.md.
"""

import sys

from db import models as _real

sys.modules[__name__] = _real
