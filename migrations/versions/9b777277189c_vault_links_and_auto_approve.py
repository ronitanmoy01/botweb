"""vault_links_and_auto_approve

Revision ID: 9b777277189c
Revises: f6a7b8c9d0e1
Create Date: 2026-11-25 22:18:40.608916

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b777277189c'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('vault_links',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('movie_id', sa.Uuid(), nullable=True),
    sa.Column('series_id', sa.Uuid(), nullable=True),
    sa.Column('url', sa.String(length=500), nullable=False),
    sa.Column('label', sa.String(length=100), server_default='', nullable=False),
    sa.Column('kind', sa.String(length=16), server_default='file', nullable=False),
    sa.Column('added_by', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
    sa.Column('points_reward', sa.Integer(), server_default='0', nullable=False),
    sa.Column('reviewed_by', sa.Uuid(), nullable=True),
    sa.Column('review_note', sa.String(length=500), server_default='', nullable=False),
    sa.Column('reviewed_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['added_by'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['movie_id'], ['movies.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['series_id'], ['series.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_vault_links_added_by'), 'vault_links', ['added_by'], unique=False)
    op.create_index(op.f('ix_vault_links_created_at'), 'vault_links', ['created_at'], unique=False)
    op.create_index(op.f('ix_vault_links_movie_id'), 'vault_links', ['movie_id'], unique=False)
    op.create_index(op.f('ix_vault_links_series_id'), 'vault_links', ['series_id'], unique=False)
    op.create_index(op.f('ix_vault_links_status'), 'vault_links', ['status'], unique=False)
    op.add_column('users', sa.Column('can_auto_approve', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'can_auto_approve')
    op.drop_index(op.f('ix_vault_links_status'), table_name='vault_links')
    op.drop_index(op.f('ix_vault_links_series_id'), table_name='vault_links')
    op.drop_index(op.f('ix_vault_links_movie_id'), table_name='vault_links')
    op.drop_index(op.f('ix_vault_links_created_at'), table_name='vault_links')
    op.drop_index(op.f('ix_vault_links_added_by'), table_name='vault_links')
    op.drop_table('vault_links')
