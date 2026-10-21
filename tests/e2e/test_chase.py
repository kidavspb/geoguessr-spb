"""Хэллоуинская «Ночь Медного всадника»: пасхалка, режим и погоня в раунде."""
import re

from playwright.sync_api import expect

from test_settings import _author_challenge_token

FIRST_WHISPER = 'Медный всадник сошёл с Гром-камня…'
NO_HSCROLL = 'document.documentElement.scrollWidth <= innerWidth'


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


def _open_chase(page, server):
    """Без анимаций: всадник стоит на месте, режим включается сразу."""
    page.emulate_media(reduced_motion='reduce')
    page.goto(server)
    page.locator('#horseman-peek').click()
    expect(page.locator('#chase-title')).to_be_visible()


def _start_chase_round(page):
    page.locator('#start-btn').click()
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)


def test_horseman_turns_start_screen_into_chase_mode(page, halloween_server):
    console_issues = []
    page.on('pageerror', lambda error: console_issues.append(str(error)))
    page.on('console', lambda message: console_issues.append(message.text)
            if message.type == 'error'
            and not message.text.startswith('Failed to load resource:') else None)
    page.emulate_media(reduced_motion='reduce')
    page.goto(halloween_server)

    horseman = page.get_by_role('button', name=re.compile('Медный всадник'))
    expect(horseman).to_be_visible()
    expect(page.locator('#chase-title')).to_be_hidden()
    for width in [320, 390, 1280]:
        page.set_viewport_size({'width': width, 'height': 800})
        assert page.evaluate(NO_HSCROLL)

    horseman.click()
    expect(page.locator('#chase-title')).to_have_text('Ночь Медного всадника')
    expect(page.locator('.brand-title:not(.chase-only)')).to_be_hidden()
    expect(page.locator('.chase-note')).to_have_text('До 1 ноября')
    expect(page.locator('html')).to_have_class(re.compile('theme-chase'))
    expect(page.locator('#start-btn')).to_have_text('Бежать')
    expect(horseman).to_be_disabled()
    # Без таймера погоня невозможна: вызов дня и «Без лимита» видны, но неактивны.
    expect(page.locator('#daily-btn')).to_be_visible()
    expect(page.locator('#daily-btn')).to_be_disabled()
    expect(page.locator('label[for="timer-unlimited"]')).to_be_visible()
    expect(page.locator('#timer-unlimited')).to_be_disabled()
    expect(page.locator('input[name="round-time"]:checked')).to_have_value('60')
    expect(page.get_by_role('button', name='Назад')).to_be_visible()
    for width in [320, 390, 1280]:
        page.set_viewport_size({'width': width, 'height': 800})
        assert page.evaluate(NO_HSCROLL)

    # Все настройки главного экрана работают и в погоне, включая район.
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option('petrogradsky')
    page.locator('#district-confirm-btn').click()
    page.get_by_text('3 мин', exact=True).click()
    page.get_by_role('switch').click()
    with page.expect_request(lambda request: request.url.endswith('/api/game/start')) as start:
        _start_chase_round(page)
    payload = start.value.post_data_json
    assert payload['difficulty'] == 'district'
    assert payload['district_id'] == 'petrogradsky'
    assert payload['time_limit'] == 180
    assert payload['no_move'] is True
    assert payload['challenge_token'] is None

    expect(page.locator('.game-stage')).to_have_class(re.compile('chase-mode'))
    expect(page.locator('#chase-layer')).to_be_visible()
    expect(page.locator('#chase-whisper')).to_have_text(FIRST_WHISPER)
    expect(page.locator('#timer-chip svg.timer-icon')).to_be_visible()
    expect(page.locator('#timer-chip span.timer-icon')).to_be_hidden()
    # Чёрно-белый только снимок: логотип и копирайты Яндекса не трогаем.
    assert page.locator('#panorama-player canvas').evaluate(
        'element => getComputedStyle(element).filter') == 'grayscale(1)'
    assert page.locator('#panorama-player').evaluate(
        'element => getComputedStyle(element).filter') == 'none'
    # Погоня — только CSS: ни второго Player, ни перехвата кликов по карте.
    assert page.evaluate('window.__ymapsStats.panoramaPlayersActive') == 1
    assert page.locator('#chase-layer').evaluate(
        'element => getComputedStyle(element).pointerEvents') == 'none'

    page.locator('#map').click(position={'x': 150, 'y': 120})
    page.locator('#guess-btn').click()
    expect(page.locator('#result-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#result-title')).not_to_have_text('Всадник догнал!')
    expect(page.locator('#chase-caught')).to_be_hidden()
    assert console_issues == []


