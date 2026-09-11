"""Shared fixtures.

Fitting the ML engine takes ~6s. Before this, each test module booted its own
TestClient and paid that cost again. The app fixture is session-scoped so it
is paid once.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

# main.py uses flat imports (`from engine import ...`).
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# The one-shot CLI passes moved to backend/scripts/ in the Phase B
# restructure. The application never imports them, but four test modules do
# — test_book_vectors, test_chunk_pass, test_embed_pass and test_front_matter
# all `import chunk_pass` / `import embed_pass` flat. Putting the directory
# on the path keeps those imports working without editing the tests, which
# is the only reason this entry exists. See RESTRUCTURE-NOTES B-7.
SCRIPTS = BACKEND / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# --------------------------------------------------------------------------
# Test database isolation
# --------------------------------------------------------------------------
# MUST run before anything imports `config`, which reads these at import time.
# conftest is loaded before any test module, so this is the right place.
#
# Without this, tests ran against the development database. State accumulated
# across runs, comments were rehydrated at startup, and comment_score for
# book 0 drifted to 0.9 and started clamping — which made the feedback-delta
# assertions fail for reasons that had nothing to do with the code. Tests
# that depend on how many times they have been run before are worthless.
#
# Set DIGIKITAB_TEST_DB to point somewhere else if needed.
TEST_DB = os.environ.setdefault("DIGIKITAB_TEST_DB", "digikitab_test")
os.environ["POSTGRES_DB"] = TEST_DB
os.environ.pop("DATABASE_URL", None)  # force it to be rebuilt from the parts


def ml_stack_available() -> bool:
    try:
        import gtts  # noqa: F401
        import sklearn  # noqa: F401

        return True
    except ImportError:
        return False


def database_reachable() -> bool:
    try:
        from sqlalchemy import text

        from db import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _ensure_test_database() -> bool:
    """Create, migrate and seed the test database. Idempotent.

    First run pays the catalogue ingest (~20s). Later runs skip it, because
    the books are already there and immutable.
    """
    import config
    from sqlalchemy import create_engine, text

    admin_url = config.DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            exists = conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB}
            )
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
        admin.dispose()
    except Exception:
        return False

    env = {**os.environ, "POSTGRES_DB": TEST_DB}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND, env=env, capture_output=True, check=False,
    )

    from db import engine as test_engine

    with test_engine.connect() as conn:
        book_count = conn.scalar(text("SELECT count(*) FROM books")) or 0
    if book_count == 0:
        subprocess.run(
            [sys.executable, "-m", "ingest"],
            cwd=BACKEND, env=env, capture_output=True, check=False,
        )
    return True


@pytest.fixture(scope="session", autouse=True)
def clean_user_state():
    """Start every session from an empty user state.

    Books are left alone — they are immutable reference data and re-ingesting
    costs 20s. Only the tables tests write to are cleared.
    """
    if not database_reachable() and not _ensure_test_database():
        yield
        return
    try:
        from sqlalchemy import text

        from db import engine

        with engine.begin() as conn:
            conn.execute(
                text("TRUNCATE users, comments, reading_progress, reminders, "
                     "user_books, interaction_events, recommendation_log CASCADE")
            )
    except Exception:
        pass
    yield


@pytest.fixture(scope="session")
def fitted_app():
    """The real app with the real catalogue, fitted once per session."""
    if not ml_stack_available():
        pytest.skip("full ML stack not installed")
    logging.disable(logging.INFO)
    import main

    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        yield main, client


@pytest.fixture(scope="session")
def recommender(fitted_app):
    main, _ = fitted_app
    if main.RECOMMENDER is None or not getattr(main.RECOMMENDER, "_fitted", False):
        pytest.fail("ML engine did not fit — golden baselines cannot be trusted")
    return main.RECOMMENDER


@pytest.fixture(scope="session")
def questioner(fitted_app):
    main, _ = fitted_app
    if main.QUESTIONER is None:
        pytest.fail("QuestionerEngine did not initialise")
    return main.QUESTIONER


# --------------------------------------------------------------------------
# F-43 — a green run that tested nothing is worse than a red one.
#
# Every database-dependent test is guarded by `database_reachable()`, which
# swallows its exception and returns False. When Docker Desktop stops — which
# on this machine has now happened four times — the whole suite skips those
# tests and **exits 0**. Observed live: `10 skipped in 131s`, exit code 0,
# and nothing in the output said the database was the reason.
#
# That is the same shape as the watcher that could not see a crash: silence
# reading as success. The suite must say so, and in CI it must fail.
# --------------------------------------------------------------------------

REQUIRE_DATABASE = os.getenv("REQUIRE_DATABASE", "").strip().lower() in {"1", "true", "yes"}


def pytest_configure(config):
    """Fail fast when the database is required but absent.

    Failing at configure time rather than per-test means one clear message
    instead of N skips, and it happens before the expensive ML fixture runs.
    """
    if REQUIRE_DATABASE and not database_reachable():
        raise pytest.UsageError(
            "REQUIRE_DATABASE=1 but Postgres is not reachable. "
            "The database-dependent tests would silently skip and the run "
            "would exit 0, reporting success for tests that never ran. "
            "Start it with: docker compose up -d"
        )


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Say plainly when the database was the reason, and how many were lost."""
    skipped = terminalreporter.stats.get("skipped", [])
    if not skipped:
        return
    db_skips = [
        r for r in skipped
        if any(
            phrase in str(getattr(r, "longrepr", ""))
            for phrase in ("Postgres not reachable", "database")
        )
    ]
    if not db_skips:
        return

    terminalreporter.write_sep("=", "DATABASE UNREACHABLE", red=True, bold=True)
    terminalreporter.write_line(
        f"{len(db_skips)} test(s) skipped because Postgres was not reachable — "
        "they did not run and prove nothing."
    )
    terminalreporter.write_line(
        "  Start it with:  docker compose up -d"
    )
    terminalreporter.write_line(
        "  Set REQUIRE_DATABASE=1 to make this a failure instead of a skip."
    )
