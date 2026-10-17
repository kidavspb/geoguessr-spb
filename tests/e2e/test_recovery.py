"""Recovery from failed HTTP requests and late responses across screens."""
import json
import re

import pytest
from playwright.sync_api import expect


def play_round(page):
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    page.locator('#map').click(position={'x': 100, 'y': 100})
    page.locator('#guess-btn').click()
    expect(page.locator('#result-screen')).to_have_class(re.compile('active'))


def test_failed_location_can_be_retried_without_reload(page, server):
    def without_embedded_location(route):
        response = route.fetch()
        data = response.json()
        data.pop('location')
        route.fulfill(response=response, json=data)

    page.route('**/api/game/start', without_embedded_location)
    page.route('**/api/game/location', lambda route: route.fulfill(
        status=503, json={'error': 'Временная ошибка'}))
    page.goto(server)
    page.locator('#start-btn').click()
    expect(page.locator('#retry-panorama-btn')).to_be_visible()
    page.unroute('**/api/game/location')
    page.locator('#retry-panorama-btn').click()
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    play_round(page)


def test_final_results_failure_keeps_a_retryable_result(page, server):
    page.goto(server)
    page.locator('#start-btn').click()
    for number in range(5):
        play_round(page)
        if number < 4:
            page.locator('#next-round-btn').click()

    page.route('**/api/game/results', lambda route: route.fulfill(
        status=503, json={'error': 'Результаты временно недоступны'}))
    page.locator('#next-round-btn').click()
    expect(page.locator('#toast')).to_contain_text('Результаты временно недоступны')
    expect(page.locator('#result-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#next-round-btn')).to_be_enabled()

    page.unroute('**/api/game/results')
    page.locator('#next-round-btn').click()
    expect(page.locator('#final-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#rounds-summary .round-item')).to_have_count(5)
    assert page.evaluate('window.__ymapsStats.panoramaPlayersActive') == 0


def test_expired_ready_deadline_submits_timeout_only_once(page, server):
    def expired_deadline(route):
        response = route.fetch()
        data = response.json()
        data['remaining_ms'] = 0
        route.fulfill(response=response, json=data)

    guesses = []
    page.on('request', lambda request: guesses.append(request.post_data_json)
            if request.url.endswith('/api/game/guess') else None)
    page.route('**/api/game/ready', expired_deadline)
    page.goto(server)
    page.get_by_text('1 мин', exact=True).click()
    page.locator('#start-btn').click()
    expect(page.locator('#result-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#result-title')).to_have_text('Время вышло!')
    # Check several former 250 ms timer ticks after the synchronous expiry.
    page.wait_for_timeout(800)
    assert len(guesses) == 1
    assert guesses[0]['timed_out'] is True


def test_answer_rejected_as_late_is_shown_as_timeout(page, server):
    def late_answer(route):
        response = route.fetch()
        data = response.json()
        data.update(timed_out=True, score=0)
        route.fulfill(response=response, json=data)

    page.route('**/api/game/guess', late_answer)
    page.goto(server)
    page.locator('#start-btn').click()
    play_round(page)
    expect(page.locator('#result-title')).to_have_text('Время вышло!')
    expect(page.locator('#result-miss')).to_contain_text('после окончания времени')


@pytest.mark.parametrize('clock_offset_ms', [90_000, -90_000])
def test_round_timer_ignores_device_clock_offset(page, server, clock_offset_ms):
    """Остаток считается от ответа сервера, а не от сравнения двух часов."""
    page.add_init_script(f"""
        const realNow = Date.now.bind(Date);
        Date.now = () => realNow() + ({clock_offset_ms});
    """)
    page.goto(server)
    page.get_by_text('1 мин', exact=True).click()
    with page.expect_response('**/api/game/ready'):
        page.locator('#start-btn').click()
    # Несколько тиков таймера после применения ответа /ready.
    page.wait_for_timeout(1000)

    expect(page.locator('#game-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#timer-value')).to_have_text(re.compile(r'^(1:00|0:5\d)$'))


