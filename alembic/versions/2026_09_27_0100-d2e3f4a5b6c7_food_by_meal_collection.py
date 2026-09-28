"""food tracked by meal collection, not eligibility

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-09-27 01:00:00.000000

Replaces the per-day meal eligibility flags on mm_delegates with a food preference and a
notes field, and adds meal_scans (one plate per delegate per meal per day) plus
meal_scan_flags (rejected second scans). See docs/adr/0003.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_MEAL_COLUMNS = [
    f"d{day}_{meal}" for day in (1, 2, 3) for meal in ("bf", "lunch", "hitea")
]


def upgrade() -> None:
    """Upgrade schema."""
    # mm_delegates: drop the eligibility flags, add preference + notes.
    for column in _MEAL_COLUMNS:
        op.drop_column('mm_delegates', column)
    op.add_column('mm_delegates', sa.Column('food_preference', sa.String(), nullable=True))
    op.add_column(
        'mm_delegates',
        sa.Column('food_notes', sa.String(), server_default='', nullable=False),
    )
    op.create_check_constraint(
        op.f('ck_mm_delegates_food_preference_valid'),
        'mm_delegates',
        "food_preference IN ('veg', 'non_veg', 'jain')",
    )

    op.create_table('meal_scans',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('delegate_id', sa.String(), nullable=False),
    sa.Column('day', sa.Integer(), nullable=False),
    sa.Column('meal', sa.String(), nullable=False),
    sa.Column('served_by', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("meal IN ('breakfast', 'lunch', 'hitea')", name=op.f('ck_meal_scans_meal_valid')),
    sa.ForeignKeyConstraint(['delegate_id'], ['delegates.id'], name=op.f('fk_meal_scans_delegate_id_delegates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], name=op.f('fk_meal_scans_event_id_events'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_meal_scans')),
    sa.UniqueConstraint('event_id', 'delegate_id', 'day', 'meal', name='delegate_day_meal')
    )
    op.create_index(op.f('ix_meal_scans_delegate_id'), 'meal_scans', ['delegate_id'], unique=False)
    op.create_index(op.f('ix_meal_scans_event_id'), 'meal_scans', ['event_id'], unique=False)
    op.create_table('meal_scan_flags',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('delegate_id', sa.String(), nullable=False),
    sa.Column('day', sa.Integer(), nullable=False),
    sa.Column('meal', sa.String(), nullable=False),
    sa.Column('scanned_by', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['delegate_id'], ['delegates.id'], name=op.f('fk_meal_scan_flags_delegate_id_delegates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], name=op.f('fk_meal_scan_flags_event_id_events'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_meal_scan_flags'))
    )
    op.create_index(op.f('ix_meal_scan_flags_delegate_id'), 'meal_scan_flags', ['delegate_id'], unique=False)
    op.create_index(op.f('ix_meal_scan_flags_event_id'), 'meal_scan_flags', ['event_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_meal_scan_flags_event_id'), table_name='meal_scan_flags')
    op.drop_index(op.f('ix_meal_scan_flags_delegate_id'), table_name='meal_scan_flags')
    op.drop_table('meal_scan_flags')
    op.drop_index(op.f('ix_meal_scans_event_id'), table_name='meal_scans')
    op.drop_index(op.f('ix_meal_scans_delegate_id'), table_name='meal_scans')
    op.drop_table('meal_scans')

    op.drop_constraint(op.f('ck_mm_delegates_food_preference_valid'), 'mm_delegates', type_='check')
    op.drop_column('mm_delegates', 'food_notes')
    op.drop_column('mm_delegates', 'food_preference')
    op.add_column('mm_delegates', sa.Column('d1_bf', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    for column in _MEAL_COLUMNS[1:]:
        op.add_column('mm_delegates', sa.Column(column, sa.Boolean(), server_default=sa.text('false'), nullable=False))
