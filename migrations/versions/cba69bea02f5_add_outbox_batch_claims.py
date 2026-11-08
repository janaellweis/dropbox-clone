"""add outbox batch claims

Revision ID: cba69bea02f5
Revises: da877d9fe246
Create Date: 2026-11-08 13:22:51.091975

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'cba69bea02f5'
down_revision: Union[str, Sequence[str], None] = 'da877d9fe246'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column("claimed_by", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column(
            "claimed_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("outbox_events", "claimed_until")
    op.drop_column("outbox_events", "claimed_by")
