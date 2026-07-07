"""Фикстуры E2E: живой сервер приложения + браузер с заглушкой Яндекс.Карт.

Все запросы к api-maps.yandex.ru перехватываются и подменяются заглушкой
(tests/e2e/ymaps_stub.js) — тесты не ходят в сеть и не требуют ключей.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False

E2E_DIR = Path(__file__).parent
PROJECT_ROOT = E2E_DIR.parent.parent
PORT = 5688
BASE_URL = f'http://127.0.0.1:{PORT}'

pytestmark = pytest.mark.skipif(not HAVE_PLAYWRIGHT, reason='playwright не установлен')


def _wait_port(port, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                return True
        time.sleep(0.2)
    return False


@pytest.fixture(scope='session')
def server(tmp_path_factory):
    """Приложение как отдельный процесс на временной БД."""
    db_path = tmp_path_factory.mktemp('e2e-db') / 'e2e.db'
    env = {
        **os.environ,
        'DATABASE_URL': f'sqlite:///{db_path}',
        'SESSION_COOKIE_SECURE': 'false',
        'RATELIMIT_ENABLED': 'false',
        'AUTO_MIGRATE': 'true',
        'SECRET_KEY': 'e2e-secret',
        'YANDEX_MAPS_API_KEY': '',
        'YANDEX_GEOCODER_API_KEY': '',
        'METRIKA_ID': '',
        'PORT': str(PORT),
        'FLASK_DEBUG': 'false',
    }
    proc = subprocess.Popen(
        [sys.executable, 'app.py'],
        cwd=PROJECT_ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    assert _wait_port(PORT), 'сервер не поднялся'
    yield BASE_URL
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture(scope='session')
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture()
def page(browser, server):
    """Свежая страница с подменёнными Яндекс.Картами."""
    context = browser.new_context(viewport={'width': 1280, 'height': 800})
    page = context.new_page()

    stub_js = (E2E_DIR / 'ymaps_stub.js').read_text()
    page.route('**/api-maps.yandex.ru/**', lambda route: route.fulfill(
        status=200, content_type='application/javascript', body=stub_js))
    page.route('**/mc.yandex.ru/**', lambda route: route.abort())

    yield page
    context.close()
