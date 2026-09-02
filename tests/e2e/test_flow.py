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
    console_issues = []
    failed_resources = []
    page.on('pageerror', lambda error: console_issues.append(str(error)))
    page.on('console', lambda message: console_issues.append(message.text)
            if message.type in ('warning', 'error')
            and not message.text.startswith('Failed to load resource:') else None)
    page.on('response', lambda response: failed_resources.append(response.url)
            if response.status >= 400 else None)
    page.set_viewport_size({'width': 320, 'height': 780})
    page.goto(server)

    # Нативная семантика доступна не только мыши: range, radio group, switch.
    territory = page.get_by_role('slider', name='Территория')
    expect(territory).to_have_value('1')
    expect(territory).to_have_attribute('aria-valuetext', 'Средняя')
    expect(page.locator('#territory-value')).to_have_text('Средняя')
    expect(page.get_by_role('radio', name='Без лимита')).to_be_checked()
    movement = page.get_by_role('switch')
    expect(movement).to_be_checked()
    expect(page.locator('#movement-title')).to_have_text('Можно перемещаться')
    assert page.evaluate(
        "getComputedStyle(document.documentElement).backgroundColor"
    ) == 'rgb(35, 28, 98)'

    # Обычные reload получают согласованные HTML/CSS/JS без падения initToggles.
    for _ in range(3):
        page.reload()
        expect(territory).to_have_value('1')
        expect(page.locator('#territory-value')).to_have_text('Средняя')

    # Клик по каждой подписи выбирает соответствующую дискретную позицию.
    for label, value in [('Центр', '0'), ('Средняя', '1'), ('Весь город', '2')]:
        page.get_by_role('button', name=label, exact=True).click()
        expect(territory).to_have_value(value)
        expect(page.locator('#territory-value')).to_have_text(label)
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
    assert territory.evaluate(
        "element => getComputedStyle(element).webkitTapHighlightColor"
    ) == 'rgba(0, 0, 0, 0)'

    # Клавиши range и radio сохраняют ожидаемый порядок слева направо.
    territory.focus()
    page.keyboard.press('Home')
    expect(territory).to_have_value('0')
    assert territory.evaluate("element => element.matches(':focus-visible')")
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
    expect(page.locator('#movement-title')).to_have_text('Нельзя перемещаться')
    expect(page.locator('#movement-description')).to_have_text(
        'Начальная точка обзора зафиксирована')
    expect(page.locator('.move-emoji-fixed')).to_be_visible()
    assert movement.evaluate(
        "element => getComputedStyle(element).webkitTapHighlightColor"
    ) == 'rgba(0, 0, 0, 0)'
    movement.focus()
    page.keyboard.press('Space')
    expect(movement).to_be_checked()
    expect(page.locator('#movement-title')).to_have_text('Можно перемещаться')
    expect(page.locator('#movement-description')).to_have_text(
        'Свободно перемещайтесь по панораме')
    expect(page.locator('.move-emoji-walk')).to_be_visible()

    # Компактные подписи остаются удобны благодаря широким зонам по горизонтали;
    # основные controls сохраняют touch targets не меньше 44px.
    territory_label_sizes = page.locator('.territory-label').evaluate_all(
        "elements => elements.map(el => el.getBoundingClientRect())")
    assert all(size['height'] >= 32 for size in territory_label_sizes)
    control_sizes = page.locator(
        '.segmented-control label, .move-setting'
    ).evaluate_all("elements => elements.map(el => el.getBoundingClientRect())")
    assert all(size['height'] >= 44 for size in control_sizes)
    for width, height in [(320, 780), (390, 844), (430, 860), (1280, 800)]:
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
    # Лицензируемый ALS SPb на production установлен отдельно и
    # намеренно не входит в Git; CI проверяет системный fallback.
    assert all(url.endswith('/static/fonts/ALS_SPb.woff2') for url in failed_resources)
    assert console_issues == []


