"""phase 4: books.owner_id for private uploads

Section 29. NULL means "catalogue"; a value means "this row is one reader's
private upload". See `db.models.Book.owner_id` for why ownership is a column
rather than a `source == 'upload'` convention.

The column is added before any upload feature exists, deliberately: every
pass that walks `books` was written when every row was catalogue, and the
isolation has to be in place before the first private row can be created
rather than after. `ingest.py` is the reason for the ordering — it deletes
every book absent from the catalogue file, which an upload always is.

Nullable with no backfill: the 29,975 existing rows are catalogue, and NULL
already says so.

Revision ID: 7bf266537994
Revises: faaa9c1a2d93
Create Date: 2026-09-22 12:26:53.745082

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7bf266537994'
down_revision: Union[str, Sequence[str], None] = 'faaa9c1a2d93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Named explicitly. Autogenerate emitted `create_foreign_key(None, ...)` and
# `drop_constraint(None, ...)`, which is F-28 again: the upgrade works because
# Postgres invents a name, and the downgrade then fails because it has none to
# drop. A migration that cannot be reversed is one you find out about while
# trying to reverse it.
FK_OWNER = "fk_books_owner_id_users"
IX_OWNER = "ix_books_owner"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("books", sa.Column("owner_id", sa.Integer(), nullable=True))
    # Partial: catalogue-wide queries filter on `owner_id IS NULL`, which is
    # nearly every row, so the small side is the side worth indexing.
    op.create_index(
        IX_OWNER,
        "books",
        ["owner_id"],
        unique=False,
        postgresql_where=sa.text("owner_id IS NOT NULL"),
    )
    # ON DELETE CASCADE: deleting a reader takes their uploads with them,
    # which then cascades to their chunks and progress. A private book with
    # no owner would be an orphan that `catalogue_only()` treats as public.
    op.create_foreign_key(
        FK_OWNER, "books", "users", ["owner_id"], ["id"], ondelete="CASCADE"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(FK_OWNER, "books", type_="foreignkey")
    op.drop_index(IX_OWNER, table_name="books", postgresql_where=sa.text("owner_id IS NOT NULL"))
    op.drop_column("books", "owner_id")
