"""Database engine, session factory, and declarative base."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from core import config

engine = create_engine(
    config.DATABASE_URL,
    echo=config.DB_ECHO,
    pool_size=config.DB_POOL_SIZE,
    max_overflow=config.DB_MAX_OVERFLOW,
    # Recycle before typical cloud idle timeouts so a pooled connection is
    # never handed out already dead.
    pool_pre_ping=True,
    pool_recycle=1800,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_session() -> Iterator[Session]:
    """FastAPI dependency. One session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
