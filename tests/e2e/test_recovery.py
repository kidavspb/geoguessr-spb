"""Recovery from failed HTTP requests and late responses across screens."""
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
        data['deadline_ms'] = 1
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
