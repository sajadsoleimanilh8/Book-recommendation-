"""book_memory — section 33, Phase 5 (AI Book Memory)

One row per Reading Copilot turn, append-only: (user_id, book_id, summary,
created_at). Never a raw question or a full answer — services/memory.py
bounds `summary` to a short, structured line, which is what "do not replay
raw transcripts forever" requires at the storage layer, not just in the
prompt built from it.

Revision ID: a9011ba4228d
Revises: a73fc73a4646
Create Date: 2026-09-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9011ba4228d'
down_revision: Union[str, Sequence[str], None] = 'a73fc73a4646'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

IX_USER_BOOK = "ix_book_memory_user_book"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "book_memory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
    )
    # Every read this table serves is "this reader's memory for this book,
    # most recent first" — the composite index matches that access exactly.
    op.create_index(IX_USER_BOOK, "book_memory", ["user_id", "book_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(IX_USER_BOOK, table_name="book_memory")
    op.drop_table("book_memory")
