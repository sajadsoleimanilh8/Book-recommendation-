"""Shared fixtures.

Fitting the ML engine takes ~6s. Before this, each test module booted its own
TestClient and paid that cost again. The app fixture is session-scoped so it
is paid once.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

# main.py uses flat imports (`from engine import ...`).
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


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
