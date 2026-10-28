"""Add the CCC portfolio matrix (24 historical figures with placard flags) and fix DISEC's Turkiye name

Revision ID: f1b7c3d9e2a4
Revises: e9a3b2c4d5f6
Create Date: 2026-10-24 00:00:00.000000

- Seeds CCC's 24-portfolio matrix in sheet order into committee_portfolios with their
  respective placard flag codes.
- Rewrites DISEC's Turkiye row (flag 'tr') with a proper u-umlaut, in case the earlier
  seed stored it mis-encoded.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1b7c3d9e2a4'
down_revision: Union[str, Sequence[str], None] = 'e9a3b2c4d5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EVENT_NAME = 'Mumbai MUN 2026'

CCC_PORTFOLIOS: list[tuple[str, str]] = [
    ('Cornell Franklin', 'us'),
    ('Clarence Edward Gauss', 'us'),
    ('Stirling Fessenden', 'us'),
    ('Addie Viola Smith', 'us'),
    ('Sir William Johnston Keswick', 'gb'),
    ('Kenneth Bourne', 'gb'),
    ('Frederick Wernham Gerrard', 'gb'),
    ('Sir Herbert Phillips', 'gb'),
    ('Frederick Maze', 'gb'),
    ('Victor Sassoon', 'gb'),
    ('Liu Xiao', 'cn-ccp'),
    ('Pan Hannian', 'tw'),
    ('Liu Changsheng', 'cn-ccp'),
    ('KP Chen', 'tw'),
    ('Du Yuesheng', 'tw'),
    ('Yu Xiaqing', 'tw'),
    ('Huang Jinrong', 'tw'),
    ("Fu Xiao'an", 'tw'),
    ('Li Shiqun', 'tw'),
    ('Dai Li', 'tw-kmt'),
    ('Ding Mocun', 'tw'),
    ('Marcel Baudez', 'fr'),
    ('Robert Jacquinot de Besange', 'fr'),
    ('Dr. Oskar Trautmann', 'de-imp'),
]

TURKIYE = 'T\u00fcrkiye'


def upgrade() -> None:
    bind = op.get_bind()

    for position, (name, code) in enumerate(CCC_PORTFOLIOS):
        bind.execute(
            sa.text(
                "INSERT INTO committee_portfolios (committee_id, position, name, code, tag, detail) "
                "SELECT c.id, :position, CAST(:name AS varchar), CAST(:code AS varchar), NULL, NULL "
                "FROM committees c JOIN events e ON e.id = c.event_id "
                "WHERE e.name = CAST(:event AS varchar) AND c.name = 'CCC' "
                "AND NOT EXISTS (SELECT 1 FROM committee_portfolios p "
                "WHERE p.committee_id = c.id AND p.name = CAST(:name AS varchar))"
            ),
            {'position': position, 'name': name, 'code': code, 'event': EVENT_NAME},
        )
        bind.execute(
            sa.text(
                "UPDATE committee_portfolios SET code = CAST(:code AS varchar) "
                "WHERE name = CAST(:name AS varchar) AND (code IS NULL OR code <> CAST(:code AS varchar)) "
                "AND committee_id IN (SELECT c.id FROM committees c "
                "JOIN events e ON e.id = c.event_id "
                "WHERE e.name = CAST(:event AS varchar) AND c.name = 'CCC')"
            ),
            {'name': name, 'code': code, 'event': EVENT_NAME},
        )

    bind.execute(
        sa.text(
            "UPDATE committee_portfolios SET name = CAST(:name AS varchar) "
            "WHERE code = 'tr' AND name <> CAST(:name AS varchar) "
            "AND committee_id IN (SELECT c.id FROM committees c "
            "JOIN events e ON e.id = c.event_id "
            "WHERE e.name = CAST(:event AS varchar) AND c.name = 'DISEC')"
        ),
        {'name': TURKIYE, 'event': EVENT_NAME},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM committee_portfolios "
            "WHERE committee_id IN (SELECT c.id FROM committees c "
            "JOIN events e ON e.id = c.event_id "
            "WHERE e.name = CAST(:event AS varchar) AND c.name = 'CCC')"
        ),
        {'event': EVENT_NAME},
    )
