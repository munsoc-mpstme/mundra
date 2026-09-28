"""committees and chat messages

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-09-27 02:00:00.000000

Adds committees (with a session status) and the per-committee chat channel messages, for
the hospitality<->rapporteur chat. See docs/adr/0003.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, Sequence[str], None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('committees',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('status', sa.String(), server_default='in_session', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('in_session', 'adjourned')", name=op.f('ck_committees_status_valid')),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], name=op.f('fk_committees_event_id_events'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_committees')),
    sa.UniqueConstraint('event_id', 'name', name='event_committee_name')
    )
    op.create_index(op.f('ix_committees_event_id'), 'committees', ['event_id'], unique=False)
    op.create_table('chat_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('committee_id', sa.Integer(), nullable=False),
    sa.Column('sender_email', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), server_default='text', nullable=False),
    sa.Column('body', sa.String(), server_default='', nullable=False),
    sa.Column('payload', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('text', 'status')", name=op.f('ck_chat_messages_kind_valid')),
    sa.ForeignKeyConstraint(['committee_id'], ['committees.id'], name=op.f('fk_chat_messages_committee_id_committees'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_messages'))
    )
    op.create_index(op.f('ix_chat_messages_committee_id'), 'chat_messages', ['committee_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_chat_messages_committee_id'), table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_index(op.f('ix_committees_event_id'), table_name='committees')
    op.drop_table('committees')
