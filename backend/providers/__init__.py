"""Compatibility shim — this package now lives at `services.providers`.

Kept so the flat imports this codebase uses (`from providers import
GoogleBooksProvider`, `from providers.base import RateLimiter`) keep
resolving while call sites are migrated. See RESTRUCTURE-NOTES.md.

Every submodule is aliased into sys.modules, not just the package. The
suite patches by dotted string —

    monkeypatch.setattr("providers.base.time.sleep", ...)

(tests/test_providers.py:292, 318, 334) — which resolves `providers.base`
through sys.modules. Without the submodule aliases below that would be a
*different* module object from `services.providers.base`, so the patch
would apply to a module nothing imports and the retry tests would sleep
for real. Aliasing here works because Python re-checks sys.modules for a
submodule after importing its parent package.

Submodules are read out of sys.modules rather than by attribute, because
`services/providers/__init__.py` binds the name `reconcile` to the
function it re-exports, shadowing the module of the same name.
"""

import sys

import services.providers.base  # noqa: F401
import services.providers.google_books  # noqa: F401
import services.providers.gutenberg_text  # noqa: F401
import services.providers.open_library  # noqa: F401
import services.providers.reconcile  # noqa: F401
from services import providers as _real

for _sub in ("base", "google_books", "gutenberg_text", "open_library", "reconcile"):
    sys.modules[f"{__name__}.{_sub}"] = sys.modules[f"services.providers.{_sub}"]

sys.modules[__name__] = _real
