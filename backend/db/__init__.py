"""Database package — engine, session factory, declarative base, schema.

The session machinery itself lives in `core.db`; `db.models` holds the
SQLAlchemy schema. This module re-exports the former so that the flat
imports this codebase uses (`from db import SessionLocal | Base |
get_session | engine`) keep resolving unchanged.

Why a re-export here rather than the sys.modules alias used by the other
Phase B shims: `backend/db.py` and a `backend/db/` package cannot coexist —
Python resolves the package first and would silently shadow the module.
Since `db/` has to exist to hold `models.py`, the package __init__ *is* the
compatibility surface. A name re-export is safe here because nothing
patches attributes on the `db` module object; every call site either
imports these four names or reaches `db.models`. See RESTRUCTURE-NOTES 3.2.
"""

from core.db import Base, SessionLocal, engine, get_session

__all__ = ["Base", "SessionLocal", "engine", "get_session"]
