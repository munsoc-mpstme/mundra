"""EB Tools: each committee's portfolio matrix

Revision ID: d8f2a1b3c4e5
Revises: c7e1f0a2b3d4
Create Date: 2026-10-21 00:00:00.000000

- committee_portfolios: the official portfolio matrix of a committee, in sheet order. EB
  Tools offers only these (plus a free "Add other"), and the server takes each portfolio's
  name and flag from here, so a typo or a wrong flag never reaches the smartboard.
- Seeds the Mumbai MUN 2026 matrices for UNSC, ECOSOC, UNICEF, UNODC, WTO and PSC into
  the seeded event (skipping committees that don't exist and rows already there). CCC has
  no matrix yet and IPC (the press corps) never has one: they keep free entry.

Names are as in the committee sheets, except UNICEF's "Equitorial Guinea", corrected to
"Equatorial Guinea". Countries carry their ISO 3166-1 alpha-2 flag code; PSC members carry
their party (detail) and its abbreviation (tag), shown as a badge instead of a flag.
To add CCC's matrix later: a new migration inserting its rows the same way.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8f2a1b3c4e5'
down_revision: Union[str, Sequence[str], None] = 'c7e1f0a2b3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EVENT_NAME = 'Mumbai MUN 2026'

# Flag code of every country name used in the matrices below (spellings vary by sheet).
CODES = {
    'Afghanistan': 'af', 'Angola': 'ao', 'Antigua and Barbuda': 'ag', 'Argentina': 'ar',
    'Australia': 'au', 'Bahrain': 'bh', 'Bangladesh': 'bd', 'Bolivia': 'bo',
    'Brazil': 'br', 'Burkina Faso': 'bf', 'Canada': 'ca', 'Chile': 'cl', 'China': 'cn',
    'Colombia': 'co', 'Costa Rica': 'cr', "Côte d'Ivoire": 'ci', 'Cyprus': 'cy',
    'DR Congo': 'cd', 'Democratic Republic of Congo': 'cd',
    'Democratic Republic of the Congo': 'cd', 'Denmark': 'dk', 'Dominican Republic': 'do',
    'Ecuador': 'ec', 'Egypt': 'eg', 'El Salvador': 'sv', 'Equatorial Guinea': 'gq',
    'Eritrea': 'er', 'Estonia': 'ee', 'Eswatini': 'sz', 'Ethiopia': 'et', 'Fiji': 'fj',
    'France': 'fr', 'Georgia': 'ge', 'Germany': 'de', 'Ghana': 'gh', 'Greece': 'gr',
    'Guatemala': 'gt', 'Haiti': 'ht', 'Iceland': 'is', 'India': 'in', 'Indonesia': 'id',
    'Iran': 'ir', 'Israel': 'il', 'Italy': 'it', 'Ivory Coast': 'ci', 'Jamaica': 'jm',
    'Japan': 'jp', 'Kazakhstan': 'kz', 'Kenya': 'ke', 'Kyrgyzstan': 'kg',
    "Lao People's Democratic Republic": 'la', 'Latvia': 'lv', 'Lebanon': 'lb',
    'Lesotho': 'ls', 'Liberia': 'lr', 'Liechtenstein': 'li', 'Luxembourg': 'lu',
    'Malaysia': 'my', 'Maldives': 'mv', 'Mauritania': 'mr', 'Mauritius': 'mu',
    'Mexico': 'mx', 'Morocco': 'ma', 'Nepal': 'np', 'New Zealand': 'nz', 'Nigeria': 'ng',
    'Norway': 'no', 'Oman': 'om', 'Pakistan': 'pk', 'Panama': 'pa',
    "People's Republic of China": 'cn', 'Peru': 'pe', 'Philippines': 'ph', 'Poland': 'pl',
    'Republic of Korea': 'kr', 'Russia': 'ru', 'Russian Federation': 'ru', 'Rwanda': 'rw',
    'Saudi Arabia': 'sa', 'Senegal': 'sn', 'Singapore': 'sg', 'Slovenia': 'si',
    'Somalia': 'so', 'South Africa': 'za', 'Spain': 'es', 'Sri Lanka': 'lk',
    'Sweden': 'se', 'Switzerland': 'ch', 'Tanzania': 'tz', 'The Netherlands': 'nl',
    'Tunisia': 'tn', 'Türkiye': 'tr', 'Turkmenistan': 'tm', 'Ukraine': 'ua',
    'United Arab Emirates': 'ae', 'United Kingdom': 'gb', 'United States': 'us',
    'United States of America': 'us', 'Uruguay': 'uy', 'Venezuela': 've', 'Vietnam': 'vn',
    'Yemen': 'ye',
}

COUNTRY_MATRICES = {
    'UNSC': [
        'China', 'France', 'Russian Federation', 'United Kingdom', 'United States of America',
        'Bahrain', 'Colombia', 'Democratic Republic of Congo', 'Denmark', 'Greece', 'Latvia',
        'Liberia', 'Pakistan', 'Panama', 'Somalia', 'Eritrea', 'Ethiopia', 'Iran', 'Israel',
        'United Arab Emirates',
    ],
    'ECOSOC': [
        'Argentina', 'Australia', 'Bangladesh', 'Brazil', 'Canada', 'Chile', 'Colombia',
        "Côte d'Ivoire", 'Democratic Republic of the Congo', 'Ecuador', 'Egypt', 'Fiji',
        'France', 'Germany', 'Haiti', 'India', 'Indonesia', 'Italy', 'Japan', 'Kenya',
        'Maldives', 'Mexico', 'Morocco', 'Nepal', 'Nigeria', 'Norway', 'Pakistan',
        "People's Republic of China", 'Peru', 'Philippines', 'Poland', 'Republic of Korea',
        'Rwanda', 'Saudi Arabia', 'Senegal', 'South Africa', 'Spain', 'Sri Lanka',
        'The Netherlands', 'Türkiye', 'United Arab Emirates', 'United Kingdom',
        'United States of America', 'Uruguay', 'Vietnam',
    ],
    'UNICEF': [
        'Antigua and Barbuda', 'Australia', 'Bolivia', 'Brazil', 'Burkina Faso', 'Canada',
        'China', 'Dominican Republic', 'El Salvador', 'Equatorial Guinea', 'Eritrea',
        'Estonia', 'Eswatini', 'France', 'Georgia', 'Germany', 'Guatemala', 'Iceland',
        'India', 'Indonesia', 'Israel', 'Japan', 'Kyrgyzstan',
        "Lao People's Democratic Republic", 'Lesotho', 'Liechtenstein', 'Mauritania',
        'Nigeria', 'Norway', 'Oman', 'Pakistan', 'Republic of Korea', 'Rwanda', 'Senegal',
        'Slovenia', 'Sweden', 'Switzerland', 'Tanzania', 'The Netherlands', 'Türkiye',
        'Turkmenistan', 'Ukraine', 'United Kingdom', 'United States of America', 'Yemen',
    ],
    'UNODC': [
        'Afghanistan', 'Angola', 'Australia', 'Brazil', 'Canada', 'Colombia', 'Cyprus',
        'Egypt', 'Equatorial Guinea', 'France', 'Germany', 'India', 'Indonesia', 'Iran',
        'Italy', 'Japan', 'Kazakhstan', 'Kenya', 'Lebanon', 'Luxembourg', 'Malaysia',
        'Mauritius', 'Mexico', 'Nigeria', 'Pakistan', 'Panama',
        "People's Republic of China", 'Peru', 'Philippines', 'Republic of Korea', 'Russia',
        'Saudi Arabia', 'Singapore', 'South Africa', 'Spain', 'Sweden', 'Switzerland',
        'The Netherlands', 'Tunisia', 'Türkiye', 'Ukraine', 'United Arab Emirates',
        'United Kingdom', 'United States', 'Venezuela',
    ],
    'WTO': [
        'Argentina', 'Australia', 'Bangladesh', 'Brazil', 'Canada', 'Chile', 'China',
        'Colombia', 'Costa Rica', 'DR Congo', 'Russian Federation', 'Egypt', 'France',
        'Germany', 'Ghana', 'India', 'Indonesia', 'Israel', 'Italy', 'Ivory Coast',
        'Jamaica', 'Japan', 'Kenya', 'Republic of Korea', 'Malaysia', 'Mexico', 'Morocco',
        'The Netherlands', 'New Zealand', 'Nigeria', 'Norway', 'Pakistan', 'Peru', 'Poland',
        'Rwanda', 'Saudi Arabia', 'Singapore', 'South Africa', 'Sri Lanka', 'Switzerland',
        'Türkiye', 'United Arab Emirates', 'United Kingdom', 'United States of America',
        'Vietnam',
    ],
}

# PSC (Standing Committee on Home Affairs): members, then the permanent special invitees
# (no voting rights), with their party. The tag is the party's usual abbreviation.
PARTY_TAGS = {
    'Indian National Congress': 'INC',
    'All India Trinamool Congress': 'AITC',
    'Bharatiya Janata Party': 'BJP',
    'Indian Union Muslim League': 'IUML',
    'Shiv Sena (Uddhav Balasaheb Thackeray)': 'SS(UBT)',
    'Aam Aadmi Party': 'AAP',
    'Rashtriya Janata Dal': 'RJD',
    'Aazad Samaj Party (Kanshi Ram)': 'ASP(KR)',
    'Dravida Munnetra Kazhagam': 'DMK',
    'Shiv Sena': 'SHS',
    'Nationalist Citizens Party of India': 'NCPI',
    'Telugu Desam Party': 'TDP',
    'Nationalist Congress Party – Sharadchandra Pawar': 'NCP(SP)',
    'Jammu & Kashmir National Conference': 'JKNC',
    'Communist Party of India (Marxist)': 'CPI(M)',
    'All India Majlis-e-Ittehadul Muslimeen': 'AIMIM',
}

PSC = [
    ('Ajay Maken', 'Indian National Congress'),
    ("Derek O'Brien", 'All India Trinamool Congress'),
    ('Dr. Radha Mohan Das Agrawal', 'Bharatiya Janata Party'),
    ('Haris Beeran', 'Indian Union Muslim League'),
    ('Neeraj Shekhar', 'Bharatiya Janata Party'),
    ('Samik Bhattacharya', 'Bharatiya Janata Party'),
    ('Sanjay Raut', 'Shiv Sena (Uddhav Balasaheb Thackeray)'),
    ('Sanjay Singh', 'Aam Aadmi Party'),
    ('Sanjay Yadav', 'Rashtriya Janata Dal'),
    ('Tarun Chugh', 'Bharatiya Janata Party'),
    ('Amrinder Singh Raja Warring', 'Indian National Congress'),
    ('Anil Yeshwant Desai', 'Shiv Sena (Uddhav Balasaheb Thackeray)'),
    ('Ashish Dubey', 'Bharatiya Janata Party'),
    ('B Y Raghavendra', 'Bharatiya Janata Party'),
    ('Chandra Shekhar', 'Aazad Samaj Party (Kanshi Ram)'),
    ('Dayanidhi Maran', 'Dravida Munnetra Kazhagam'),
    ('Delkar Kalaben Mohanbhai', 'Bharatiya Janata Party'),
    ('Dhairyasheel Sambhajirao Mane', 'Shiv Sena'),
    ('Dr. Kakoli Ghosh Dastidar', 'Nationalist Citizens Party of India'),
    ('Jai Parkash', 'Indian National Congress'),
    ('Jyotirmay Singh Mahato', 'Bharatiya Janata Party'),
    ('Kesineni Sivanath', 'Telugu Desam Party'),
    ('Krishna Prasad Tenneti', 'Telugu Desam Party'),
    ('Mala Roy', 'All India Trinamool Congress'),
    ('Muhammed Hamdullah Sayeed', 'Indian National Congress'),
    ('Naveen Jindal', 'Bharatiya Janata Party'),
    ('Priyanka Gandhi Vadra', 'Indian National Congress'),
    ('Satish Kumar Gautam', 'Bharatiya Janata Party'),
    ('Saumitra Khan', 'Bharatiya Janata Party'),
    ('Supriya Sule', 'Nationalist Congress Party – Sharadchandra Pawar'),
    ('Vishnu Dayal Ram', 'Bharatiya Janata Party'),
    ('Adhikarimayum Sharda Devi', 'Bharatiya Janata Party'),
    ('Brij Lal', 'Bharatiya Janata Party'),
    ('Chowdhury Mohammad Ramzan', 'Jammu & Kashmir National Conference'),
    ('John Brittas', 'Communist Party of India (Marxist)'),
    ('Manoj Kumar Jha', 'Rashtriya Janata Dal'),
    ('P. Chidambaram', 'Indian National Congress'),
    ('Renuka Chowdhury', 'Indian National Congress'),
    ('Asaduddin Owaisi', 'All India Majlis-e-Ittehadul Muslimeen'),
    ('Dr. Angomcha Bimol Akoijam', 'Indian National Congress'),
    ('Dr. Sambit Patra', 'Bharatiya Janata Party'),
    ('Gaurav Gogoi', 'Indian National Congress'),
    ('Mahua Moitra', 'All India Trinamool Congress'),
    ('Praveen Khandelwal', 'Bharatiya Janata Party'),
    ('Rahul Gandhi', 'Indian National Congress'),
]


def matrices() -> dict[str, list[tuple[str, str | None, str | None, str | None]]]:
    """{committee: [(name, code, tag, detail), ...]} in sheet order."""
    out = {
        committee: [(name, CODES[name], None, None) for name in names]
        for committee, names in COUNTRY_MATRICES.items()
    }
    out['PSC'] = [(name, None, PARTY_TAGS[party], party) for name, party in PSC]
    return out


def upgrade() -> None:
    op.create_table(
        'committee_portfolios',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('committee_id', sa.Integer(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('code', sa.String(), nullable=True),
        sa.Column('tag', sa.String(), nullable=True),
        sa.Column('detail', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['committee_id'], ['committees.id'], name=op.f('fk_committee_portfolios_committee_id_committees'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_committee_portfolios')),
        sa.UniqueConstraint('committee_id', 'name', name=op.f('uq_committee_portfolios_committee_id')),
    )
    op.create_index(op.f('ix_committee_portfolios_committee_id'), 'committee_portfolios', ['committee_id'], unique=False)

    bind = op.get_bind()
    for committee, rows in matrices().items():
        for position, (name, code, tag, detail) in enumerate(rows):
            bind.execute(
                sa.text(
                    "INSERT INTO committee_portfolios (committee_id, position, name, code, tag, detail) "
                    "SELECT c.id, :position, CAST(:name AS varchar), CAST(:code AS varchar), "
                    "CAST(:tag AS varchar), CAST(:detail AS varchar) "
                    "FROM committees c JOIN events e ON e.id = c.event_id "
                    "WHERE e.name = CAST(:event AS varchar) AND c.name = CAST(:committee AS varchar) "
                    "AND NOT EXISTS (SELECT 1 FROM committee_portfolios p "
                    "WHERE p.committee_id = c.id AND p.name = CAST(:name AS varchar))"
                ),
                {
                    'position': position, 'name': name, 'code': code, 'tag': tag,
                    'detail': detail, 'event': EVENT_NAME, 'committee': committee,
                },
            )


def downgrade() -> None:
    op.drop_index(op.f('ix_committee_portfolios_committee_id'), table_name='committee_portfolios')
    op.drop_table('committee_portfolios')
