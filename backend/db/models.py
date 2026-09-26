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
    CheckConstraint,
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
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base


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
        # Phase 4. Every catalogue-wide query filters on `owner_id IS NULL`,
        # and that is nearly the whole table, so the useful index is the
        # small side: find one reader's uploads without scanning 30k rows.
        Index(
            "ix_books_owner",
            "owner_id",
            postgresql_where=text("owner_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)

    # Phase 4 (section 29). NULL means "catalogue"; a value means "this row is
    # one reader's private upload".
    #
    # Structural, not a `source == 'upload'` convention, for the same reason
    # `book_chunks.visibility` is structural: the thing that must never happen
    # is a private book being treated as catalogue, and a string convention
    # makes that a mistake any new query can make silently. This makes it a
    # column every query either filters on or visibly does not.
    #
    # Nullable rather than a separate `uploads` table because a private book
    # is the same shape as a catalogue book everywhere downstream — chunks,
    # embeddings, reading progress, comments all key on `books.id`, and a
    # second table would mean two of each of those paths, with only one of
    # them getting the authorization right. That is the argument `BookChunk`
    # already makes for one table and two populations.
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )

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

    # Phase 4 (section 29/30). NULL for every catalogue row — these describe
    # an upload, not a book, the same way `owner_id` does. Kept on `books`
    # rather than a separate `uploads` table for the reason `owner_id`'s own
    # comment gives: a private book is the same shape as a catalogue book
    # everywhere downstream, and a second table would mean two of every join.
    #
    # `upload_status` has exactly one value today, deliberately not enforced
    # by a CHECK constraint (same choice as `enrichment_status`, which has
    # never had one): "uploaded" means the file passed validation and is
    # stored, waiting for a pipeline that does not exist yet (extraction,
    # section 29's Validate -> ... -> READY). Future slices add to this
    # vocabulary; nothing here should need to widen a constraint to do it.
    upload_status: Mapped[str | None] = mapped_column(String(32))
    source_filename: Mapped[str | None] = mapped_column(Text)
    file_format: Mapped[str | None] = mapped_column(String(8))
    # Relative to `main.UPLOADS_DIR`, never derived from `source_filename` —
    # F-04/F-05 already cost this project an arbitrary-file-overwrite and a
    # path-traversal finding over exactly this shape of mistake, for a
    # different feature. The name on disk is a server-generated id
    # (`external_id` + a validated extension), so nothing a caller supplies
    # ever becomes part of a filesystem path.
    storage_path: Mapped[str | None] = mapped_column(Text)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)

    # Section 30: the reader's explicit claim that they may lawfully upload
    # this file, captured at the point of upload rather than inferred from
    # the act of uploading. `attestation_version` records which wording they
    # agreed to, so a later change to that wording does not retroactively
    # change what an existing attestation means.
    attested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attestation_version: Mapped[str | None] = mapped_column(String(16))

    created_at: Mapped[datetime] = TimestampCol()

    comments: Mapped[list["Comment"]] = relationship(back_populates="book")


def catalogue_only(stmt):
    """Restrict a `Book` query to the shared catalogue, excluding uploads.

    Phase 4, section 29. Every pass that walks `books` — enrichment, vector
    building, Gutenberg text, ingest — was written when every row was
    catalogue, so each one's "all books" meant what it said. The moment a
    reader uploads a private book, "all books" silently includes it.

    The enrichment pass is the one that made this urgent rather than tidy:
    `pending_query` takes `sources: list[str] | None = None` and applies no
    source filter when that is None, so an uploaded book would have been
    queued and its title and author sent to Google Books. That is a third
    party learning what is in someone's private library, and it spends the
    quota that is this project's binding constraint (F-30, OI-7) on a book
    no other reader can ever see.

    Written as `owner_id IS NULL` rather than `source != 'upload'`
    deliberately. A denylist fails open: the next private source anyone adds
    is included by default, and the failure is invisible. This fails closed,
    and the column makes the distinction impossible to forget rather than
    merely documented.
    """
    return stmt.where(Book.owner_id.is_(None))


def owned_by(stmt, user_id: int):
    """The other half: one reader's uploads, and nobody else's."""
    return stmt.where(Book.owner_id == user_id)


class BookText(Base):
    """Real book text, for books whose text we may lawfully hold.

    A separate table rather than a column on `books` on purpose: `books` is
    scanned constantly for filtering and ranking, and hanging a ~20 KB TEXT
    column off every row would make those scans carry content no query needs.

    Bounded to the opening chapters (see providers.gutenberg_text). That is
    enough to close the *fabricated content* half of F-17 — `/api/books/{id}/
    pages` currently returns the literal string "page1 از <title>" for every
    page of every book. Whole-book reading needs a storage decision (6,307
    full texts is ~3 GB), RAG chunking and a paging design: Phase 5.

    Section 11: only public-domain sources. Gutenberg only, recorded in
    `source`, so the licensing basis of every row is explicit.
    """

    __tablename__ = "book_texts"

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_complete: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    fetched_at: Mapped[datetime] = TimestampCol()


