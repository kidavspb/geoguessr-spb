"""административные районы

Revision ID: c82f4b10a6d1
Revises: a49c7d8e2f10
Create Date: 2026-09-01 22:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c82f4b10a6d1'
down_revision = 'a49c7d8e2f10'
branch_labels = None
depends_on = None


def _column_exists(bind, table_name, column_name):
    """Проверить колонку через общий SQLAlchemy Inspector."""
    return any(
        column['name'] == column_name
        for column in sa.inspect(bind).get_columns(table_name)
    )


def _index_exists(bind, table_name, index_name):
    """Проверить индекс без SQLite-специфичного SQL."""
    return any(
        index['name'] == index_name
        for index in sa.inspect(bind).get_indexes(table_name)
    )


def _ensure_district_column_and_index(bind, table_name):
    """Довести одну таблицу после любого частичного DDL-сбоя."""
    column_name = 'district_id'
    index_name = f'ix_{table_name}_{column_name}'

    if not _column_exists(bind, table_name, column_name):
        op.add_column(
            table_name,
            sa.Column(column_name, sa.String(length=32), nullable=True),
        )
    if not _index_exists(bind, table_name, index_name):
        op.create_index(index_name, table_name, [column_name], unique=False)


def upgrade():
    # GeoJSON нужен для backfill. Проверяем его до первого DDL,
    # чтобы битый/неполный deploy не оставлял частичную схему.
    from districts import (
        clear_district_caches, district_for_point, load_districts,
    )

    load_districts()

    bind = op.get_bind()
    # SQLite может сохранить DDL даже при обрыве migration до
    # обновления alembic_version. Каждый шаг поэтому проверяем
    # отдельно; Inspector также работает с PostgreSQL и другими СУБД.
    _ensure_district_column_and_index(bind, 'game_sessions')
    _ensure_district_column_and_index(bind, 'verified_points')

    # Принадлежность статической точки к району считаем один раз. Новые
    # точки получат district_id в pool.add_verified_point(). Точки вне
    # административной границы остаются NULL для legacy-режимов.
    points = sa.table(
        'verified_points',
        sa.column('id', sa.Integer),
        sa.column('latitude', sa.Float),
        sa.column('longitude', sa.Float),
        sa.column('district_id', sa.String),
    )
    rows = bind.execute(sa.select(
        points.c.id, points.c.latitude, points.c.longitude
    ).where(points.c.district_id.is_(None))).mappings().all()
    for row in rows:
        district_id = district_for_point(row['latitude'], row['longitude'])
        if district_id is not None:
            bind.execute(
                points.update()
                .where(
                    points.c.id == row['id'],
                    points.c.district_id.is_(None),
                )
                .values(district_id=district_id)
            )
    clear_district_caches()


def downgrade():
    with op.batch_alter_table('verified_points', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_verified_points_district_id'))
        batch_op.drop_column('district_id')

    with op.batch_alter_table('game_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_game_sessions_district_id'))
        batch_op.drop_column('district_id')
