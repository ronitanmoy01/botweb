"""add email outbox and token tables

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-11-24 12:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'email_outbox',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('recipient', sa.String(length=320), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        sa.Column('template_name', sa.String(length=100), nullable=False),
        sa.Column('template_data', sa.Text(), server_default='', nullable=False),
        sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
        sa.Column('attempt_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('max_attempts', sa.Integer(), server_default='3', nullable=False),
        sa.Column('last_error', sa.Text(), server_default='', nullable=False),
        sa.Column('next_retry_at', sa.DateTime(), nullable=True),
        sa.Column('dedupe_key', sa.String(length=191), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_email_outbox_status', 'email_outbox', ['status'], unique=False)
    op.create_index('ix_email_outbox_created_at', 'email_outbox', ['created_at'], unique=False)
    op.create_index('ix_email_outbox_claim', 'email_outbox', ['status', 'next_retry_at'], unique=False)
    op.create_index('ix_email_outbox_dedupe_key', 'email_outbox', ['dedupe_key'], unique=True)

    op.create_table(
        'password_reset_tokens',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_password_reset_tokens_user_id', 'password_reset_tokens', ['user_id'], unique=False)
    op.create_index('ix_password_reset_tokens_token_hash', 'password_reset_tokens', ['token_hash'], unique=True)

    op.create_table(
        'email_verification_tokens',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_email_verification_tokens_user_id', 'email_verification_tokens', ['user_id'], unique=False)
    op.create_index('ix_email_verification_tokens_token_hash', 'email_verification_tokens', ['token_hash'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_email_verification_tokens_token_hash', table_name='email_verification_tokens')
    op.drop_index('ix_email_verification_tokens_user_id', table_name='email_verification_tokens')
    op.drop_table('email_verification_tokens')
    op.drop_index('ix_password_reset_tokens_token_hash', table_name='password_reset_tokens')
    op.drop_index('ix_password_reset_tokens_user_id', table_name='password_reset_tokens')
    op.drop_table('password_reset_tokens')
    op.drop_index('ix_email_outbox_dedupe_key', table_name='email_outbox')
    op.drop_index('ix_email_outbox_claim', table_name='email_outbox')
    op.drop_index('ix_email_outbox_created_at', table_name='email_outbox')
    op.drop_index('ix_email_outbox_status', table_name='email_outbox')
    op.drop_table('email_outbox')
