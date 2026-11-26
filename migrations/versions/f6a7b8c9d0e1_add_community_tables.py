"""add community tables

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-11-24 12:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'points_ledger',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('delta', sa.Integer(), nullable=False),
        sa.Column('balance_after', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=64), nullable=False),
        sa.Column('reference_type', sa.String(length=32), server_default='', nullable=False),
        sa.Column('reference_id', sa.Uuid(), nullable=True),
        sa.Column('idempotency_key', sa.String(length=191), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_points_ledger_user_id', 'points_ledger', ['user_id'], unique=False)
    op.create_index('ix_points_ledger_idempotency_key', 'points_ledger', ['idempotency_key'], unique=True)
    op.create_index('ix_points_ledger_created_at', 'points_ledger', ['created_at'], unique=False)

    op.create_table(
        'invitations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('invited_by', sa.Uuid(), nullable=False),
        sa.Column('invitee_email', sa.String(length=320), server_default='', nullable=False),
        sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
        sa.Column('points_reserved', sa.Integer(), server_default='0', nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('accepted_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['invited_by'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_invitations_code_hash', 'invitations', ['code_hash'], unique=True)
    op.create_index('ix_invitations_invited_by', 'invitations', ['invited_by'], unique=False)
    op.create_index('ix_invitations_status', 'invitations', ['status'], unique=False)
    op.create_index('ix_invitations_created_at', 'invitations', ['created_at'], unique=False)

    op.create_table(
        'registration_applications',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('display_name', sa.String(length=100), server_default='', nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('invite_id', sa.Uuid(), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
        sa.Column('decided_by', sa.Uuid(), nullable=True),
        sa.Column('decision_note', sa.String(length=500), server_default='', nullable=False),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['invite_id'], ['invitations.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['decided_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_registration_applications_email', 'registration_applications', ['email'], unique=True)
    op.create_index('ix_registration_applications_status', 'registration_applications', ['status'], unique=False)
    op.create_index('ix_registration_applications_created_at', 'registration_applications', ['created_at'], unique=False)

    op.create_table(
        'contributions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), server_default='', nullable=False),
        sa.Column('payload', sa.Text(), server_default='', nullable=False),
        sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
        sa.Column('points_reward', sa.Integer(), server_default='0', nullable=False),
        sa.Column('reviewed_by', sa.Uuid(), nullable=True),
        sa.Column('review_note', sa.String(length=500), server_default='', nullable=False),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_contributions_user_id', 'contributions', ['user_id'], unique=False)
    op.create_index('ix_contributions_status', 'contributions', ['status'], unique=False)
    op.create_index('ix_contributions_created_at', 'contributions', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_contributions_created_at', table_name='contributions')
    op.drop_index('ix_contributions_status', table_name='contributions')
    op.drop_index('ix_contributions_user_id', table_name='contributions')
    op.drop_table('contributions')
    op.drop_index('ix_registration_applications_created_at', table_name='registration_applications')
    op.drop_index('ix_registration_applications_status', table_name='registration_applications')
    op.drop_index('ix_registration_applications_email', table_name='registration_applications')
    op.drop_table('registration_applications')
    op.drop_index('ix_invitations_created_at', table_name='invitations')
    op.drop_index('ix_invitations_status', table_name='invitations')
    op.drop_index('ix_invitations_invited_by', table_name='invitations')
    op.drop_index('ix_invitations_code_hash', table_name='invitations')
    op.drop_table('invitations')
    op.drop_index('ix_points_ledger_created_at', table_name='points_ledger')
    op.drop_index('ix_points_ledger_idempotency_key', table_name='points_ledger')
    op.drop_index('ix_points_ledger_user_id', table_name='points_ledger')
    op.drop_table('points_ledger')
