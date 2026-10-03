"""eb role

Revision ID: a9c3e5b71d20
Revises: f4a5b6c7d8e9
Create Date: 2026-10-03 12:00:00.000000

Allows the 'eb' (executive board) role on users. EB members get the app's chair tools;
they hold no OC team permissions.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a9c3e5b71d20'
down_revision: Union[str, Sequence[str], None] = 'f4a5b6c7d8e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Allow the 'eb' role."""
    op.drop_constraint(op.f('ck_users_role_valid'), 'users', type_='check')
    op.create_check_constraint(
        op.f('ck_users_role_valid'), 'users', "role IN ('delegate', 'eb', 'oc', 'admin')"
    )


def downgrade() -> None:
    """Turn any EB users back into delegates, then disallow the role."""
    op.execute("UPDATE users SET role = 'delegate' WHERE role = 'eb'")
    op.drop_constraint(op.f('ck_users_role_valid'), 'users', type_='check')
    op.create_check_constraint(
        op.f('ck_users_role_valid'), 'users', "role IN ('delegate', 'oc', 'admin')"
    )
