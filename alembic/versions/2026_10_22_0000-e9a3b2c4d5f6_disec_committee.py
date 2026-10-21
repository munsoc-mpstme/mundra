"""Add DISEC committee and its 33-country portfolio matrix

Revision ID: e9a3b2c4d5f6
Revises: d8f2a1b3c4e5
Create Date: 2026-10-22 00:00:00.000000

- Seeds DISEC into the Mumbai MUN 2026 event if not already present.
- Seeds DISEC's 33-country portfolio matrix in sheet order into committee_portfolios.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9a3b2c4d5f6'
down_revision: Union[str, Sequence[str], None] = 'd8f2a1b3c4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EVENT_NAME = 'Mumbai MUN 2026'

DISEC_PORTFOLIOS: list[tuple[str, str]] = [
    ('United States of America', 'us'),
    ("People's Republic of China", 'cn'),
    ('Russian Federation', 'ru'),
    ('United Kingdom', 'gb'),
    ('Ukraine', 'ua'),
    ('India', 'in'),
    ('Israel', 'il'),
    ('Iran', 'ir'),
    ('Republic of Korea', 'kr'),
    ("Democratic People's Republic of Korea", 'kp'),
    ('Pakistan', 'pk'),
    ('France', 'fr'),
    ('Lebanon', 'lb'),
    ('Yemen', 'ye'),
    ('Sudan', 'sd'),
    ('South Sudan', 'ss'),
    ('Libya', 'ly'),
    ('Germany', 'de'),
    ('Nigeria', 'ng'),
    ('Saudi Arabia', 'sa'),
    ('Venezuela', 've'),
    ('Türkiye', 'tr'),
    ('United Arab Emirates', 'ae'),
    ('Egypt', 'eg'),
    ('Japan', 'jp'),
    ('Brazil', 'br'),
    ('Argentina', 'ar'),
    ('Cuba', 'cu'),
    ('Ivory Coast', 'ci'),
    ('Burkina Faso', 'bf'),
    ('Colombia', 'co'),
    ('Peru', 'pe'),
    ('Australia', 'au'),
]


def upgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            "INSERT INTO committees (event_id, name) "
            "SELECT e.id, CAST(:name AS varchar) FROM events e "
            "WHERE e.name = CAST(:event AS varchar) "
            "AND NOT EXISTS (SELECT 1 FROM committees c "
            "WHERE c.event_id = e.id AND c.name = CAST(:name AS varchar)) "
            "ORDER BY e.id LIMIT 1"
        ),
        {'name': 'DISEC', 'event': EVENT_NAME},
    )

    for position, (name, code) in enumerate(DISEC_PORTFOLIOS):
        bind.execute(
            sa.text(
                "INSERT INTO committee_portfolios (committee_id, position, name, code, tag, detail) "
                "SELECT c.id, :position, CAST(:name AS varchar), CAST(:code AS varchar), NULL, NULL "
                "FROM committees c JOIN events e ON e.id = c.event_id "
                "WHERE e.name = CAST(:event AS varchar) AND c.name = 'DISEC' "
                "AND NOT EXISTS (SELECT 1 FROM committee_portfolios p "
                "WHERE p.committee_id = c.id AND p.name = CAST(:name AS varchar))"
            ),
            {
                'position': position,
                'name': name,
                'code': code,
                'event': EVENT_NAME,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM committee_portfolios "
            "WHERE committee_id IN ("
            "SELECT c.id FROM committees c JOIN events e ON e.id = c.event_id "
            "WHERE e.name = CAST(:event AS varchar) AND c.name = 'DISEC'"
            ")"
        ),
        {'event': EVENT_NAME},
    )