def test_admin_delete_tracks_requested_point_when_selection_changes(page, server):
    points = [
        {'id': n, 'latitude': 59.93, 'longitude': 30.31 + n / 100,
         'fail_count': 0, 'dist_from_center_km': 1, 'created_at': None}
        for n in (1, 2)
    ]
    page.route('**/api/admin/points', lambda route: route.fulfill(json={'points': points}))
    page.route('**/api/admin/stats', lambda route: route.fulfill(status=503, json={}))
    page.goto(server + '/admin')
    page.locator('#admin-key').fill('test-admin-key')
    page.locator('#load-btn').click()
    expect(page.locator('.admin-dot')).to_have_count(2)
    page.evaluate("selectPoint(1)")
    page.on('dialog', lambda dialog: dialog.accept())
    # Delay only DELETE's response while still allowing map selection changes.
    page.evaluate("""() => {
        const originalFetch = window.fetch;
        window.fetch = (url, options) => options?.method === 'DELETE'
            ? new Promise(resolve => { window.finishDelete = () => resolve(
                new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })
            ); })
            : originalFetch(url, options);
    }""")
    page.locator('#pi-delete').click()
    page.wait_for_function('typeof window.finishDelete === "function"')
    page.evaluate('selectPoint(2); window.finishDelete()')
    expect(page.locator('.admin-dot')).to_have_count(1)
    expect(page.locator('.admin-dot')).to_have_attribute('title', re.compile('#2'))
    expect(page.locator('#pi-coords')).to_contain_text('#2')


def _start_district_game(page, server, district_id='petrogradsky'):
    page.goto(server)
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option(district_id)
    page.locator('#district-confirm-btn').click()
    page.locator('#start-btn').click()


def test_retry_recovers_after_lost_skip_responses(page, server):
    """Сервер применил замену, но клиент не получил ответ: повтор синхронизирует версию."""
    calls = {'validate': 0, 'skip': 0}

    def validate(route):
        calls['validate'] += 1
        if calls['validate'] == 1:
            return route.fulfill(status=200, content_type='application/json',
                                 body=json.dumps({'valid': False, 'reason': 'outside_district'}))
        route.fulfill(response=route.fetch())

    def skip(route):
        calls['skip'] += 1
        response = route.fetch()  # сервер применяет замену
        if calls['skip'] <= 2:    # попытка и повтор api.js теряют ответ
            return route.abort()
        route.fulfill(response=response)

    page.route('**/api/game/validate_panorama', validate)
    page.route('**/api/game/skip_location', skip)
    _start_district_game(page, server)

    retry = page.locator('#retry-panorama-btn')
    expect(retry).to_be_visible(timeout=15000)
    expect(page.locator('#back-to-district-btn')).to_be_visible()
    retry.click()
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=15000)
    assert page.evaluate('window.__ymapsStats.panoramaPlayersActive') == 1


def test_server_error_overlay_offers_settings(page, server):
    page.route('**/api/game/validate_panorama', lambda route: route.fulfill(
        status=503, content_type='application/json',
        body=json.dumps({'error': 'Выбор района временно недоступен'})))
    _start_district_game(page, server)

    overlay = page.locator('#photo-overlay')
    expect(overlay).to_contain_text('Выбор района временно недоступен', timeout=15000)
    expect(page.locator('#retry-panorama-btn')).to_be_visible()
    page.locator('#back-to-district-btn').click()
    expect(page.locator('#start-screen')).to_have_class(re.compile('active'))


def test_failed_guess_map_stays_retryable_in_next_round(page, server):
    """Без карты ответить нельзя, поэтому повтор не должен пропадать."""
    abort_v3 = lambda route: route.abort()  # noqa: E731
    page.route('**/api-maps.yandex.ru/v3/**', abort_v3)

    def short_round(route):
        response = route.fetch()
        data = response.json()
        data['remaining_ms'] = 1500
        route.fulfill(response=response, json=data)

    page.route('**/api/game/ready', short_round)
    page.goto(server)
    page.get_by_text('1 мин', exact=True).click()
    page.locator('#start-btn').click()
    hint = page.locator('#map-hint')
    expect(hint).to_have_text(re.compile('Карта не загрузилась'), timeout=30000)
    expect(page.locator('#result-screen')).to_have_class(re.compile('active'), timeout=15000)

    page.locator('#next-round-btn').click()
    expect(page.locator('#current-round')).to_have_text('2')
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    expect(hint).to_have_text(re.compile('Карта не загрузилась'))
    expect(hint).to_have_class(re.compile('map-retry'))

    page.unroute('**/api-maps.yandex.ru/v3/**', abort_v3)
    hint.click()
    expect(hint).to_have_text('Кликни по карте, чтобы отметить место', timeout=15000)


