"""надёжная загрузка панорам, источник точки и клиентские метрики

Revision ID: a49c7d8e2f10
Revises: 736ef40ee59e
Create Date: 2026-08-02 20:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a49c7d8e2f10'
down_revision = '736ef40ee59e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('game_rounds', schema=None) as batch_op:
        batch_op.add_column(sa.Column('location_source', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('panorama_lookup_ms', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('panorama_ready_ms', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('panorama_attempts', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('panorama_status', sa.String(length=24), nullable=True))
        batch_op.create_unique_constraint(
            'uq_game_round_session_round', ['session_id', 'round_number'])


def downgrade():
    with op.batch_alter_table('game_rounds', schema=None) as batch_op:
        batch_op.drop_constraint('uq_game_round_session_round', type_='unique')
        batch_op.drop_column('panorama_status')
        batch_op.drop_column('panorama_attempts')
        batch_op.drop_column('panorama_ready_ms')
        batch_op.drop_column('panorama_lookup_ms')
        batch_op.drop_column('location_source')
