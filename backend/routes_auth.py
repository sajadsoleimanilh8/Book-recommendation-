"""Authentication endpoints — /api/auth/*."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

import auth
from auth import CurrentUser, SessionDep

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(..., min_length=auth.MIN_PASSWORD_LENGTH, max_length=256)
    display_name: str | None = Field(None, max_length=100)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(..., min_length=1, max_length=256)


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str | None
    locale: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


def _user_out(user) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        locale=user.locale,
    )


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, session: SessionDep) -> TokenOut:
    try:
        user = auth.register_user(
            session, payload.email, payload.password, payload.display_name
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return TokenOut(access_token=auth.create_access_token(user.id), user=_user_out(user))


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, session: SessionDep) -> TokenOut:
    user = auth.authenticate(session, payload.email, payload.password)
    if user is None:
        # One message for both "no such account" and "wrong password", so the
        # response does not enumerate registered emails.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenOut(access_token=auth.create_access_token(user.id), user=_user_out(user))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return _user_out(user)
