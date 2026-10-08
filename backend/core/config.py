"""Runtime configuration, read once from the environment.

Before PR 2 nothing in this codebase was configurable: ports, hosts and
paths were literals, and there was no .env at all. Every setting that
differs between a laptop and a deployment belongs here.

Deliberately plain os.getenv rather than pydantic-settings — this is a flat
read of a dozen values, and a second settings framework would be weight
without benefit.
"""

from __future__ import annotations

import ipaddress
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


# --- Trusted proxy (F-55) ---------------------------------------------------
#
# `core.ratelimit.client_ip()` ignored `X-Forwarded-For` outright, which was
# correct with nothing in front of this app but wrong the moment a real
# reverse proxy sits in front of it (OI-3, undecided until now): every
# caller would then share the proxy's one address, and the per-IP limit
# would bound the whole site's traffic rather than each visitor's.
#
# Empty by default — on purpose, and the point of this setting. Honouring
# the header from *any* source would let an attacker reset their own limit
# by inventing one (the reason it was ignored outright before this existed).
# Trust is granted to specific CIDR ranges, never implied by the header's
# mere presence, and only the immediate TCP peer's address is checked
# against them — a spoofed `X-Forwarded-For` from outside the proxy cannot
# forge that peer address, which is the one thing this check relies on.
#
# Pairs with uvicorn's own `--proxy-headers --forwarded-allow-ips=<proxy
# IP>` (documented in the production .env.example): that flag is what makes
# `request.url.scheme` and redirect/cookie logic trust the proxy for HTTPS
# detection, a different concern from this one. The two should name the
# same proxy address, but neither depends on the other being set — this
# check reads the header directly rather than relying on uvicorn to have
# already rewritten `request.client.host`, so `client_ip()` behaves
# correctly (refuses to trust the header) even if an operator forgets the
# uvicorn flag, rather than silently inheriting whatever uvicorn decided.
_TRUSTED_PROXY_CIDR_RAW = os.getenv("TRUSTED_PROXY_CIDR", "").strip()


def _parse_trusted_proxy_cidrs(raw: str) -> tuple:
    """Comma-separated CIDR ranges -> parsed networks. Empty input is not an
    error — it is the secure default this whole section exists to keep.

    A malformed entry *is* an error, raised at import time rather than
    discovered the first time a request happens to arrive from a proxy: a
    typo here is a security setting silently not taking effect, which
    should fail loudly and immediately, not fail open.
    """
    networks = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError as exc:
            raise RuntimeError(
                f"TRUSTED_PROXY_CIDR entry {part!r} is not a valid CIDR range "
                f"(e.g. 10.0.0.5/32 or 10.0.0.0/24): {exc}"
            ) from exc
    return tuple(networks)


TRUSTED_PROXY_CIDR = _TRUSTED_PROXY_CIDR_RAW
TRUSTED_PROXY_NETWORKS = _parse_trusted_proxy_cidrs(_TRUSTED_PROXY_CIDR_RAW)


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

# Section 10/12, applied to the AI Librarian (F-22 Phase B): Claude when a
# key is configured, the local Ollama chain when it is not. Read here, not
# via a bare `os.getenv` inside `services/providers/llm.py`, so every
# credential this app holds is readable from one file (the same reason
# GOOGLE_BOOKS_API_KEY lives here rather than inside `providers/google_books.py`).
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()


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
        "anthropic_key_present": bool(ANTHROPIC_API_KEY),
        "trusted_proxy_cidr": TRUSTED_PROXY_CIDR or None,
        # OI-5: what would stop this configuration being deployable. Empty in
        # production by construction (the guard above refuses to boot
        # otherwise); on a laptop it is the standing to-do list, visible
        # rather than remembered.
        "production_blockers": production_problems(),
    }
