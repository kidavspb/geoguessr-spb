"""Version panorama rules for active games

Revision ID: 7cc7dd2165af
Revises: 972e240b1d4d
Create Date: 2026-09-30 14:04:11.194155

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7cc7dd2165af'
down_revision = '972e240b1d4d'
branch_labels = None
depends_on = None


def upgrade():
    # Старые игры и INSERT от ещё не перезапущенного worker сохраняют прежние
    # правила. Новое приложение явно выставляет актуальную версию при старте.
    # SQLite может сохранить ADD COLUMN до обновления alembic_version: повтор
    # должен оставить нетронутыми уже существующие значения 0 и 1.
    columns = sa.inspect(op.get_bind()).get_columns('game_sessions')
    if not any(column['name'] == 'panorama_rules_version' for column in columns):
        op.add_column('game_sessions', sa.Column(
            'panorama_rules_version', sa.Integer(),
            server_default='0', nullable=False,
        ))


def downgrade():
    with op.batch_alter_table('game_sessions', schema=None) as batch_op:
        batch_op.drop_column('panorama_rules_version')
