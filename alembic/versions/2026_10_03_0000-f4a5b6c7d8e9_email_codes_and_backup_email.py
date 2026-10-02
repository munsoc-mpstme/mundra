"""6-digit email verification codes and delegate backup email

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-10-03 00:00:00.000000

Adds the email_verifications table (6-digit codes replacing the JWT verification link)
and a backup_email column on delegates (verification and reset mail are also sent there).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4a5b6c7d8e9'
down_revision: Union[str, Sequence[str], None] = 'e3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'delegates',
        sa.Column('backup_email', sa.String(), server_default='', nullable=False),
    )
    op.create_table('email_verifications',
    sa.Column('email', sa.String(), nullable=False),
    sa.Column('code', sa.String(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['email'], ['delegates.email'], name=op.f('fk_email_verifications_email_delegates'), onupdate='CASCADE', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('email', name=op.f('pk_email_verifications'))
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('email_verifications')
    op.drop_column('delegates', 'backup_email')
