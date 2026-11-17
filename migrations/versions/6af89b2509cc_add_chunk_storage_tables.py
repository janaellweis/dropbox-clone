"""add chunk storage tables

Revision ID: 6af89b2509cc
Revises: a6954266f9d6
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "6af89b2509cc"
down_revision: Union[str, Sequence[str], None] = "a6954266f9d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("hash"),
    )

    op.create_table(
        "file_version_chunks",
        sa.Column("file_version_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["file_version_id"],
            ["file_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["chunk_hash"],
            ["chunks.hash"],
        ),
        sa.PrimaryKeyConstraint(
            "file_version_id",
            "chunk_index",
        ),
    )


def downgrade() -> None:
    op.drop_table("file_version_chunks")
    op.drop_table("chunks")
