"""widen box_office column for full box office text

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-11-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Widen box_office from String(50) to String(300)."""
    with op.batch_alter_table('movies') as batch:
        batch.alter_column('box_office', type_=sa.String(length=300), existing_type=sa.String(length=50), existing_nullable=False, existing_server_default='')


def downgrade() -> None:
    """Shrink box_office back to String(50)."""
    with op.batch_alter_table('movies') as batch:
        batch.alter_column('box_office', type_=sa.String(length=50), existing_type=sa.String(length=300), existing_nullable=False, existing_server_default='')