@pytest.mark.parametrize('sdk', ['v2', 'v3'])
def test_sdk_readiness_timeout_allows_a_fresh_attempt(page, server, sdk):
    page.goto(server)
    page.wait_for_function('window.ymaps && window.ymaps3')
    page.clock.install()
    module = 'panorama' if sdk == 'v2' else 'maps'
    ready = 'ymapsV2Ready' if sdk == 'v2' else 'ymapsV3Ready'
    page.evaluate("""async ({ sdk, module, ready }) => {
        if (sdk === 'v2') window.ymaps.ready = () => {};
        else window.ymaps3.ready = new Promise(() => {});
        const api = await import(`/static/js/${module}.js`);
        window.sdkOutcome = 'pending';
        api[ready]().then(() => { window.sdkOutcome = 'ready'; },
                          () => { window.sdkOutcome = 'failed'; });
    }""", {'sdk': sdk, 'module': module, 'ready': ready})
    page.clock.fast_forward(13000)
    assert page.evaluate('window.sdkOutcome') == 'failed'
    page.clock.resume()
    # A new attempt reloads the stub script and resolves normally.
    assert page.evaluate("""async ({ module, ready }) => {
        const api = await import(`/static/js/${module}.js`);
        await api[ready]();
        return true;
    }""", {'module': module, 'ready': ready})


def test_outdated_tab_gets_reload_message_before_starting_game(page, server):
    """Старый payload не запускает игру, после обновления можно начать."""
    dialogs = []

    def accept_dialog(dialog):
        dialogs.append(dialog.message)
        dialog.accept()

    def old_start_payload(route):
        payload = route.request.post_data_json
        payload.pop('client_panorama_rules_version', None)
        route.continue_(post_data=json.dumps(payload))

    page.on('dialog', accept_dialog)
    page.route('**/api/game/start', old_start_payload)
    page.goto(server)
    page.get_by_role('button', name='Весь город', exact=True).click()
    page.locator('#start-btn').click()

    expect(page.locator('#start-btn')).to_be_enabled()
    assert dialogs == [
        'Ошибка: Игра обновилась. Обновите страницу перед началом новой игры',
    ]
    expect(page.locator('#start-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#game-screen')).not_to_have_class(re.compile('active'))
    assert page.evaluate('window.__ymapsStats.panoramaPlayersCreated') == 0

    page.unroute('**/api/game/start', old_start_payload)
    page.reload()
    page.get_by_role('button', name='Весь город', exact=True).click()
    page.locator('#start-btn').click()
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    expect(page.locator('#game-screen')).to_have_class(re.compile('active'))
    assert page.evaluate('window.__ymapsStats.panoramaPlayersCreated') == 1


def test_challenge_survives_failed_info_and_unknown_token_returns_settings(page, server):
    """Сбой GET /api/challenge не отменяет челлендж; 404 при старте — отменяет."""
    page.route('**/api/challenge/**', lambda route: route.fulfill(
        status=503, content_type='application/json', body='{"error": "Сервер занят"}'))
    page.goto(f'{server}/?challenge=missing-token')
    banner = page.locator('#challenge-banner')
    expect(banner).to_contain_text('те же')
    expect(page.locator('#difficulty-group')).to_be_hidden()

    with page.expect_request('**/api/game/start') as first_start:
        page.locator('#start-btn').click()
    assert first_start.value.post_data_json['challenge_token'] == 'missing-token'
    # Сервер не знает токен: показываем это и возвращаем обычные настройки.
    expect(banner).to_contain_text('Челлендж не найден')
    expect(page.locator('#difficulty-group')).to_be_visible()
    expect(page.locator('#start-screen')).to_have_class(re.compile('active'))

    with page.expect_request('**/api/game/start') as second_start:
        page.locator('#start-btn').click()
    assert second_start.value.post_data_json['challenge_token'] is None
    expect(page.locator('#game-screen')).to_have_class(re.compile('active'))
