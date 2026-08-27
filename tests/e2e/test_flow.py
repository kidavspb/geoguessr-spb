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


def test_start_settings_are_semantic_and_responsive(page, server):
    """Настройки работают как шкала, presets и switch, включая клавиатуру."""
    js_errors = []
    page.on('pageerror', lambda error: js_errors.append(str(error)))
    page.on('console', lambda message: js_errors.append(message.text)
            if message.type == 'error' else None)
    page.set_viewport_size({'width': 320, 'height': 780})
    page.goto(server)

    # Нативная семантика доступна не только мыши: range, radio group, switch.
    territory = page.get_by_role('slider', name='Территория')
    expect(territory).to_have_value('1')
    expect(territory).to_have_attribute('aria-valuetext', 'Средняя')
    expect(page.get_by_role('radio', name='Без лимита')).to_be_checked()
    movement = page.get_by_role('switch', name=re.compile('Можно перемещаться'))
    expect(movement).to_be_checked()

    # Клик по каждой подписи выбирает соответствующую дискретную позицию.
    for label, value in [('Центр', '0'), ('Средняя', '1'), ('Весь город', '2')]:
        page.get_by_role('button', name=label, exact=True).click()
        expect(territory).to_have_value(value)
        expect(page.get_by_role('button', name=label, exact=True)).to_have_attribute(
            'aria-pressed', 'true')

    # Сам track кликается, thumb перетаскивается и всегда snap'ится к шагу.
    box = territory.bounding_box()
    page.mouse.click(box['x'] + 2, box['y'] + box['height'] / 2)
    expect(territory).to_have_value('0')
    page.mouse.move(box['x'] + 10, box['y'] + box['height'] / 2)
    page.mouse.down()
    page.mouse.move(box['x'] + box['width'] - 10, box['y'] + box['height'] / 2)
    page.mouse.up()
    expect(territory).to_have_value('2')

    # Клавиши range и radio сохраняют ожидаемый порядок слева направо.
    territory.focus()
    page.keyboard.press('Home')
    expect(territory).to_have_value('0')
    page.keyboard.press('ArrowRight')
    expect(territory).to_have_value('1')
    page.keyboard.press('End')
    expect(territory).to_have_value('2')

    page.get_by_text('1 мин', exact=True).click()
    expect(page.get_by_role('radio', name='1 мин')).to_be_checked()
    page.get_by_text('3 мин', exact=True).click()
    expect(page.get_by_role('radio', name='3 мин')).to_be_checked()
    page.get_by_role('radio', name='3 мин').focus()
    page.keyboard.press('ArrowRight')
    expect(page.get_by_role('radio', name='Без лимита')).to_be_checked()

    movement.click()
    expect(movement).not_to_be_checked()
    expect(page.locator('#movement-description')).to_have_text(
        'Начальная точка обзора будет зафиксирована')
    movement.focus()
    page.keyboard.press('Space')
    expect(movement).to_be_checked()
    expect(page.locator('#movement-description')).to_have_text(
        'Свободно перемещайтесь по панораме')

    # На минимальной ширине нет горизонтального overflow, touch targets >= 44px.
    sizes = page.locator(
        '.territory-label, .segmented-control label, .move-setting'
    ).evaluate_all("elements => elements.map(el => el.getBoundingClientRect())")
    assert all(size['height'] >= 44 for size in sizes)
    for width, height in [(320, 780), (390, 844), (1280, 800)]:
        page.set_viewport_size({'width': width, 'height': height})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')

    # Основная комбинация доходит до прежнего API без нового механизма состояния.
    page.get_by_role('button', name='Центр', exact=True).click()
    page.get_by_text('1 мин', exact=True).click()
    movement.click()
    with page.expect_request(lambda request: request.url.endswith('/api/game/start')) as request_info:
        page.locator('#start-btn').click()
    payload = request_info.value.post_data_json
    assert payload['difficulty'] == 'center'
    assert payload['time_limit'] == 60
    assert payload['no_move'] is True
    expect(page.locator('#game-screen')).to_have_class(re.compile('active'), timeout=10000)
    assert js_errors == []


def test_full_game_flow(page, server):
    # Первый location приходит прямо из /start, последующие — из ответа guess.
    # Отдельные GET больше не нужны; их появление означает потерю prefetch-задачи.
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
    page.wait_for_function('window.__ymapsStats.mapsActive === 1')
    resource_stats = page.evaluate('window.__ymapsStats')
    assert resource_stats['panoramaPlayersActive'] == 0

    # Таблица лидеров: игрок попал в топ
    page.locator('#final-leaderboard-btn').click()
    expect(page.locator('#leaderboard-screen')).to_have_class(re.compile('active'))
    expect(page.locator('.leaderboard-table')).to_contain_text('E2E-игрок')
    assert page.evaluate('window.__ymapsStats.mapsActive') == 0

    assert len(location_requests) == 0, \
        f'ожидалось 0 дополнительных запросов раунда, было {len(location_requests)}'


