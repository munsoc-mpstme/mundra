"""EB Tools: the live committee floor (speakers' lists and timer)

Revision ID: c7e1f0a2b3d4
Revises: b8d4f6c82e31
Create Date: 2026-10-20 00:00:00.000000

- committee_floors: one JSON document per committee with its GSL and moderated caucus
  lists and its timer (see floor.py), so a restart or redeploy doesn't lose them.
  Additive only.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c7e1f0a2b3d4'
down_revision: Union[str, Sequence[str], None] = 'b8d4f6c82e31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'committee_floors',
        sa.Column('committee_id', sa.Integer(), nullable=False),
        sa.Column('state', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('updated_by', sa.String(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['committee_id'], ['committees.id'], name=op.f('fk_committee_floors_committee_id_committees'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('committee_id', name=op.f('pk_committee_floors')),
    )


def downgrade() -> None:
    op.drop_table('committee_floors')
