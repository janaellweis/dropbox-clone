"""add per file event sequencing

Revision ID: b8b4ea608403
Revises: 8ca22c4f6a11
Create Date: 2026-11-02 16:03:45.557348

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b8b4ea608403'
down_revision: Union[str, Sequence[str], None] = '8ca22c4f6a11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add and initialize per-file event sequencing."""

    op.add_column(
        "files",
        sa.Column(
            "event_sequence",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )

    op.add_column(
        "outbox_events",
        sa.Column("event_sequence", sa.Integer(), nullable=True),
    )

    # Number existing events independently for each file.
    op.execute("""
        WITH numbered_events AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY file_id
                    ORDER BY created_at, id
                ) AS sequence_number
            FROM outbox_events
        )
        UPDATE outbox_events AS o
        SET event_sequence = n.sequence_number
        FROM numbered_events AS n
        WHERE o.id = n.id
    """)

    # Store each file's latest assigned sequence.
    op.execute("""
        UPDATE files AS f
        SET event_sequence = COALESCE(
            (
                SELECT MAX(o.event_sequence)
                FROM outbox_events AS o
                WHERE o.file_id = f.id
            ),
            0
        )
    """)

    op.alter_column(
        "outbox_events",
        "event_sequence",
        nullable=False,
    )

    op.create_unique_constraint(
        "uq_outbox_file_event_sequence",
        "outbox_events",
        ["file_id", "event_sequence"],
    )


def downgrade() -> None:
    """Remove per-file event sequencing."""

    op.drop_constraint(
        "uq_outbox_file_event_sequence",
        "outbox_events",
        type_="unique",
    )

    op.drop_column("outbox_events", "event_sequence")
    op.drop_column("files", "event_sequence")
