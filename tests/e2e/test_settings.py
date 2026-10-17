"""Фиксированные правила вызова дня и челленджа не подменяют настройки игрока."""
import http.cookiejar
import json
import re
import urllib.request

from playwright.sync_api import expect


def _author_challenge_token(server, **settings):
    """Завершённая игра автора (отдельные cookie) и её токен челленджа."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    def post(path, body):
        request = urllib.request.Request(
            server + path, data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'}, method='POST',
        )
        with opener.open(request) as response:
            return json.load(response)

    location = post('/api/game/start', {'player_name': 'Автор', **settings})['location']
    for _ in range(5):
        result = post('/api/game/guess', {
            'round_id': location['round_id'],
            'location_version': location['location_version'],
            'latitude': location['latitude'],
            'longitude': location['longitude'],
        })
        location = result.get('next_location')
    with opener.open(server + '/api/game/results') as response:
        return json.load(response)['challenge_token']


def _expect_settings(page, territory, timer_value, can_move):
    # Стартовый экран скрыт во время игры, поэтому без поиска по ролям.
    expect(page.locator('#territory-value')).to_have_text(territory)
    expect(page.locator('input[name="round-time"]:checked')).to_have_value(timer_value)
    movement = page.locator('#movement-toggle')
    if can_move:
        expect(movement).to_be_checked()
    else:
        expect(movement).not_to_be_checked()


def test_daily_keeps_chosen_settings_for_next_games(page, server):
    page.goto(server)
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option('petrogradsky')
    page.locator('#district-confirm-btn').click()
    page.get_by_text('1 мин', exact=True).click()
    page.get_by_role('switch').click()

    page.locator('#daily-btn').click()
    expect(page.locator('#game-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)

    # Вызов дня идёт по своим правилам: без таймера и с перемещением.
    expect(page.locator('#timer-chip')).to_be_hidden()
    expect(page.locator('#pano-home')).to_be_visible()
    # Выбор игрока для следующих обычных игр не тронут.
    _expect_settings(page, 'Петроградский район', '60', can_move=False)


def test_challenge_does_not_leave_challenger_settings_behind(page, server):
    token = _author_challenge_token(server, difficulty='center', time_limit=180, no_move=True)
    page.goto(f'{server}/?challenge={token}')
    expect(page.locator('#challenge-banner')).to_contain_text('Автор')

    page.locator('#start-btn').click()
    expect(page.locator('#game-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)

    # Игра идёт по правилам автора: таймер 3 минуты, без перемещения.
    expect(page.locator('#timer-chip')).to_be_visible()
    expect(page.locator('#pano-home')).to_be_hidden()
    # Настройки на стартовом экране остаются прежними, а не авторскими.
    _expect_settings(page, 'Средняя', '0', can_move=True)
