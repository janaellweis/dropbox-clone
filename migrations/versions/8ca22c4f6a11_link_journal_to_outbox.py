"""link journal to outbox

Revision ID: 8ca22c4f6a11
Revises: 34b24deb428e
Create Date: 2026-11-02 14:15:05.156150

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8ca22c4f6a11'
down_revision: Union[str, Sequence[str], None] = '34b24deb428e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "change_journal",
        sa.Column("outbox_event_id", sa.Uuid(), nullable=True),
    )

    op.create_unique_constraint(
        "uq_change_journal_outbox_event_id",
        "change_journal",
        ["outbox_event_id"],
    )

    op.create_foreign_key(
        "fk_change_journal_outbox_event_id",
        "change_journal",
        "outbox_events",
        ["outbox_event_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_change_journal_outbox_event_id",
        "change_journal",
        type_="foreignkey",
    )

    op.drop_constraint(
        "uq_change_journal_outbox_event_id",
        "change_journal",
        type_="unique",
    )

    op.drop_column("change_journal", "outbox_event_id")