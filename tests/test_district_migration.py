"""Регрессии recovery-safe миграции административных районов."""
import importlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

import districts


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIOR_REVISION = 'a49c7d8e2f10'
DISTRICT_REVISION = 'c82f4b10a6d1'
MIGRATION_MODULE = (
    'migrations.versions.c82f4b10a6d1_административные_районы'
)


def _upgrade(database_path, revision, *, auto_migrate=False):
    env = os.environ.copy()
    env.update({
        'DATABASE_URL': f'sqlite:///{database_path}',
        'AUTO_MIGRATE': 'true' if auto_migrate else 'false',
        'RATELIMIT_ENABLED': 'false',
        'SESSION_COOKIE_SECURE': 'false',
        'SECRET_KEY': 'district-migration-test',
        'YANDEX_MAPS_API_KEY': '',
    })
    subprocess.run(
        [
            sys.executable, '-m', 'flask', '--app', 'app.py',
            'db', 'upgrade', revision,
        ],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _seed_verified_points(connection):
    point = districts.district_map()['tsentralny'].geometry.representative_point()
    latitude, longitude = point.y, point.x
    connection.executemany(
        """
        INSERT INTO verified_points
            (id, latitude, longitude, lat_key, lon_key, dist_from_center_km)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (1, latitude, longitude, 1, 1, 0.1),
            (2, latitude, longitude, 2, 2, 0.1),
        ],
    )


@pytest.mark.parametrize(
    'partial_state',
    ['game_column', 'first_table', 'both_columns', 'schema_complete'],
)
def test_upgrade_recovers_from_partially_applied_sqlite_ddl(
        tmp_path, partial_state):
    database_path = tmp_path / f'{partial_state}.db'
    _upgrade(database_path, PRIOR_REVISION)

    with sqlite3.connect(database_path) as connection:
        _seed_verified_points(connection)
        # Имитируем обрыв после DDL, но до обновления
        # alembic_version, на нескольких возможных границах шага.
        connection.execute(
            'ALTER TABLE game_sessions ADD COLUMN district_id VARCHAR(32)'
        )
        if partial_state != 'game_column':
            connection.execute(
                'CREATE INDEX ix_game_sessions_district_id '
                'ON game_sessions (district_id)'
            )
        if partial_state in ('both_columns', 'schema_complete'):
            # Более поздний обрыв: вторая колонка есть,
            # её индекса ещё нет, а часть строк уже заполнена.
            connection.execute(
                'ALTER TABLE verified_points '
                'ADD COLUMN district_id VARCHAR(32)'
            )
            connection.execute(
                "UPDATE verified_points SET district_id = 'kurortny' WHERE id = 2"
            )
            if partial_state == 'schema_complete':
                connection.execute(
                    'CREATE INDEX ix_verified_points_district_id '
                    'ON verified_points (district_id)'
                )

    _upgrade(database_path, 'head')

    engine = sa.create_engine(f'sqlite:///{database_path}')
    inspector = sa.inspect(engine)
    for table_name in ('game_sessions', 'verified_points'):
        assert 'district_id' in {
            column['name'] for column in inspector.get_columns(table_name)
        }
        assert f'ix_{table_name}_district_id' in {
            index['name'] for index in inspector.get_indexes(table_name)
        }

    with engine.connect() as connection:
        assert connection.execute(
            sa.text('SELECT version_num FROM alembic_version')
        ).scalar_one() == DISTRICT_REVISION
        district_ids = connection.execute(
            sa.text('SELECT id, district_id FROM verified_points ORDER BY id')
        ).all()

    assert district_ids[0] == (1, 'tsentralny')
    if partial_state in ('both_columns', 'schema_complete'):
        # Backfill не переклассифицирует уже обработанную строку.
        assert district_ids[1] == (2, 'kurortny')
    else:
        assert district_ids[1] == (2, 'tsentralny')
    engine.dispose()


def test_dataset_is_validated_before_any_ddl(tmp_path, monkeypatch):
    database_path = tmp_path / 'invalid-dataset.db'
    _upgrade(database_path, PRIOR_REVISION)
    engine = sa.create_engine(f'sqlite:///{database_path}')

    def reject_dataset():
        raise districts.DistrictDataError('test: invalid canonical dataset')

    monkeypatch.setattr(districts, 'load_districts', reject_dataset)
    migration = importlib.import_module(MIGRATION_MODULE)

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            with pytest.raises(districts.DistrictDataError):
                migration.upgrade()

    inspector = sa.inspect(engine)
    for table_name in ('game_sessions', 'verified_points'):
        assert 'district_id' not in {
            column['name'] for column in inspector.get_columns(table_name)
        }
        assert f'ix_{table_name}_district_id' not in {
            index['name'] for index in inspector.get_indexes(table_name)
        }
    with engine.connect() as connection:
        assert connection.execute(
            sa.text('SELECT version_num FROM alembic_version')
        ).scalar_one() == PRIOR_REVISION
    engine.dispose()


def test_legacy_database_without_alembic_is_stamped_and_upgraded(tmp_path):
    database_path = tmp_path / 'legacy.db'
    _upgrade(database_path, '5f7875fb0c81')
    with sqlite3.connect(database_path) as connection:
        connection.execute('DROP TABLE alembic_version')
        connection.execute("INSERT INTO game_sessions (id, player_name) VALUES (1, 'Legacy')")

    _upgrade(database_path, 'head', auto_migrate=True)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('SELECT version_num FROM alembic_version').fetchone() == (
            DISTRICT_REVISION,
        )
        assert connection.execute('SELECT player_name FROM game_sessions').fetchone() == ('Legacy',)
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
