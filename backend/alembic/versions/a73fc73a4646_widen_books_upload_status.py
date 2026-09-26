"""widen books.upload_status to varchar(32)

Found running the pipeline's own tests, not in production: `"extraction_failed"`
is 17 characters, one past `VARCHAR(16)`'s limit set in c25d1ebf747d, and
Postgres raised `StringDataRightTruncation` rather than silently truncating
it. 32 gives headroom for the vocabulary this pipeline is still growing
(section 29's remaining stages), rather than a second one-character-margin
column needing widening again the next time a value is added.

Revision ID: a73fc73a4646
Revises: c25d1ebf747d
Create Date: 2026-09-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a73fc73a4646'
down_revision: Union[str, Sequence[str], None] = 'c25d1ebf747d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "books", "upload_status",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "books", "upload_status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
    )