def test_district_picker_map_state_keyboard_and_standard_mode(page, server):
    """Район выбирается картой/клавиатурой и не блокирует прежнюю шкалу."""
    console_issues = []
    page.on('pageerror', lambda error: console_issues.append(str(error)))
    page.on('console', lambda message: console_issues.append(message.text)
            if message.type in ('warning', 'error')
            and not message.text.startswith('Failed to load resource:') else None)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(server)

    picker = page.get_by_role('button', name='Выбрать конкретный район')
    picker.click()
    expect(page.locator('#district-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#district-screen-title')).to_be_focused()
    expect(page.locator('#district-map')).to_be_visible(timeout=10000)
    expect(page.locator('#district-map .district-shape')).to_have_count(18)
    expect(page.locator('#district-list')).to_be_visible()
    expect(page.locator('#district-selected-name')).to_have_text('Район не выбран')
    expect(page.get_by_role('button', name='Выбрать район')).to_be_disabled()
    assert page.locator('#district-map .district-shape[tabindex="0"]').count() == 1
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')

    # Маленькие центральные районы доступны на SVG; отдельная кнопка даёт
    # удобное увеличение без pinch/pan, конфликтующего со scroll iPhone.
    original_viewbox = page.locator('#district-map').get_attribute('viewBox')
    page.get_by_role('button', name='Центр крупнее').click()
    assert page.locator('#district-map').get_attribute('viewBox') != original_viewbox
    expect(page.get_by_role('button', name='Показать весь город')).to_have_attribute(
        'aria-pressed', 'true')
    assert page.get_by_role('button', name='Показать весь город').evaluate(
        'element => element.getBoundingClientRect().height') >= 44

    petrogradsky = page.locator(
        '#district-map [data-district-id="petrogradsky"]'
    )
    petrogradsky.click()
    expect(page.locator('#district-selected-name')).to_have_text('Петроградский район')
    expect(petrogradsky).to_have_attribute('aria-checked', 'true')
    expect(page.locator('#district-list')).to_have_value('petrogradsky')
    page.get_by_role('button', name='Выбрать район').click()

    expect(page.locator('#start-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#territory-value')).to_have_text('Петроградский район')
    expect(page.locator('#territory-control')).to_have_class(
        re.compile('district-active'))
    expect(page.get_by_role('slider', name='Территория')).to_be_enabled()

    # Внутренняя навигация приложения сохраняет ту же настройку территории.
    page.locator('#show-leaderboard-btn').click()
    expect(page.locator('#leaderboard-screen')).to_have_class(re.compile('active'))
    page.locator('#back-btn').click()
    expect(page.locator('#territory-value')).to_have_text('Петроградский район')

    # Повторное открытие показывает текущий район; карта поддерживает radio-
    # keyboard pattern, Escape отменяет draft и возвращает focus.
    page.get_by_role('button', name=re.compile('Изменить район')).click()
    expect(page.locator('#district-list')).to_have_value('petrogradsky')
    admiralteysky = page.locator(
        '#district-map [data-district-id="admiralteysky"]'
    )
    admiralteysky.focus()
    page.keyboard.press('Space')
    expect(page.locator('#district-selected-name')).to_have_text('Адмиралтейский район')
    page.keyboard.press('Escape')
    expect(page.locator('#territory-value')).to_have_text('Петроградский район')
    expect(page.locator('#district-picker-btn')).to_be_focused()

    # Любой обычный preset атомарно снимает район; отдельный reset не нужен.
    page.get_by_role('button', name='Центр', exact=True).click()
    expect(page.locator('#territory-value')).to_have_text('Центр')
    expect(page.locator('#territory-control')).not_to_have_class(
        re.compile('district-active'))
    expect(page.locator('#district-action-label')).to_have_text(
        'Выбрать конкретный район')

    # Native select — компактный accessibility/touch fallback. Выбранная пара
    # доходит до того же /start как единое district state.
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option('kolpinsky')
    expect(page.locator('#district-selected-name')).to_have_text('Колпинский район')
    page.get_by_role('button', name='Выбрать район').click()
    with page.expect_request(
            lambda request: request.url.endswith('/api/game/start')) as request_info:
        page.locator('#start-btn').click()
    payload = request_info.value.post_data_json
    assert payload['difficulty'] == 'district'
    assert payload['district_id'] == 'kolpinsky'
    expect(page.locator('#game-screen')).to_have_class(re.compile('active'), timeout=10000)
    assert console_issues == []


def test_district_picker_list_fallback_when_geometry_fails(page, server):
    """Локальная карта может сломаться отдельно, но район всё ещё выбирается."""
    page.route('**/api/districts/geometry', lambda route: route.fulfill(
        status=500, content_type='application/json', body='{"error":"broken"}'))
    page.goto(server)
    page.locator('#district-picker-btn').click()

    expect(page.locator('#district-map-error')).to_be_visible(timeout=10000)
    expect(page.locator('#district-list')).to_be_visible()
    page.locator('#district-list').select_option('kronshtadtsky')
    page.get_by_role('button', name='Выбрать район').click()
    expect(page.locator('#territory-value')).to_have_text('Кронштадтский район')
    expect(page.locator('#territory-control')).to_have_class(
        re.compile('district-active'))


def test_settings_have_styled_initial_render_without_javascript(browser, server):
    """CSS оформляет native controls до и независимо от выполнения entry JS."""
    context = browser.new_context(
        viewport={'width': 320, 'height': 780},
        java_script_enabled=False,
    )
    page = context.new_page()
    try:
        page.goto(server)
        expect(page.locator('#territory-value')).to_have_text('Средняя')
        expect(page.locator('#territory-range')).to_be_visible()
        expect(page.locator('.segmented-control')).to_be_visible()
        expect(page.locator('.switch-control')).to_be_visible()
        expect(page.locator('.difficulty-btn, .timer-btn, .move-btn')).to_have_count(0)

        styles = page.locator('#territory-range').evaluate(
            "element => ({"
            "appearance: getComputedStyle(element).appearance,"
            "height: element.getBoundingClientRect().height"
            "})"
        )
        assert styles['appearance'] == 'none'
        assert styles['height'] >= 44
        assert page.locator('.territory-label').first.evaluate(
            "element => getComputedStyle(element).backgroundColor"
        ) == 'rgba(0, 0, 0, 0)'
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    finally:
        context.close()


