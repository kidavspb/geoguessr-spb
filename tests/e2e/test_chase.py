"""Хэллоуинский «Побег от Медного всадника»: минутная игра с погоней."""
import re

from playwright.sync_api import expect

FIRST_WHISPER = 'Медный всадник сошёл с Гром-камня…'


def _shorten_rounds(page, remaining_ms):
    """Сервер «отвечает», что от раунда осталось remaining_ms."""
    def ready(route):
        response = route.fetch()
        data = response.json()
        data['remaining_ms'] = remaining_ms
        route.fulfill(response=response, json=data)

    page.route('**/api/game/ready', ready)


def _record_guesses(page):
    guesses = []
    page.on('request', lambda request: guesses.append(request.post_data_json)
            if request.url.endswith('/api/game/guess') else None)
    return guesses


def test_chase_is_a_one_minute_game_on_chosen_territory(page, halloween_server):
    console_issues = []
    page.on('pageerror', lambda error: console_issues.append(str(error)))
    page.on('console', lambda message: console_issues.append(message.text)
            if message.type == 'error'
            and not message.text.startswith('Failed to load resource:') else None)
    page.goto(halloween_server)

    chase = page.get_by_role('button', name='Побег от Медного всадника')
    for width in [320, 390, 1280]:
        page.set_viewport_size({'width': width, 'height': 800})
        expect(chase).to_be_visible()
        assert chase.bounding_box()['height'] >= 44
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')

    page.get_by_role('button', name='Центр', exact=True).click()
    page.get_by_role('switch').click()
    with page.expect_request(lambda request: request.url.endswith('/api/game/start')) as start:
        chase.click()
    payload = start.value.post_data_json
    assert payload['difficulty'] == 'center'
    assert payload['time_limit'] == 60
    assert payload['no_move'] is True

    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    expect(page.locator('#timer-chip')).to_be_visible()
    expect(page.locator('#chase-layer')).to_be_visible()
    expect(page.locator('#chase-whisper')).to_have_text(FIRST_WHISPER)
    expect(page.locator('#chase-whisper')).to_have_class(re.compile('show'))
    # Погоня — только CSS: ни второго Player, ни перехвата кликов по карте.
    assert page.evaluate('window.__ymapsStats.panoramaPlayersActive') == 1
    assert page.locator('#chase-layer').evaluate(
        'element => getComputedStyle(element).pointerEvents') == 'none'
    # Минутный таймер погони не подменяет выбор игрока для обычных игр.
    expect(page.locator('input[name="round-time"]:checked')).to_have_value('0')

    page.locator('#map').click(position={'x': 150, 'y': 120})
    page.locator('#guess-btn').click()
    expect(page.locator('#result-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#result-title')).not_to_have_text('Всадник догнал!')
    expect(page.locator('#chase-caught')).to_be_hidden()
    assert console_issues == []


def test_horseman_catches_player_and_next_round_starts_far_away(page, halloween_server):
    guesses = _record_guesses(page)
    _shorten_rounds(page, 1500)
    page.goto(halloween_server)
    page.locator('#chase-btn').click()

    expect(page.locator('#chase-caught')).to_be_visible(timeout=10000)
    expect(page.locator('#result-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#result-title')).to_have_text('Всадник догнал!')
    expect(page.locator('#result-miss')).to_have_text(
        'Всадник догнал тебя раньше, чем ты отметил точку')
    # Сцена короткая и не мешает нажать «Продолжить».
    expect(page.locator('#chase-caught')).to_be_hidden(timeout=4000)
    assert len(guesses) == 1
    assert guesses[0]['timed_out'] is True

    page.unroute('**/api/game/ready')
    page.locator('#next-round-btn').click()
    expect(page.locator('#current-round')).to_have_text('2')
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    # Новый раунд — погоня заново: первая фраза и честный заголовок.
    expect(page.locator('#chase-whisper')).to_have_text(FIRST_WHISPER)
    page.locator('#map').click(position={'x': 150, 'y': 120})
    page.locator('#guess-btn').click()
    expect(page.locator('#result-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#result-title')).not_to_have_text('Всадник догнал!')


def test_point_marked_before_catch_still_counts(page, halloween_server):
    guesses = _record_guesses(page)
    _shorten_rounds(page, 3000)
    page.goto(halloween_server)
    page.locator('#chase-btn').click()
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    page.locator('#map').click(position={'x': 150, 'y': 120})
    expect(page.locator('#guess-btn')).to_be_enabled()

    expect(page.locator('#chase-caught')).to_be_visible(timeout=5000)
    expect(page.locator('#result-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#result-title')).to_have_text('Всадник догнал!')
    expect(page.locator('#result-distance')).to_be_visible()
    assert len(guesses) == 1
    assert 'latitude' in guesses[0] and not guesses[0].get('timed_out')


def test_regular_timed_game_has_no_chase_in_season(page, halloween_server):
    page.goto(halloween_server)
    page.get_by_text('1 мин', exact=True).click()
    page.locator('#start-btn').click()
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    expect(page.locator('#timer-chip')).to_be_visible()
    expect(page.locator('#chase-layer')).to_be_hidden()
    expect(page.locator('#chase-whisper')).not_to_have_class(re.compile('show'))


def test_off_season_page_has_no_halloween(page, server):
    page.goto(server)
    expect(page.locator('#start-btn')).to_be_visible()
    expect(page.locator('#chase-btn')).to_have_count(0)
    expect(page.locator('.halloween-sky')).to_have_count(0)
    expect(page.locator('body')).not_to_have_class(re.compile('halloween'))
