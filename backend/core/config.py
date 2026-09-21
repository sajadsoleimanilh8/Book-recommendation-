"""Runtime configuration, read once from the environment.

Before PR 2 nothing in this codebase was configurable: ports, hosts and
paths were literals, and there was no .env at all. Every setting that
differs between a laptop and a deployment belongs here.

Deliberately plain os.getenv rather than pydantic-settings — this is a flat
read of a dozen values, and a second settings framework would be weight
without benefit.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from urllib.parse import quote

# parents[1], not parent: this module moved from backend/config.py into
# backend/core/, and BACKEND_DIR must keep resolving to backend/ itself.
# CATALOGUE_PATH and AUDIO_DIR below are derived from it, so an unadjusted
# `parent` would point both at backend/core/ — with no import error and
# nothing in the suite that would notice.
BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:  # pragma: no cover
    pass


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# --- Environment ----------------------------------------------------------

ENV = os.getenv("ENV", "development").strip().lower()
IS_PRODUCTION = ENV == "production"
DEBUG = _bool("DEBUG", not IS_PRODUCTION)


# --- Database -------------------------------------------------------------

POSTGRES_USER = os.getenv("POSTGRES_USER", "digikitab")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "digikitab_dev")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
# 5433, not 5432 — this project shares a machine with other work (F-24) and
# must not assume it owns the default port.
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5433")
POSTGRES_DB = os.getenv("POSTGRES_DB", "digikitab")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql+psycopg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}",
)

DB_ECHO = _bool("DB_ECHO", False)
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))


# --- Redis ----------------------------------------------------------------

# OI-5: Redis now requires a password (see docker-compose.yml for why —
# short version: it had none at all, on a published port). The URL is built
# from it so there is one place to change, and quoted because a password is
# not guaranteed to be URL-safe.
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "digikitab_dev")
_redis_auth = f":{quote(REDIS_PASSWORD, safe='')}@" if REDIS_PASSWORD else ""
REDIS_URL = os.getenv(
    "REDIS_URL", f"redis://{_redis_auth}127.0.0.1:6380/0"
)


# --- Auth -----------------------------------------------------------------

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", str(60 * 24 * 7)))

_secret = os.getenv("JWT_SECRET", "").strip()
if not _secret:
    if IS_PRODUCTION:
        # Never generate one in production: every worker would mint a
        # different key, so tokens would fail across workers and every
        # restart would log the whole userbase out.
        raise RuntimeError(
            "JWT_SECRET must be set when ENV=production. "
            "Generate one with: python -c \"import secrets;print(secrets.token_urlsafe(48))\""
        )

    # Development: generate once and persist to a gitignored file.
    #
    # Generating per-process meant every restart invalidated every token —
    # which cost real time during PR 3's restart testing before the cause
    # was obvious, and would do the same to anyone running `--reload`.
    # A file keeps sessions alive across restarts without putting a secret
    # in the repo. Production still requires an explicit value.
    _secret_file = PROJECT_ROOT / ".dev-jwt-secret"
    try:
        if _secret_file.exists():
            _secret = _secret_file.read_text(encoding="utf-8").strip()
        if not _secret:
            _secret = secrets.token_urlsafe(48)
            _secret_file.write_text(_secret, encoding="utf-8")
    except OSError:
        # Read-only filesystem or similar — fall back to per-process.
        _secret = _secret or secrets.token_urlsafe(48)

JWT_SECRET = _secret


# --- Catalogue ------------------------------------------------------------

CATALOGUE_PATH = Path(
    os.getenv("CATALOGUE_PATH", str(BACKEND_DIR / "site_ready_books.json"))
)
AUDIO_DIR = Path(os.getenv("AUDIO_DIR", str(BACKEND_DIR / "audio_outputs")))
BOOK_LOAD_LIMIT = int(os.getenv("BOOK_LOAD_LIMIT", "0")) or None

# OI-2: English-only for now. Records in other languages stay in the
# catalogue and remain enrichable — they are filtered at query time, never
# deleted. Empty means "no filter".
CATALOGUE_LANGUAGES = [
    s.strip() for s in os.getenv("CATALOGUE_LANGUAGES", "en").split(",") if s.strip()
]


# --- Providers (Phase 2) --------------------------------------------------

GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY", "").strip()


# --- What production refuses to start with --------------------------------
#
# OI-5. `ENV` defaults to "development", which is right for a laptop and
# means every production guard in this file is inert until someone sets it.
# That is the intended shape — the guards exist so that *declaring*
# production is enough to be told what is still wrong, rather than finding
# out later. The JWT_SECRET check above is the oldest of them; these are the
# credentials that ship with a default in this repo, and a default credential
# is a published one.
DEV_DEFAULT_PASSWORDS = {"digikitab_dev"}


def production_problems() -> list[str]:
    """Settings that are fine on a laptop and unacceptable on a network.

    Returned rather than raised so `/health` can report them and so a caller
    can see the whole list at once instead of one per restart.
    """
    problems: list[str] = []
    if POSTGRES_PASSWORD in DEV_DEFAULT_PASSWORDS:
        problems.append("POSTGRES_PASSWORD is the development default")
    if REDIS_PASSWORD in DEV_DEFAULT_PASSWORDS:
        problems.append("REDIS_PASSWORD is the development default")
    if not REDIS_PASSWORD:
        problems.append("REDIS_PASSWORD is empty — Redis would be unauthenticated")
    if DEBUG:
        problems.append("DEBUG is on")
    return problems


if IS_PRODUCTION:
    _problems = production_problems()
    if _problems:
        raise RuntimeError(
            "ENV=production, but: " + "; ".join(_problems) + ". "
            "Set these in the environment before starting."
        )


def summary() -> dict:
    """Non-secret config, safe to log or expose on /health."""
    return {
        "env": ENV,
        "debug": DEBUG,
        "database": f"{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}",
        "redis": REDIS_URL.rsplit("@", 1)[-1],
        "catalogue_languages": CATALOGUE_LANGUAGES,
        "google_books_key_present": bool(GOOGLE_BOOKS_API_KEY),
        # OI-5: what would stop this configuration being deployable. Empty in
        # production by construction (the guard above refuses to boot
        # otherwise); on a laptop it is the standing to-do list, visible
        # rather than remembered.
        "production_blockers": production_problems(),
    }
