"""replace is_deleted with deleted_at

Revision ID: fb6524bfc8e7
Revises: 7b55b0ef100c
Create Date: 2026-10-26 11:21:43.653339

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fb6524bfc8e7"
down_revision: Union[str, Sequence[str], None] = "7b55b0ef100c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Preserve deletion status for existing deleted files
    op.execute(
        """
        UPDATE files
        SET deleted_at = updated_at
        WHERE is_deleted = true
        """
    )

    op.drop_index("uq_files_active_key", table_name="files")

    op.create_index(
        "uq_files_active_key",
        "files",
        ["key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.drop_column("files", "is_deleted")


def downgrade() -> None:
    op.add_column(
        "files",
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.execute(
        """
        UPDATE files
        SET is_deleted = true
        WHERE deleted_at IS NOT NULL
        """
    )

    op.drop_index("uq_files_active_key", table_name="files")

    op.create_index(
        "uq_files_active_key",
        "files",
        ["key"],
        unique=True,
        postgresql_where=sa.text("is_deleted IS false"),
    )

    op.drop_column("files", "deleted_at")
