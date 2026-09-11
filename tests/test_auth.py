"""Auth and authorization tests — F-07.

Requires a live Postgres (docker compose up -d). Skipped, not failed, when
one is unreachable, so the rest of the suite still runs on a bare checkout.

The centrepiece is test_bob_cannot_delete_alices_comment. Before PR 2 that
operation returned 200 to an anonymous caller.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

pytest.importorskip("sqlalchemy", reason="persistence stack not installed")
pytest.importorskip("jwt", reason="PyJWT not installed")
pytest.importorskip("argon2", reason="argon2-cffi not installed")
pytest.importorskip("sklearn", reason="full ML stack not installed")


def _database_reachable() -> bool:
    try:
        from sqlalchemy import text

        from db import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_reachable(),
    reason="Postgres unreachable — run `docker compose up -d`",
)


@pytest.fixture(scope="module")
def client():
    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        yield c


def _unique_email(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture
def alice(client):
    email = _unique_email("alice")
    r = client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct-horse-battery", "display_name": "Alice"},
    )
    assert r.status_code == 201, r.text
    return {"email": email, "token": r.json()["access_token"], "id": r.json()["user"]["id"]}


@pytest.fixture
def bob(client):
    email = _unique_email("bob")
    r = client.post(
        "/api/auth/register",
        json={"email": email, "password": "another-long-password"},
    )
    assert r.status_code == 201, r.text
    return {"email": email, "token": r.json()["access_token"], "id": r.json()["user"]["id"]}


def auth_header(user) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


# --------------------------------------------------------------------------
# Registration and login
# --------------------------------------------------------------------------

def test_register_returns_token_and_user(alice):
    assert alice["token"]
    assert alice["id"] > 0


def test_email_is_normalised(client):
    email = _unique_email("Mixed")
    r = client.post(
        "/api/auth/register", json={"email": email.upper(), "password": "long-enough-password"}
    )
    assert r.status_code == 201
    assert r.json()["user"]["email"] == email.lower()
    # ...and login works with any casing.
    r2 = client.post(
        "/api/auth/login", json={"email": email.upper(), "password": "long-enough-password"}
    )
    assert r2.status_code == 200


def test_duplicate_registration_rejected(client, alice):
    r = client.post(
        "/api/auth/register",
        json={"email": alice["email"], "password": "correct-horse-battery"},
    )
    assert r.status_code == 400


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"email": "notanemail", "password": "long-enough-password"}, 422),
        ({"email": "x@example.com", "password": "short"}, 422),
        ({"email": "x@example.com"}, 422),
    ],
)
def test_registration_validation(client, payload, expected):
    assert client.post("/api/auth/register", json=payload).status_code == expected


def test_wrong_password_and_unknown_user_are_indistinguishable(client, alice):
    """Both must return 401 with the same body, or the response enumerates
    which emails are registered."""
    wrong = client.post(
        "/api/auth/login", json={"email": alice["email"], "password": "not-the-password"}
    )
    unknown = client.post(
        "/api/auth/login",
        json={"email": _unique_email("ghost"), "password": "not-the-password"},
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------

def test_me_requires_a_token(client):
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("token", ["not.a.token", "", "Bearer", "a.b.c"])
def test_malformed_tokens_rejected(client, token):
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_token_signed_with_another_secret_rejected(client, alice):
    """A token this server did not sign must not be accepted."""
    import jwt

    forged = jwt.encode({"sub": str(alice["id"])}, "not-the-real-secret", algorithm="HS256")
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


def test_expired_token_rejected(client, alice):
    import jwt
    from datetime import datetime, timedelta, timezone

    import config

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    expired = jwt.encode(
        {"sub": str(alice["id"]), "iat": past, "exp": past},
        config.JWT_SECRET,
        algorithm=config.JWT_ALGORITHM,
    )
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


# --------------------------------------------------------------------------
# Password storage
# --------------------------------------------------------------------------

def test_passwords_are_argon2id_and_never_plaintext(client, alice):
    from sqlalchemy import select

    from db import SessionLocal
    from models import User

    with SessionLocal() as s:
        user = s.scalar(select(User).where(User.email == alice["email"]))
        assert user is not None
        assert user.password_hash.startswith("$argon2id$")
        assert "correct-horse-battery" not in user.password_hash


# --------------------------------------------------------------------------
# F-07 — the hole this PR exists to close
# --------------------------------------------------------------------------

def _post_comment(client, user, text="a comment"):
    return client.post(
        "/api/comments",
        headers=auth_header(user),
        json={"book_id": 1, "comment": text, "rating": 5},
    )


def test_posting_a_comment_requires_auth(client):
    r = client.post("/api/comments", json={"book_id": 1, "comment": "anonymous"})
    assert r.status_code == 401, "anonymous comment posting was accepted"


def test_comment_author_is_the_authenticated_user(client, alice):
    r = _post_comment(client, alice)
    assert r.status_code == 200, r.text
    assert r.json()["comment"]["user_id"] == str(alice["id"])


def test_client_cannot_spoof_the_author(client, bob):
    """user_id must fail closed, not be silently ignored — the same reasoning
    as output_file in F-04."""
    r = client.post(
        "/api/comments",
        headers=auth_header(bob),
        json={"user_id": "1", "book_id": 1, "comment": "spoofed"},
    )
    assert r.status_code == 422


def test_deleting_a_comment_requires_auth(client, alice):
    cid = _post_comment(client, alice).json()["comment"]["id"]
    r = client.delete(f"/api/comments/{cid}")
    assert r.status_code == 401, "anonymous delete was accepted — F-07 is open"


def test_bob_cannot_delete_alices_comment(client, alice, bob):
    """The exact F-07 exploit. This returned 200 to an anonymous caller
    before PR 2, and is now id-addressed rather than index-addressed."""
    posted = _post_comment(client, alice, "Alice's comment")
    assert posted.status_code == 200
    cid = posted.json()["comment"]["id"]
    before = len(client.get("/api/comments/1").json()["comments"])

    r = client.delete(f"/api/comments/{cid}", headers=auth_header(bob))
    assert r.status_code == 403, "Bob deleted a comment he does not own"

    after = len(client.get("/api/comments/1").json()["comments"])
    assert after == before, "the comment was removed despite the 403"


def test_alice_can_delete_her_own_comment(client, alice):
    cid = _post_comment(client, alice, "delete me").json()["comment"]["id"]
    r = client.delete(f"/api/comments/{cid}", headers=auth_header(alice))
    assert r.status_code == 200, r.text
    remaining = [c["id"] for c in client.get("/api/comments/1").json()["comments"]]
    assert cid not in remaining, "delete returned 200 but the row survived"


def test_unknown_comment_id_is_404_not_500(client, alice):
    r = client.delete("/api/comments/99999999", headers=auth_header(alice))
    assert r.status_code == 404


def test_comment_survives_in_postgres(client, alice):
    """F-12: the comment is a durable row, not a dict entry."""
    from sqlalchemy import select

    from db import SessionLocal
    from models import Comment

    cid = _post_comment(client, alice, "persisted please").json()["comment"]["id"]
    with SessionLocal() as s:
        row = s.scalar(select(Comment).where(Comment.id == cid))
    assert row is not None, "comment was not written to Postgres"
    assert row.user_id == alice["id"]
    assert row.text == "persisted please"


# --------------------------------------------------------------------------
# F-07 read-authz sweep (PR 3)
# --------------------------------------------------------------------------

def test_cannot_read_another_users_profile(client, alice, bob):
    """This exposed any user's taste profile, mood and viewing history to any
    anonymous caller who could guess an id — the default being "guest"."""
    r = client.get(f"/api/profile/{alice['id']}", headers=auth_header(bob))
    assert r.status_code == 403


def test_can_read_own_profile(client, alice):
    r = client.get(f"/api/profile/{alice['id']}", headers=auth_header(alice))
    assert r.status_code == 200
    assert r.json()["user_id"] == str(alice["id"])


def test_progress_is_scoped_to_the_caller(client, alice, bob):
    assert client.post(
        "/api/progress", headers=auth_header(alice),
        json={"book_id": 1, "progress": 0.5},
    ).status_code == 200

    mine = client.get("/api/progress", headers=auth_header(alice)).json()
    theirs = client.get("/api/progress", headers=auth_header(bob)).json()
    assert len(mine["items"]) == 1
    assert theirs["items"] == [], "Bob can see Alice's reading progress"


def test_reminders_are_scoped_to_the_caller(client, alice, bob):
    assert client.post(
        "/api/reminder", headers=auth_header(alice),
        json={"book_id": 1, "enabled": True},
    ).status_code == 200

    mine = client.get("/api/reminders", headers=auth_header(alice)).json()
    theirs = client.get("/api/reminders", headers=auth_header(bob)).json()
    assert len(mine["reminders"]) == 1
    assert theirs["reminders"] == [], "Bob can see Alice's reminders"
