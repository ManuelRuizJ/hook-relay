"""add idempotency key to events

Revision ID: 7c1e4a9d2b6f
Revises: 589249a68b3f
Create Date: 2026-11-14 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '7c1e4a9d2b6f'
down_revision: Union[str, None] = '589249a68b3f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Both columns are nullable: existing events keep NULL and are not
    # constrained (Postgres treats NULLs as distinct in UNIQUE).
    op.add_column(
        'events', sa.Column('idempotency_key', sa.String(length=255), nullable=True)
    )
    op.add_column(
        'events',
        sa.Column('request_fingerprint', sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        'uq_events_endpoint_id_idempotency_key',
        'events',
        ['endpoint_id', 'idempotency_key'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_events_endpoint_id_idempotency_key', 'events', type_='unique'
    )
    op.drop_column('events', 'request_fingerprint')
    op.drop_column('events', 'idempotency_key')
