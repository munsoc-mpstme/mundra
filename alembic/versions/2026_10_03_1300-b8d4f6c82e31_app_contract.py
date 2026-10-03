"""Delego app contract: scan diet and the break-coordination committees

Revision ID: b8d4f6c82e31
Revises: a9c3e5b71d20
Create Date: 2026-10-03 13:00:00.000000

- meal_scans.diet: the diet the scanner operator picked, used for the plate counts.
- Seeds the eight Mumbai MUN committees (UNSC, CCC, PSC, WTO, UNODC, UNICEF, ECOSOC, IPC)
  into the seeded event, so break coordination has channels to open. Skips any that exist.

Event dates are deliberately not touched: an admin sets them with PATCH /events/{id}. Until
then (and outside the conference) meal scanning falls back to the calendar date.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8d4f6c82e31'
down_revision: Union[str, Sequence[str], None] = 'a9c3e5b71d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EVENT_NAME = 'Mumbai MUN 2026'
COMMITTEES = ('UNSC', 'CCC', 'PSC', 'WTO', 'UNODC', 'UNICEF', 'ECOSOC', 'IPC')


def upgrade() -> None:
    op.add_column('meal_scans', sa.Column('diet', sa.String(), nullable=True))

    bind = op.get_bind()
    for name in COMMITTEES:  # one at a time so ids follow this order
        bind.execute(
            sa.text(
                "INSERT INTO committees (event_id, name) "
                "SELECT e.id, CAST(:name AS varchar) FROM events e WHERE e.name = CAST(:event AS varchar) "
                "AND NOT EXISTS (SELECT 1 FROM committees c "
                "WHERE c.event_id = e.id AND c.name = CAST(:name AS varchar)) "
                "ORDER BY e.id LIMIT 1"
            ),
            {"name": name, "event": EVENT_NAME},
        )


def downgrade() -> None:
    # The seeded committees are data, not schema, and may have
    # messages or edits attached, so they are left in place.
    op.drop_column('meal_scans', 'diet')
