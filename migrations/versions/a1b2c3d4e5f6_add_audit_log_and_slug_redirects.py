"""add audit log and slug redirects

Revision ID: a1b2c3d4e5f6
Revises: 0e20a4767de8
Create Date: 2026-11-06 11:57:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '0e20a4767de8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('actor_email', sa.String(length=320), nullable=False),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('target', sa.String(length=255), server_default='', nullable=False),
        sa.Column('detail', sa.String(length=500), server_default='', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_logs_created_at'), 'audit_logs', ['created_at'], unique=False)

    op.create_table(
        'slug_redirects',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('old_slug', sa.String(length=255), nullable=False),
        sa.Column('target_id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_slug_redirects_kind_old', 'slug_redirects', ['kind', 'old_slug'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_slug_redirects_kind_old', table_name='slug_redirects')
    op.drop_table('slug_redirects')
    op.drop_index(op.f('ix_audit_logs_created_at'), table_name='audit_logs')
    op.drop_table('audit_logs')