def test_back_returns_regular_start_screen(page, halloween_server):
    _open_chase(page, halloween_server)
    page.get_by_role('button', name='Назад').click()

    expect(page.locator('.brand-title:not(.chase-only)')).to_have_text('Петербургский следопыт')
    expect(page.locator('#chase-title')).to_be_hidden()
    expect(page.locator('html')).not_to_have_class(re.compile('theme-chase'))
    expect(page.locator('#start-btn')).to_have_text('Начать игру')
    expect(page.locator('#horseman-peek')).to_be_enabled()
    expect(page.locator('#daily-btn')).to_be_enabled()
    expect(page.locator('#timer-unlimited')).to_be_enabled()
    # «Без лимита» был выбран до погони — он и возвращается.
    expect(page.locator('input[name="round-time"]:checked')).to_have_value('0')

    with page.expect_request(lambda request: request.url.endswith('/api/game/start')) as start:
        page.locator('#start-btn').click()
    assert start.value.post_data_json['time_limit'] is None
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    expect(page.locator('.game-stage')).not_to_have_class(re.compile('chase-mode'))


def test_flood_switches_mode_under_the_wave(page, halloween_server):
    page.goto(halloween_server)
    expect(page.locator('#chase-flood')).not_to_have_class(re.compile('run'))
    # Всадник то появляется, то исчезает — ждать «стабильности» бессмысленно.
    page.locator('#horseman-peek').click(force=True)
    expect(page.locator('#chase-flood')).to_have_class(re.compile('run'))
    expect(page.locator('#chase-title')).to_be_visible(timeout=3000)
    expect(page.locator('#chase-flood')).not_to_have_class(re.compile('run'), timeout=3000)
    expect(page.locator('#start-btn')).to_have_text('Бежать')


def test_horseman_catches_player_and_next_round_starts_far_away(page, halloween_server):
    guesses = _record_guesses(page)
    _shorten_rounds(page, 1500)
    _open_chase(page, halloween_server)
    page.locator('#start-btn').click()

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
    # С запасом на загрузку карты под нагрузкой: точку надо успеть отметить.
    _shorten_rounds(page, 8000)
    _open_chase(page, halloween_server)
    _start_chase_round(page)
    page.locator('#map').click(position={'x': 150, 'y': 120})
    expect(page.locator('#guess-btn')).to_be_enabled()

    # Ответа нет — его отправит истечение времени. Заголовок «догнал» при
    # засчитанной точке ставит только сцена поимки.
    expect(page.locator('#result-screen')).to_have_class(re.compile('active'), timeout=15000)
    expect(page.locator('#result-title')).to_have_text('Всадник догнал!')
    expect(page.locator('#result-distance')).to_be_visible()
    assert len(guesses) == 1
    assert 'latitude' in guesses[0] and not guesses[0].get('timed_out')


def test_regular_timed_game_has_no_chase_in_season(page, halloween_server):
    page.goto(halloween_server)
    page.get_by_text('1 мин', exact=True).click()
    page.locator('#start-btn').click()
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    expect(page.locator('#timer-chip')).to_be_visible()
    expect(page.locator('#timer-chip span.timer-icon')).to_be_visible()
    expect(page.locator('#chase-layer')).to_be_hidden()
    expect(page.locator('#chase-whisper')).not_to_have_class(re.compile('show'))
    assert page.locator('#panorama-player canvas').evaluate(
        'element => getComputedStyle(element).filter') == 'none'


def test_challenge_link_hides_the_horseman(page, halloween_server):
    token = _author_challenge_token(halloween_server, difficulty='center', time_limit=60)
    page.goto(f'{halloween_server}/?challenge={token}')
    expect(page.locator('#challenge-banner')).to_contain_text('Автор')
    # Погоня — своя игра, а не способ сыграть чужой челлендж.
    expect(page.locator('#horseman-peek')).to_be_hidden()


def test_off_season_page_has_no_halloween(page, server):
    page.goto(server)
    expect(page.locator('#start-btn')).to_be_visible()
    for selector in ('#horseman-peek', '#chase-title', '#chase-flood', '#chase-layer'):
        expect(page.locator(selector)).to_have_count(0)
    expect(page.locator('html')).not_to_have_class(re.compile('theme-chase'))
