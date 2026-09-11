"""Authentication — closes F-07.

Before this, there was no auth of any kind. `user_id` was a client-supplied
string defaulting to "guest", so anyone could read any profile and delete any
comment by index. The Phase 0 audit's repo-wide scan for auth primitives
matched only book titles.

Design notes:

- **argon2id**, not bcrypt. bcrypt silently truncates at 72 bytes, which is a
  real footgun with long passphrases, and argon2id is the current password
  hashing recommendation. `argon2-cffi` handles salting and encodes
  parameters in the hash, so rehashing on parameter change is detectable.

- **JWT over server-side sessions**, for now. The app has no session store
  yet and Redis is present but not load-bearing; stateless tokens avoid
  adding a dependency the rest of the system does not need. The tradeoff is
  that logout cannot revoke a token before expiry — acceptable at this stage
  and noted in PROGRESS.md, revisit when Redis becomes load-bearing.

- **`get_current_user_optional`** exists because most read endpoints
  legitimately serve anonymous visitors, and anonymous browsing is still
  signal worth logging (interaction_events.user_id is nullable).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from db import get_session
from models import User

_hasher = PasswordHasher()

# auto_error=False so a missing header reaches our own handlers, which can
# then distinguish "anonymous is fine here" from "this endpoint requires a
# user" instead of FastAPI raising a blanket 403.
_bearer = HTTPBearer(auto_error=False)

MIN_PASSWORD_LENGTH = 8


# --- Passwords ------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return False


# --- Tokens ---------------------------------------------------------------


def create_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=config.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


# Machine-readable reasons, so a frontend can act on them rather than parsing
# prose. `token_expired` is the one that matters: it is the only failure a
# client can recover from on its own, by refreshing.
TOKEN_EXPIRED = "token_expired"
TOKEN_INVALID = "token_invalid"
ACCOUNT_INACTIVE = "account_inactive"


def decode_token_detailed(token: str) -> tuple[int | None, str | None]:
    """Return (user_id, failure_reason). Exactly one is non-None.

    The earlier version collapsed every failure into None on the grounds that
    an attacker learns nothing from "expired" versus "malformed". That
    argument is real but thin — anyone holding a token already knows whether
    they forged it — and it was paid for by a genuine user cost: a session
    that expired mid-visit was indistinguishable from never having signed in,
    so the client could not tell "refresh" from "log in again" (F-34).

    RFC 6750 takes the same position, distinguishing `invalid_token` reasons
    in the WWW-Authenticate header.
    """
    try:
        payload = jwt.decode(
            token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM]
        )
    except jwt.ExpiredSignatureError:
        return None, TOKEN_EXPIRED
    except (jwt.PyJWTError, TypeError, ValueError):
        return None, TOKEN_INVALID

    sub = payload.get("sub")
    if sub is None:
        return None, TOKEN_INVALID
    try:
        return int(sub), None
    except (TypeError, ValueError):
        # A well-signed token whose subject is not a user id is forged or
        # from another issuer. Never treat that as anonymous.
        return None, TOKEN_INVALID


def decode_token(token: str) -> int | None:
    """User id, or None for any invalid token. Kept for callers that do not
    care why."""
    return decode_token_detailed(token)[0]


def _unauthorized(reason: str, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"message": detail, "code": reason},
        # RFC 6750: the reason belongs here too, so a client can act on it
        # without depending on the body shape.
        headers={
            "WWW-Authenticate": (
                f'Bearer error="invalid_token", error_description="{reason}"'
            )
        },
    )


# --- Dependencies ---------------------------------------------------------

SessionDep = Annotated[Session, Depends(get_session)]
CredsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def get_current_user_optional(creds: CredsDep, session: SessionDep) -> User | None:
    """The caller, or None when there is genuinely no caller.

    "Optional" means *the endpoint tolerates anonymous visitors*. It does not
    mean authentication is optional once attempted: a request that presents a
    token is claiming an identity, and a bad claim is an error, not an
    anonymous visit (F-34).

    The distinction is not pedantic. Silently downgrading meant a user whose
    token expired kept browsing while their own private data quietly vanished
    from their results, with nothing to tell them why.
    """
    if creds is None or not creds.credentials:
        return None  # no claim made — anonymous is the honest reading

    user_id, reason = decode_token_detailed(creds.credentials)
    if reason == TOKEN_EXPIRED:
        raise _unauthorized(TOKEN_EXPIRED, "Session expired. Refresh and retry.")
    if reason is not None or user_id is None:
        raise _unauthorized(TOKEN_INVALID, "Invalid authentication token.")

    user = session.get(User, user_id)
    if user is None:
        # Well-signed token for a user that no longer exists.
        raise _unauthorized(TOKEN_INVALID, "Invalid authentication token.")
    if not user.is_active:
        raise _unauthorized(ACCOUNT_INACTIVE, "This account is disabled.")
    return user


def get_current_user(
    user: Annotated[User | None, Depends(get_current_user_optional)],
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]


# --- Registration / login -------------------------------------------------


def normalise_email(email: str) -> str:
    return email.strip().lower()


def register_user(session: Session, email: str, password: str, display_name: str | None = None) -> User:
    email = normalise_email(email)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    existing = session.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise ValueError("An account with that email already exists.")

    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=(display_name or "").strip() or None,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def authenticate(session: Session, email: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.email == normalise_email(email)))
    if user is None:
        # Hash anyway so a missing account and a wrong password take
        # comparable time — otherwise response timing enumerates users.
        _hasher.hash(password)
        return None
    if not user.is_active or not verify_password(password, user.password_hash):
        return None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        session.commit()
    return user
