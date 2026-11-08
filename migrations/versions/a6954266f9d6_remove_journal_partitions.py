"""remove journal partitions

Revision ID: a6954266f9d6
Revises: cba69bea02f5
Create Date: 2026-11-08 13:30:10.696115

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a6954266f9d6'
down_revision: Union[str, Sequence[str], None] = 'cba69bea02f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.drop_constraint(
        "uq_journal_partition_position",
        "change_journal",
        type_="unique",
    )
    op.drop_column("change_journal", "partition_id")
    op.drop_column("change_journal", "partition_position")
    op.drop_column("outbox_events", "partition_id")
    op.drop_table("partition_state")
def downgrade() -> None:
    raise NotImplementedError(
        "Restoring journal partitions requires a data migration."
    )