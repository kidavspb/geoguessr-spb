"""Неудачи точек пула по играм

Revision ID: eea6d96ce6f6
Revises: 7cc7dd2165af
Create Date: 2026-10-20 19:21:36

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'eea6d96ce6f6'
down_revision = '7cc7dd2165af'
branch_labels = None
depends_on = None


def upgrade():
    # Игра последней неудачи: повтор от неё же fail_count не увеличивает.
    # NULL у существующих точек и у INSERT от ещё не перезапущенного worker
    # означает «неудач ни от какой игры не записано». SQLite может сохранить
    # ADD COLUMN до обновления alembic_version — повтор не должен падать.
    columns = sa.inspect(op.get_bind()).get_columns('verified_points')
    if not any(column['name'] == 'last_failed_game_id' for column in columns):
        op.add_column('verified_points', sa.Column(
            'last_failed_game_id', sa.Integer(), nullable=True,
        ))


def downgrade():
    with op.batch_alter_table('verified_points', schema=None) as batch_op:
        batch_op.drop_column('last_failed_game_id')
