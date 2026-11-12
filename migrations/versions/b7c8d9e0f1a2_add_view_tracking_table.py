"""add view tracking table

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-11-06 18:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'views',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('visitor_id', sa.String(length=64), nullable=False),
        sa.Column('movie_id', sa.Uuid(), nullable=True),
        sa.Column('series_id', sa.Uuid(), nullable=True),
        sa.Column('episode_id', sa.Uuid(), nullable=True),
        sa.Column('user_agent', sa.String(length=500), server_default='', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['movie_id'], ['movies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['series_id'], ['series.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['episode_id'], ['episodes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_views_visitor_id'), 'views', ['visitor_id'], unique=False)
    op.create_index(op.f('ix_views_movie_id'), 'views', ['movie_id'], unique=False)
    op.create_index(op.f('ix_views_series_id'), 'views', ['series_id'], unique=False)
    op.create_index(op.f('ix_views_episode_id'), 'views', ['episode_id'], unique=False)
    op.create_index(op.f('ix_views_created_at'), 'views', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_views_created_at'), table_name='views')
    op.drop_index(op.f('ix_views_episode_id'), table_name='views')
    op.drop_index(op.f('ix_views_series_id'), table_name='views')
    op.drop_index(op.f('ix_views_movie_id'), table_name='views')
    op.drop_index(op.f('ix_views_visitor_id'), table_name='views')
    op.drop_table('views')
