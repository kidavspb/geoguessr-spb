"""E2E: полный игровой цикл в реальном браузере (Chromium + заглушка карт).

Ловит регрессии фронтенда, которые не видны юнит-тестам бэкенда:
несуществующие элементы, сломанные обработчики, порядок экранов.
"""
import re

import pytest

try:
    import playwright  # noqa: F401
except ImportError:
    pytest.skip('playwright не установлен', allow_module_level=True)

from playwright.sync_api import expect


def _play_round(page):
    """Дождаться панорамы, кликнуть по карте, ответить."""
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)

    page.locator('#map').click(position={'x': 150, 'y': 120})
    expect(page.locator('#guess-btn')).to_be_enabled()
    page.locator('#guess-btn').click()

    expect(page.locator('#result-screen')).to_have_class(re.compile('active'), timeout=10000)


def test_full_game_flow(page, server):
    # Считаем «боевые» запросы точки раунда: их должно быть ровно по одному
    # на раунд. Дубли означают двойной обработчик «Продолжить» и гонку двух
    # загрузок панорамы (реальный баг: зависание на переходе раунда).
    location_requests = []
    page.on('request', lambda req: location_requests.append(req.url)
            if '/api/game/location' in req.url and 'peek' not in req.url else None)

    page.goto(server)
    expect(page).to_have_title(re.compile('Петербургский следопыт'))

    # Стартовый экран: все контролы на месте
    expect(page.locator('#start-btn')).to_be_visible()
    expect(page.locator('#daily-btn')).to_be_visible()
    expect(page.locator('#move-group')).to_be_visible()

    page.locator('#player-name').fill('E2E-игрок')
    page.locator('#start-btn').click()

    expect(page.locator('#game-screen')).to_have_class(re.compile('active'), timeout=10000)

    for round_no in range(1, 6):
        expect(page.locator('#current-round')).to_have_text(str(round_no), timeout=10000)
        _play_round(page)

        # Адрес из клиентского геокодера-заглушки
        expect(page.locator('#correct-location-name')).to_have_text('Тестовая улица, 1', timeout=5000)

        if round_no < 5:
            page.locator('#next-round-btn').click()
            expect(page.locator('#game-screen')).to_have_class(re.compile('active'))

    # Финал
    page.locator('#next-round-btn').click()
    expect(page.locator('#final-screen')).to_have_class(re.compile('active'), timeout=10000)
    expect(page.locator('#final-score')).not_to_have_text('0')
    expect(page.locator('#rounds-summary .round-item')).to_have_count(5)
    expect(page.locator('#challenge-btn')).to_be_visible()

    # Таблица лидеров: игрок попал в топ
    page.locator('#final-leaderboard-btn').click()
    expect(page.locator('#leaderboard-screen')).to_have_class(re.compile('active'))
    expect(page.locator('.leaderboard-table')).to_contain_text('E2E-игрок')

    # Ровно один запрос точки на раунд — без дублей от двойных обработчиков
    assert len(location_requests) == 5, f'ожидалось 5 запросов раунда, было {len(location_requests)}'


def test_no_move_round_and_map_toggle(page, server):
    # Мобильная ширина: «ручка» панели карты существует только там
    page.set_viewport_size({'width': 420, 'height': 820})
    page.goto(server)

    # Включаем «не сходя с места» и играем один раунд
    page.locator('.move-btn[data-move="no"]').click()
    page.locator('#start-btn').click()
    expect(page.locator('#game-screen')).to_have_class(re.compile('active'), timeout=10000)
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)

    # Ручка «нижнего листа» сворачивает и разворачивает панель карты
    page.locator('#map-handle').click()
    expect(page.locator('#map-panel')).to_have_class(re.compile('collapsed'))
    page.locator('#map-handle').click()
    expect(page.locator('#map-panel')).not_to_have_class(re.compile('collapsed'))

    _play_round(page)
    expect(page.locator('#result-score')).to_be_visible()

