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


def decode_token(token: str) -> int | None:
    """Return the user id, or None for any invalid/expired token.

    Deliberately does not distinguish failure modes to the caller — an
    attacker learns nothing from 'expired' versus 'malformed'.
    """
    try:
        payload = jwt.decode(
            token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM]
        )
        sub = payload.get("sub")
        return int(sub) if sub is not None else None
    except (jwt.PyJWTError, TypeError, ValueError):
        return None


# --- Dependencies ---------------------------------------------------------

SessionDep = Annotated[Session, Depends(get_session)]
CredsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def get_current_user_optional(creds: CredsDep, session: SessionDep) -> User | None:
    if creds is None or not creds.credentials:
        return None
    user_id = decode_token(creds.credentials)
    if user_id is None:
        return None
    user = session.get(User, user_id)
    return user if user and user.is_active else None


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
