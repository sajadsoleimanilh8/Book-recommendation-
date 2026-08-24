"""SQLAlchemy models — the Core schema from the Phase 0 audit, section F.

Three design decisions carried from the audit, all load-bearing:

1. **Stable book identity (F-10, H.1).** The pre-PR-2 code keyed books by
   array position (`id = i + 1`), which changes whenever the dataset is
   re-sorted and silently invalidates every stored comment, progress row and
   cached audio file. `app_state.json` is evidence a *previous* version used
   real external IDs and that this was a regression. UNIQUE(source,
   external_id) restores durable identity; the integer PK is a surrogate.

2. **i18n-ready from the first migration.** English-only was the product
   decision, but `books.language` and `users.locale` are first-class columns
   now because adding them later means a migration over live data. All text
   is TEXT, never VARCHAR(n) sized for Latin script.

3. **Ownership columns exist from day one (F-07).** `comments.user_id` is
   what makes "you may only delete your own comment" expressible. Before
   PR 2, DELETE /api/comments/{book_id}/{index} had no ownership concept at
   all — anyone could delete anything.

Observability tables ship here too, before any model work, because the audit
found no metric in this codebase measures recommendation quality (F-13) and
that cannot be fixed retroactively — the data has to start accumulating now.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


TimestampCol = lambda: mapped_column(  # noqa: E731
    DateTime(timezone=True), default=utcnow, nullable=False
)


# ==========================================================================
# Core
# ==========================================================================


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)

    # i18n-ready (see module docstring). Only "en" is served today.
    locale: Mapped[str] = mapped_column(String(10), default="en", nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = TimestampCol()

    comments: Mapped[list["Comment"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    progress: Mapped[list["ReadingProgress"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    reminders: Mapped[list["Reminder"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Book(Base):
    __tablename__ = "books"
    __table_args__ = (
        # F-10 / H.1: the durable identity. The catalogue mixes Google Books
        # volume ids (strings) and Goodreads ids (ints) in one field, so
        # `source` is what disambiguates them.
        UniqueConstraint("source", "external_id", name="uq_books_source_external"),
        Index("ix_books_genre", "genre"),
        Index("ix_books_language", "language"),
        Index("ix_books_rating", "average_rating"),
        # Phase 2: the reconciliation join key, and the F-25 duplicate check.
        Index("ix_books_isbn_13", "isbn_13"),
        Index("ix_books_isbn_10", "isbn_10"),
        # Drives the backfill queue: "what still needs enriching, in priority
        # order". A partial index keeps it small as the backlog drains.
        Index(
            "ix_books_enrichment_pending",
            "enrichment_status",
            postgresql_where=text("enrichment_status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(Text, default="Unknown", nullable=False)
    genre: Mapped[str] = mapped_column(Text, default="Unknown", nullable=False)

    # Nullable on purpose. 100% of the current catalogue has no description
    # (F-15) and the honest representation of "we don't have it" is NULL,
    # not the placeholder string "No description available" that the JSONL
    # carries. Phase 2 enrichment fills these in; `description IS NULL` is
    # the query that drives the backfill queue.
    description: Mapped[str | None] = mapped_column(Text)

    # i18n-ready. NULL means unknown, which is 9,842 of 29,975 records.
    language: Mapped[str | None] = mapped_column(String(10))

    average_rating: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    ratings_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    list_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    published_year: Mapped[int | None] = mapped_column(Integer)
    thumbnail: Mapped[str | None] = mapped_column(Text)

    # Phase 2. ISBNs are the only reliable cross-provider join key: Google
    # volume ids and Goodreads ids do not interoperate, and title+author
    # matching is ambiguous across editions and translations.
    #
    # They are also the evidence F-25 needs. 1,576 records were collapsed as
    # duplicates on (source, external_id) alone; two records with *different*
    # ISBNs are distinct editions, not duplicates, and must be split back out.
    isbn_10: Mapped[str | None] = mapped_column(String(10))
    isbn_13: Mapped[str | None] = mapped_column(String(13))

    # Enrichment bookkeeping — makes the backfill resumable and idempotent.
    # Without it a run that dies at book 9,000 has to start from zero, and
    # with a 1,000/day quota that is not a recoverable mistake.
    enrichment_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", nullable=False
    )
    enrichment_source: Mapped[str | None] = mapped_column(String(32))
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = TimestampCol()

    comments: Mapped[list["Comment"]] = relationship(back_populates="book")


class UserBook(Base):
    """Ownership — section 21 book/user isolation."""

    __tablename__ = "user_books"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    acquired_at: Mapped[datetime] = TimestampCol()


class ReadingProgress(Base):
    """Replaces ReminderEngine._progress — F-12."""

    __tablename__ = "reading_progress"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
    )
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    page: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="progress")


class Comment(Base):
    """Replaces CommentEngine's in-memory dict — F-12, and closes F-07's
    delete hole by giving every comment an owner."""

    __tablename__ = "comments"
    __table_args__ = (Index("ix_comments_book", "book_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[int | None] = mapped_column(Integer)
    sentiment: Mapped[str | None] = mapped_column(String(16))
    keywords: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = TimestampCol()

    user: Mapped["User"] = relationship(back_populates="comments")
    book: Mapped["Book"] = relationship(back_populates="comments")


class Reminder(Base):
    """Replaces ReminderEngine._reminders — F-12.

    Note this stores intent only. Delivery is still F-18 (the engine fires
    plyer desktop notifications on the *server*, which reach nobody). Moving
    delivery to a real channel is Phase 6.
    """

    __tablename__ = "reminders"
    __table_args__ = (
        UniqueConstraint("user_id", "book_id", name="uq_reminder_user_book"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_notified: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = TimestampCol()

    user: Mapped["User"] = relationship(back_populates="reminders")


# ==========================================================================
# Observability — the answer to F-13
# ==========================================================================


class InteractionEvent(Base):
    """Every user action worth learning from.

    This is the seed of the data moat (section 54). It ships before any model
    work because interaction history cannot be backfilled — a week not logged
    is a week lost permanently.
    """

    __tablename__ = "interaction_events"
    __table_args__ = (
        Index("ix_events_user_time", "user_id", "created_at"),
        Index("ix_events_type_time", "event_type", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Nullable: anonymous browsing is still signal.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    book_id: Mapped[int | None] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE")
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    context: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = TimestampCol()


class RecommendationLog(Base):
    """What was requested, what was considered, what was shown.

    Without this there is no held-out ground truth, and F-13's circular
    target cannot be replaced with a real one. The audit is explicit that
    Phase 3 is gated on this table having accumulated data — so it starts
    filling now, in Phase 1.
    """

    __tablename__ = "recommendation_log"
    __table_args__ = (Index("ix_reclog_user_time", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    request: Mapped[dict | None] = mapped_column(JSONB)
    shown: Mapped[dict | None] = mapped_column(JSONB)
    model_version: Mapped[str | None] = mapped_column(String(64))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = TimestampCol()
