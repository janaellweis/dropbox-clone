"""add folder markers

Revision ID: 138b4fb10bf0
Revises: 2cc05b24baed
Create Date: 2026-10-21 14:16:56.927272

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '138b4fb10bf0'
down_revision: Union[str, Sequence[str], None] = '2cc05b24baed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Add the is_folder column to the files table.
    # Mark existing records as regular files.
    op.add_column(
        "files",
        sa.Column(
            "is_folder",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false()
        )
    )


def downgrade() -> None:
    # Remove the column if we reverse the migration.
    op.drop_column("files", "is_folder")