def test_missing_settings_component_does_not_abort_other_controls(page, server):
    """Несовместимый/опциональный блок не обрушает инициализацию соседних."""
    page_errors = []
    page.on('pageerror', lambda error: page_errors.append(str(error)))
    page.add_init_script("""
        document.addEventListener('DOMContentLoaded', () => {
            document.getElementById('difficulty-group')?.remove();
        }, { once: true });
    """)
    page.goto(server)

    page.get_by_text('1 мин', exact=True).click()
    expect(page.get_by_role('radio', name='1 мин')).to_be_checked()
    movement = page.get_by_role('switch')
    movement.click()
    expect(movement).not_to_be_checked()
    expect(page.locator('#movement-title')).to_have_text('Нельзя перемещаться')
    assert page_errors == []


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


def test_district_without_coverage_can_return_to_preserved_settings(page, server):
    """Редкий район без съёмки не запирает игрока на игровом экране."""
    page.set_viewport_size({'width': 390, 'height': 844})
    page.add_init_script('window.__ymapsEmptyLocateCount = 100')
    page.goto(server)
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option('kurortny')
    page.get_by_role('button', name='Выбрать район').click()
    page.locator('#start-btn').click()

    expect(page.locator('#photo-overlay')).to_be_visible(timeout=10000)
    expect(page.locator('#photo-overlay > span')).to_have_text(
        'Не удалось найти съёмку рядом.'
    )
    expect(page.get_by_role('button', name='Повторить')).to_be_visible()
    expect(page.get_by_role('button', name='Другое место')).to_be_visible()
    back = page.get_by_role('button', name='К настройкам')
    expect(back).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')

    back.click()
    expect(page.locator('#start-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#territory-value')).to_have_text('Курортный район')
    expect(page.locator('#territory-control')).to_have_class(
        re.compile('district-active'))
    expect(page.locator('#district-picker-btn')).to_be_focused()
    page.wait_for_function(
        'window.__ymapsStats.panoramaPlayersActive === 0 && '
        'window.__ymapsStats.mapsActive === 0'
    )


def test_district_skip_limit_has_honest_recovery_message(page, server):
    """429 лимита замен не маскируется под сетевую ошибку и имеет выход."""
    page.add_init_script('window.__ymapsEmptyLocateCount = 100')
    page.route('**/api/game/skip_location', lambda route: route.fulfill(
        status=429,
        content_type='application/json',
        body='{"error":"Лимит перегенераций точки для этого раунда исчерпан"}',
    ))
    page.goto(server)
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option('kronshtadtsky')
    page.get_by_role('button', name='Выбрать район').click()
    page.locator('#start-btn').click()

    expect(page.locator('#photo-overlay > span')).to_have_text(
        'В этом районе не удалось найти доступную панораму. '
        'Выберите другой район или режим.',
        timeout=10000,
    )
    expect(page.get_by_role('button', name='Повторить')).to_be_hidden()
    expect(page.get_by_role('button', name='Другое место')).to_be_hidden()
    expect(page.get_by_role('button', name='К настройкам')).to_be_visible()


def test_too_distant_nearest_panorama_is_not_used_as_the_answer(page, server):
    """Ближайшая, но далёкая съёмка не рассинхронизирует картинку и счёт."""
    page.add_init_script('window.__ymapsFarLocateCount = 1')
    page.goto(server)
    page.locator('#start-btn').click()

    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    stats = page.evaluate('window.__ymapsStats')
    assert stats['locateCalls'] == 2
    assert stats['panoramaPlayersActive'] == 1


def test_panorama_outside_district_is_replaced_before_player(page, server):
    """Съёмка за границей района заменяется и не показывается игроку."""
    validation_count = 0
    skip_payloads = []

    def validate_route(route):
        nonlocal validation_count
        validation_count += 1
        if validation_count == 1:
            route.fulfill(
                status=200,
                content_type='application/json',
                body='{"valid":false,"reason":"outside_district"}',
            )
        else:
            route.continue_()

    page.route('**/api/game/validate_panorama', validate_route)
    page.on('request', lambda request: skip_payloads.append(request.post_data_json)
            if request.url.endswith('/api/game/skip_location') else None)
    page.goto(server)
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option('petrogradsky')
    page.get_by_role('button', name='Выбрать район').click()
    page.locator('#start-btn').click()

    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    stats = page.evaluate('window.__ymapsStats')
    assert validation_count == 2
    assert stats['locateCalls'] == 2
    assert skip_payloads[0]['reason'] == 'outside_district'
    assert stats['panoramaPlayersCreated'] == 1


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