# Dimension of the embedding column. 384 is all-MiniLM-L6-v2, the smallest
# model that handles §27's query style ("a short philosophical book that makes
# me question my life") — those need semantics, not the lexical matching the
# existing TF-IDF/LSA engine does. Changing this is a migration, so it is a
# named constant rather than a literal buried in the column definition.
EMBEDDING_DIM = 384


class BookChunk(Base):
    """Retrievable pieces of a book, public catalogue or private upload (§22).

    One table, two populations, because a user searching their own library
    expects one result list rather than two — and because the alternative
    (separate public/private tables) means every retrieval path is written
    twice and only one of them gets the authorization right.

    The safety property that matters: **a user's private chunks must never
    appear in another user's search.** It is expressed here structurally
    rather than left to each query to remember:

        public   ->  visibility='public'  AND user_id IS NULL
        private  ->  visibility='private' AND user_id = <owner>

    A CHECK constraint makes the illegal states unrepresentable, so a private
    chunk cannot exist without an owner, and a public chunk cannot carry one.
    Without it, a single NULL user_id on a private row would silently publish
    somebody's uploaded book to every search on the platform.

    Section 11 still governs content: public rows only ever hold text we may
    lawfully redistribute. Private rows hold the user's own upload and are
    never served to anyone else.
    """

    __tablename__ = "book_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    # NULL for catalogue chunks. Deliberately nullable: "no owner" is what
    # makes a chunk public, and it is checked against `visibility` below.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="public"
    )
    # Position within the source text, so retrieved chunks can be shown in
    # order and neighbouring context can be fetched without a second scan.
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    # Where the text came from. Section 11 requires the licensing basis of
    # every stored passage to be explicit and auditable, and these differ:
    # "text" is public-domain prose from Project Gutenberg, "description" is a
    # third-party blurb from a metadata provider. Serving them is the same
    # operation; being able to tell them apart afterwards is not optional.
    # It also matters for retrieval — a blurb and a chapter are different
    # kinds of evidence that a book matches a query.
    origin: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="text"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Nullable because chunking and embedding are separate passes: text is
    # cheap and local, embeddings are not. A NULL embedding means "chunked,
    # not yet embedded", which is exactly the work queue for the embed pass.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    # Which model produced the vector. Without it, a model change silently
    # mixes incompatible vector spaces in one column and retrieval quietly
    # degrades instead of failing.
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = TimestampCol()

    __table_args__ = (
        # Makes the illegal states unrepresentable rather than trusting every
        # future INSERT to remember the rule. A private chunk without an owner
        # would be visible to every search on the platform.
        CheckConstraint(
            "(visibility = 'public'  AND user_id IS NULL) OR "
            "(visibility = 'private' AND user_id IS NOT NULL)",
            name="ck_book_chunks_visibility_owner",
        ),
        # Postgres treats NULLs as distinct in unique constraints, so the
        # plain form would not stop duplicate *public* chunks — the common
        # case. NULLS NOT DISTINCT (PG15+, and the image is PG16) fixes that.
        Index(
            "uq_book_chunks_identity",
            "book_id",
            "user_id",
            "ordinal",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        # The authorization filter is (visibility, user_id) on every search.
        Index("ix_book_chunks_visibility_user", "visibility", "user_id"),
        # Finds the embed pass's work queue without scanning content.
        Index(
            "ix_book_chunks_unembedded",
            "id",
            postgresql_where=text("embedding IS NULL"),
        ),
    )


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


class BookVector(Base):
    """One embedding per book, for content similarity — F-44.

    Distinct from `book_chunks` on purpose. A chunk is a passage someone can
    read; this is a summary of the whole book, and mixing the two would put
    29,975 metadata blobs into passage search results.

    **Uniform recipe, every book.** `title + author + genre + description`,
    with the description simply absent when there is none. The tempting
    alternative — chunk means where chunks exist, metadata elsewhere — would
    create two populations with different character inside one vector space,
    which is F-39 wearing a different hat. Comparable vectors require
    comparable construction, not merely the same model.

    Why it must cover everything: content similarity carries weight 0.28 in
    the final blend and feeds the LTR component's 0.32. Only 23.3% of books
    have a chunk, so a vector built from chunks alone would silently drop
    three quarters of the catalogue out of content-based ranking. Nothing
    would error; the rankings would just quietly get worse.
    """

    __tablename__ = "book_vectors"

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    # Same reason as book_chunks.embedding_model: without it, a backend change
    # leaves two vector spaces in one column and nothing says so (F-39).
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    # What text produced the vector, so a thin metadata-only vector can be
    # told apart from one that saw a real description. Recorded now because
    # explanations (section 26) will want it and it is expensive to backfill.
    has_description: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    source_chars: Mapped[int | None] = mapped_column(Integer)
    built_at: Mapped[datetime] = TimestampCol()


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
