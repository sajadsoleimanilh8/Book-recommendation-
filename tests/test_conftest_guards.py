"""conftest.py's own collection-time guards — F-60.

`_check_test_db_migration_head` runs once, in `pytest_configure`, before any
test is collected. Tested by calling it directly rather than through a real
pytest sub-invocation (slow, and would need to spawn a whole second
process) — the function itself is what needs proving, not that
`pytest_configure` calls it (a one-line, visually-inspectable fact).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from conftest import _check_test_db_migration_head, database_reachable  # noqa: E402

pytestmark = pytest.mark.skipif(
    not database_reachable(), reason="Postgres not reachable"
)


def test_passes_silently_when_the_test_db_is_at_head():
    """No exception, no output beyond a possible log line — this is the
    steady state every other test in the suite already relies on.
    """
    _check_test_db_migration_head()  # must not raise


def test_fails_loudly_on_a_mismatch(monkeypatch):
    """The actual regression F-60 describes: digikitab_test reachable, but
    on a different migration than the code expects.

    Mocks the *code's* reported head rather than touching the real
    database's `alembic_version` row — proves the same comparison without
    any risk of leaving the shared test database in a mismatched state if
    this test itself failed partway through.
    """
    from alembic.script import ScriptDirectory

    monkeypatch.setattr(
        ScriptDirectory, "get_current_head", lambda self: "0" * 12
    )
    with pytest.raises(pytest.UsageError) as exc_info:
        _check_test_db_migration_head()

    message = str(exc_info.value)
    assert "000000000000" in message
    assert "digikitab_test" in message
    assert "alembic upgrade head" in message


def test_unreachable_database_is_a_silent_no_op(monkeypatch):
    """A database that is not reachable at all is a different, already
    -handled degradation (REQUIRE_DATABASE / per-file skips) — this guard
    must not raise its own, unrelated error on top of that one.
    """
    import conftest

    monkeypatch.setattr(conftest, "database_reachable", lambda: False)
    _check_test_db_migration_head()  # must not raise
