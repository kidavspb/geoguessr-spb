"""Порции поиска панорам с сохранением версии точки

Revision ID: 972e240b1d4d
Revises: c82f4b10a6d1
Create Date: 2026-09-26 15:59:22.774891

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '972e240b1d4d'
down_revision = 'c82f4b10a6d1'
branch_labels = None
depends_on = None


def upgrade():
    # Старые раунды сохраняют skips и получают первую серию. Повтор после
    # частично выполненного SQLite DDL безопасен и не сбрасывает новые серии.
    columns = sa.inspect(op.get_bind()).get_columns('game_rounds')
    if not any(column['name'] == 'search_batch' for column in columns):
        op.add_column('game_rounds', sa.Column(
            'search_batch', sa.Integer(), server_default='1', nullable=False,
        ))


def downgrade():
    with op.batch_alter_table('game_rounds', schema=None) as batch_op:
        batch_op.drop_column('search_batch')
