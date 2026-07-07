"""Общие фикстуры для тестов.

Приложение настраивается на отдельную временную SQLite-базу через DATABASE_URL,
чтобы тесты ни при каких условиях не трогали рабочую БД. Переменные окружения
выставляются ДО импорта app (URI базы и флаги читаются на этапе импорта).
"""
import os
import importlib

import pytest


@pytest.fixture(scope='session')
def app_module(tmp_path_factory):
    db_path = tmp_path_factory.mktemp('db') / 'test.db'
    os.environ['DATABASE_URL'] = f'sqlite:///{db_path}'
    os.environ['SESSION_COOKIE_SECURE'] = 'false'  # тестовый клиент ходит по http
    os.environ['SECRET_KEY'] = 'test-secret-key'
    os.environ['RATELIMIT_ENABLED'] = 'false'      # лимиты мешали бы циклам в тестах
    # Схему в тестах создают фикстуры (db.create_all), миграции не нужны
    os.environ['AUTO_MIGRATE'] = 'false'
    # Пустой ключ отключает обратное геокодирование: тесты не ходят в сеть
    os.environ['YANDEX_GEOCODER_API_KEY'] = ''
    os.environ['YANDEX_MAPS_API_KEY'] = ''
    # Известный ключ админки для тестов модерации (не из .env)
    os.environ['ADMIN_KEY'] = 'test-admin-key'

    import app as app_module
    # На случай, если модуль уже был импортирован другим тестовым прогоном.
    importlib.reload(app_module)
    app_module.app.config.update(TESTING=True)
    return app_module


@pytest.fixture()
def app(app_module):
    """Свежая схема БД на каждый тест — полная изоляция."""
    with app_module.app.app_context():
        app_module.db.drop_all()
        app_module.db.create_all()
    return app_module.app


@pytest.fixture()
def client(app):
    return app.test_client()
