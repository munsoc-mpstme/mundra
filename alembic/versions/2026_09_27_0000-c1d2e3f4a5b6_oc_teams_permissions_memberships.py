"""oc teams, permissions and memberships

Revision ID: c1d2e3f4a5b6
Revises: 7ab3a4dcb760
Create Date: 2026-09-27 00:00:00.000000

Adds the Organizing Committee access model from docs/adr/0003: events, teams and their
permissions, event heads, memberships, roster invites and a roster audit trail. Seeds a
single event, "Mumbai MUN 2026", so teams have something to belong to.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = '7ab3a4dcb760'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_events'))
    )
    op.create_table('teams',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.String(), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], name=op.f('fk_teams_event_id_events'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_teams')),
    sa.UniqueConstraint('event_id', 'name', name='event_name')
    )
    op.create_index(op.f('ix_teams_event_id'), 'teams', ['event_id'], unique=False)
    op.create_table('team_permissions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('permission', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_team_permissions_team_id_teams'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_team_permissions')),
    sa.UniqueConstraint('team_id', 'permission', name='team_permission')
    )
    op.create_index(op.f('ix_team_permissions_team_id'), 'team_permissions', ['team_id'], unique=False)
    op.create_table('event_heads',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('user_email', sa.String(), nullable=False),
    sa.Column('granted_by', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], name=op.f('fk_event_heads_event_id_events'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_email'], ['users.email'], name=op.f('fk_event_heads_user_email_users'), onupdate='CASCADE', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_event_heads')),
    sa.UniqueConstraint('event_id', 'user_email', name='event_user')
    )
    op.create_index(op.f('ix_event_heads_event_id'), 'event_heads', ['event_id'], unique=False)
    op.create_index(op.f('ix_event_heads_user_email'), 'event_heads', ['user_email'], unique=False)
    op.create_table('memberships',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('user_email', sa.String(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('committee', sa.String(), nullable=True),
    sa.Column('level', sa.String(), server_default='member', nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("level IN ('member', 'lead')", name=op.f('ck_memberships_level_valid')),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], name=op.f('fk_memberships_event_id_events'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_memberships_team_id_teams'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_email'], ['users.email'], name=op.f('fk_memberships_user_email_users'), onupdate='CASCADE', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_memberships')),
    sa.UniqueConstraint('event_id', 'user_email', 'team_id', name='event_user_team')
    )
    op.create_index(op.f('ix_memberships_event_id'), 'memberships', ['event_id'], unique=False)
    op.create_index(op.f('ix_memberships_user_email'), 'memberships', ['user_email'], unique=False)
    op.create_table('team_invites',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('committee', sa.String(), nullable=True),
    sa.Column('level', sa.String(), server_default='member', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("level IN ('member', 'lead')", name=op.f('ck_team_invites_level_valid')),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], name=op.f('fk_team_invites_event_id_events'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_team_invites_team_id_teams'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_team_invites')),
    sa.UniqueConstraint('email', 'team_id', name='email_team')
    )
    op.create_index(op.f('ix_team_invites_email'), 'team_invites', ['email'], unique=False)
    op.create_index(op.f('ix_team_invites_event_id'), 'team_invites', ['event_id'], unique=False)
    op.create_table('membership_audit',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('actor_email', sa.String(), nullable=False),
    sa.Column('target_email', sa.String(), nullable=False),
    sa.Column('team_name', sa.String(), nullable=False),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_membership_audit'))
    )

    # Seed the current event so newly created teams have somewhere to belong. Dates are
    # left null until the organisers set them.
    op.execute("INSERT INTO events (name) VALUES ('Mumbai MUN 2026')")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('membership_audit')
    op.drop_index(op.f('ix_team_invites_event_id'), table_name='team_invites')
    op.drop_index(op.f('ix_team_invites_email'), table_name='team_invites')
    op.drop_table('team_invites')
    op.drop_index(op.f('ix_memberships_user_email'), table_name='memberships')
    op.drop_index(op.f('ix_memberships_event_id'), table_name='memberships')
    op.drop_table('memberships')
    op.drop_index(op.f('ix_event_heads_user_email'), table_name='event_heads')
    op.drop_index(op.f('ix_event_heads_event_id'), table_name='event_heads')
    op.drop_table('event_heads')
    op.drop_index(op.f('ix_team_permissions_team_id'), table_name='team_permissions')
    op.drop_table('team_permissions')
    op.drop_index(op.f('ix_teams_event_id'), table_name='teams')
    op.drop_table('teams')
    op.drop_table('events')
