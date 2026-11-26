"""add user verification and points columns

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-11-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('email_verified_at', sa.DateTime(), nullable=True))
        batch.add_column(sa.Column('points_balance', sa.Integer(), server_default='0', nullable=False))
        batch.add_column(sa.Column('points_reserved', sa.Integer(), server_default='0', nullable=False))
        batch.add_column(sa.Column('points_lifetime', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('points_lifetime')
        batch.drop_column('points_reserved')
        batch.drop_column('points_balance')
        batch.drop_column('email_verified_at')
