"""/healthz подтверждает deploy только при живой БД и совпадающей схеме."""
import pytest
from sqlalchemy import text


@pytest.fixture()
def alembic_version(app, app_module):
    """Таблица версии, как после flask db upgrade (в тестах схему создаёт create_all)."""
    db = app_module.db

    def stamp(revision):
        with app.app_context():
            db.session.execute(text('DELETE FROM alembic_version'))
            db.session.execute(text('INSERT INTO alembic_version VALUES (:r)'), {'r': revision})
            db.session.commit()

    with app.app_context():
        db.session.execute(text(
            'CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)'
        ))
        db.session.commit()
    yield stamp
    with app.app_context():
        db.session.execute(text('DROP TABLE IF EXISTS alembic_version'))
        db.session.commit()


def test_healthz_ok_when_schema_matches_code(client, app_module, alembic_version):
    alembic_version(app_module.CODE_SCHEMA_REVISION)
    response = client.get('/healthz')
    assert response.status_code == 200
    assert response.get_json()['status'] == 'ok'


def test_healthz_fails_when_migration_is_missing(client, alembic_version):
    alembic_version('5f7875fb0c81')
    response = client.get('/healthz')
    assert response.status_code == 503
    assert response.get_json()['db_revision'] == '5f7875fb0c81'


def test_healthz_fails_without_alembic_version(client):
    assert client.get('/healthz').status_code == 503


def test_code_schema_revision_is_the_migration_head(app_module):
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory(app_module.MIGRATIONS_DIR).get_heads()
    assert heads == [app_module.CODE_SCHEMA_REVISION]
