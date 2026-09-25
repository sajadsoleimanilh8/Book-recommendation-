"""phase 4: upload metadata and attestation on books

Section 29/30. Six nullable columns, every one NULL for the 29,975 existing
catalogue rows and populated only when `books.owner_id` is set — the same
"NULL means catalogue" convention `owner_id` itself established.

`upload_status`, `source_filename`, `file_format`, `storage_path`,
`file_size_bytes` describe the uploaded file. `attested_at` and
`attestation_version` are section 30's ownership attestation: the reader's
explicit, timestamped claim that they may lawfully upload this file, with
the exact wording they agreed to recorded alongside it so a later change to
that wording does not retroactively reinterpret an existing attestation.

No CHECK constraint on `upload_status`, matching `enrichment_status`'s own
precedent: the vocabulary will grow as the extraction pipeline (section 29)
is built in later slices, and nothing here should need a constraint widened
to add a value.

Revision ID: c25d1ebf747d
Revises: 7bf266537994
Create Date: 2026-09-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c25d1ebf747d'
down_revision: Union[str, Sequence[str], None] = '7bf266537994'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("books", sa.Column("upload_status", sa.String(length=16), nullable=True))
    op.add_column("books", sa.Column("source_filename", sa.Text(), nullable=True))
    op.add_column("books", sa.Column("file_format", sa.String(length=8), nullable=True))
    op.add_column("books", sa.Column("storage_path", sa.Text(), nullable=True))
    op.add_column("books", sa.Column("file_size_bytes", sa.Integer(), nullable=True))
    op.add_column("books", sa.Column("attested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("books", sa.Column("attestation_version", sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("books", "attestation_version")
    op.drop_column("books", "attested_at")
    op.drop_column("books", "file_size_bytes")
    op.drop_column("books", "storage_path")
    op.drop_column("books", "file_format")
    op.drop_column("books", "source_filename")
    op.drop_column("books", "upload_status")