def test_no_move_round_and_map_toggle(page, server):
    # Мобильная ширина: «ручка» панели карты существует только там
    page.set_viewport_size({'width': 420, 'height': 820})
    page.goto(server)

    # Включаем «не сходя с места» и играем один раунд
    page.get_by_role('switch', name=re.compile('Можно перемещаться')).click()
    page.locator('#start-btn').click()
    expect(page.locator('#game-screen')).to_have_class(re.compile('active'), timeout=10000)
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)

    # На мобильных раунд начинается со свёрнутой картой: первым делом игрок
    # осматривается, и панорама сразу на весь экран
    expect(page.locator('#map-panel')).to_have_class(re.compile('collapsed'))

    # Ручка «нижнего листа» разворачивает и сворачивает панель
    page.locator('#map-handle').click()
    expect(page.locator('#map-panel')).not_to_have_class(re.compile('collapsed'))
    page.locator('#map-handle').click()
    expect(page.locator('#map-panel')).to_have_class(re.compile('collapsed'))
    page.locator('#map-handle').click()

    _play_round(page)
    expect(page.locator('#result-score')).to_be_visible()

    # Адрес на результате — ссылка на панораму места в Яндекс Картах
    expect(page.locator('#correct-location-name')).to_have_attribute(
        'href', re.compile(r'yandex\.ru/maps/\?panorama'))


def test_fast_continue_reuses_inflight_prefetch(page, server):
    """Быстрый переход не запускает второй locate и скрытый Player."""
    page.add_init_script('window.__ymapsLocateDelay = 350')
    page.goto(server)
    page.locator('#start-btn').click()
    _play_round(page)

    # Нажимаем сразу после появления результата, пока locate следующего раунда
    # с искусственной задержкой почти наверняка ещё выполняется.
    page.locator('#next-round-btn').click()
    expect(page.locator('#current-round')).to_have_text('2', timeout=10000)
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)

    stats = page.evaluate('window.__ymapsStats')
    assert stats['locateCalls'] == 2  # первый раунд + один общий prefetch второго
    assert stats['panoramaPlayersActive'] == 1
    assert stats['panoramaPlayersCreated'] == 2


def test_missing_new_place_falls_back_without_reload(page, server):
    """Пустой locate исследовательской точки автоматически восстанавливает раунд."""
    page.add_init_script('window.__ymapsEmptyLocateCount = 1')
    page.goto(server)
    page.locator('#start-btn').click()

    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    stats = page.evaluate('window.__ymapsStats')
    assert stats['locateCalls'] == 2
    assert stats['panoramaPlayersActive'] == 1


def test_too_distant_nearest_panorama_is_not_used_as_the_answer(page, server):
    """Ближайшая, но далёкая съёмка не рассинхронизирует картинку и счёт."""
    page.add_init_script('window.__ymapsFarLocateCount = 1')
    page.goto(server)
    page.locator('#start-btn').click()

    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    stats = page.evaluate('window.__ymapsStats')
    assert stats['locateCalls'] == 2
    assert stats['panoramaPlayersActive'] == 1


def test_transient_panorama_error_retries_same_place_without_skip(page, server):
    """Транспортный сбой не меняет место и не помечает точку пула плохой."""
    page.add_init_script('window.__ymapsNetworkFailures = 1')
    skip_requests = []
    page.on('request', lambda req: skip_requests.append(req.url)
            if '/api/game/skip_location' in req.url else None)
    page.goto(server)
    page.locator('#start-btn').click()

    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    stats = page.evaluate('window.__ymapsStats')
    assert stats['locateCalls'] == 2
    assert skip_requests == []


def test_quick_modal_close_destroys_late_panorama_player(page, server):
    """Поздний Promise полноэкранного Player не оставляет WebGL-контекст."""
    page.goto(server)
    page.locator('#start-btn').click()
    _play_round(page)

    page.evaluate('window.__ymapsPlayerDelay = 300')
    page.locator('#result-pano').click()
    page.locator('#pano-modal-close').click()
    page.wait_for_timeout(500)

    stats = page.evaluate('window.__ymapsStats')
    assert stats['panoramaPlayersActive'] == 0


def test_slow_ready_response_does_not_block_fast_next_round(page, server):
    """Сетевое подтверждение таймера не держит уже видимый интерфейс."""
    page.add_init_script("""
        const originalFetch = window.fetch.bind(window);
        window.fetch = async (...args) => {
            const response = await originalFetch(...args);
            const url = String(args[0]);
            if (url.includes('/api/game/ready')) {
                await new Promise(resolve => setTimeout(resolve, 1500));
            }
            return response;
        };
    """)
    page.goto(server)
    page.locator('#start-btn').click()
    _play_round(page)

    page.locator('#next-round-btn').click()
    expect(page.locator('#current-round')).to_have_text('2', timeout=10000)
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
