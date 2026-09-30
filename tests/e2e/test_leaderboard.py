"""E2E таблицы лидеров: контекст игры, фильтры и запоздавшие ответы."""
import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest

try:
    import playwright  # noqa: F401
except ImportError:
    pytest.skip('playwright не установлен', allow_module_level=True)

from playwright.sync_api import expect


def _query(url):
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def _is_board(request, **expected):
    return (
        urlsplit(request.url).path == '/api/leaderboard'
        and all(_query(request.url).get(key) == value for key, value in expected.items())
    )


def _board_response(route, name):
    route.fulfill(
        status=200,
        content_type='application/json',
        body=json.dumps({'leaderboard': [{
            'rank': 1,
            'player_name': name,
            'total_score': 23456,
            'date': '27.09.2026',
        }]}, ensure_ascii=False),
    )


def _finish_game(page):
    for round_number in range(1, 6):
        expect(page.locator('#current-round')).to_have_text(str(round_number))
        expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
        expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
        page.locator('#map').click(position={'x': 150, 'y': 120})
        expect(page.locator('#guess-btn')).to_be_enabled()
        page.locator('#guess-btn').click()
        expect(page.locator('#result-screen')).to_have_class(re.compile('active'))
        page.locator('#next-round-btn').click()

    expect(page.locator('#final-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#rounds-summary .round-item')).to_have_count(5)


def test_district_leaderboard_filters_keep_territory_across_periods(page, server):
    requests = []

    def respond(route):
        query = _query(route.request.url)
        requests.append(query)
        district = query.get('district_id')
        _board_response(route, f'Лидер {district}' if district else 'Общий лидер')

    page.route('**/api/leaderboard*', respond)
    page.route('**/api/daily/leaderboard', lambda route: _board_response(route, 'Лидер дня'))
    page.goto(server)
    page.locator('#show-leaderboard-btn').click()
    table = page.locator('#leaderboard-table')
    expect(table).to_contain_text('Общий лидер')
    page.locator('#leaderboard-filter [data-difficulty="district"]').click()
    select = page.locator('#lb-district-select')
    expect(page.locator('#lb-district-filter')).to_be_visible()
    expect(select).to_have_value('')
    expect(select.locator('option:not([value=""])')).to_have_count(18)
    expect(table).to_contain_text(re.compile('выбер.*район', re.IGNORECASE))
    expect(table.locator('.leaderboard-row')).to_have_count(0)
    assert not any(query.get('difficulty') == 'district' for query in requests)

    with page.expect_request(lambda request: _is_board(
            request, difficulty='district', district_id='petrogradsky')):
        select.select_option('petrogradsky')
    expect(table).to_contain_text('Лидер petrogradsky')

    with page.expect_request(lambda request: _is_board(request)
                             and not _query(request.url)):
        page.locator('#leaderboard-filter [data-difficulty="all"]').click()
    expect(page.locator('#lb-district-filter')).to_be_hidden()
    expect(table).to_contain_text('Общий лидер')
    with page.expect_request(lambda request: _is_board(
            request, difficulty='district', district_id='petrogradsky')):
        page.locator('#leaderboard-filter [data-difficulty="district"]').click()
    expect(select).to_be_visible()
    expect(select).to_have_value('petrogradsky')
    expect(table).to_contain_text('Лидер petrogradsky')

    with page.expect_request(lambda request: _is_board(
            request, difficulty='center')) as center_request:
        page.locator('#leaderboard-filter [data-difficulty="center"]').click()
    assert 'district_id' not in _query(center_request.value.url)
    expect(page.locator('#lb-district-filter')).to_be_hidden()
    expect(table).to_contain_text('Общий лидер')
    with page.expect_request(lambda request: _is_board(
            request, difficulty='district', district_id='petrogradsky')):
        page.locator('#leaderboard-filter [data-difficulty="district"]').click()
    expect(select).to_have_value('petrogradsky')
    expect(table).to_contain_text('Лидер petrogradsky')

    for period in ('week', 'month'):
        with page.expect_request(lambda request: _is_board(
                request, difficulty='district', district_id='petrogradsky', period=period)):
            page.locator(f'#lb-period-filter [data-period="{period}"]').click()
        expect(table).to_contain_text('Лидер petrogradsky')

    page.locator('#lb-period-filter [data-period="daily"]').click()
    expect(table).to_contain_text('Лидер дня')
    expect(page.locator('#leaderboard-filter')).to_be_hidden()
    expect(page.locator('#lb-district-filter')).to_be_hidden()
    with page.expect_request(lambda request: _is_board(
            request, difficulty='district', district_id='petrogradsky', period='week')):
        page.locator('#lb-period-filter [data-period="week"]').click()
    expect(select).to_be_visible()
    expect(select).to_have_value('petrogradsky')
    expect(table).to_contain_text('Лидер petrogradsky')

    # Новое открытие с главной остаётся общим топом, а не последним районом.
    page.locator('#back-btn').click()
    with page.expect_request(lambda request: _is_board(request)
                             and not _query(request.url)):
        page.locator('#show-leaderboard-btn').click()
    expect(page.locator('#leaderboard-filter [data-difficulty="all"]')).to_have_class(
        re.compile('active'))
    expect(page.locator('#lb-period-filter [data-period="all"]')).to_have_class(
        re.compile('active'))
    expect(page.locator('#lb-district-filter')).to_be_hidden()
    expect(table).to_contain_text('Общий лидер')
    assert all(query.get('district_id') for query in requests
               if query.get('difficulty') == 'district')


def test_district_leaderboard_clears_old_rows_and_ignores_late_response(page, server):
    pending = {}

    def respond(route):
        district = _query(route.request.url).get('district_id')
        if district in ('tsentralny', 'kronshtadtsky'):
            pending[district] = route
        else:
            _board_response(route, 'Предыдущий лидер')

    page.route('**/api/leaderboard*', respond)
    page.goto(server)
    page.locator('#show-leaderboard-btn').click()
    table = page.locator('#leaderboard-table')
    expect(table).to_contain_text('Предыдущий лидер')
    page.locator('#leaderboard-filter [data-difficulty="district"]').click()
    select = page.locator('#lb-district-select')
    select.select_option('petrogradsky')
    expect(table).to_contain_text('Предыдущий лидер')

    with page.expect_request(lambda request: _is_board(request, district_id='tsentralny')):
        select.select_option('tsentralny')
    expect(table).to_contain_text('Загрузка')
    expect(table.locator('.leaderboard-row')).to_have_count(0)
    with page.expect_request(lambda request: _is_board(request, district_id='kronshtadtsky')):
        select.select_option('kronshtadtsky')
    expect(table).to_contain_text('Загрузка')
    expect(table.locator('.leaderboard-row')).to_have_count(0)

    _board_response(pending['kronshtadtsky'], 'Лидер Кронштадта')
    expect(table).to_contain_text('Лидер Кронштадта')
    with page.expect_response(lambda response: _is_board(
            response.request, district_id='tsentralny')) as late_response:
        _board_response(pending['tsentralny'], 'Опоздавший лидер центра')
    late_response.value.finished()
    # Даём браузеру обработать полученное тело и очередь отрисовки, без sleep.
    page.evaluate('''() => new Promise(resolve =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)))''')
    expect(select).to_have_value('kronshtadtsky')
    expect(table).to_contain_text('Лидер Кронштадта')
    expect(table).not_to_contain_text('Опоздавший лидер центра')
    expect(table.locator('.leaderboard-row:not(.header)')).to_have_count(1)


def test_completed_district_game_opens_its_own_leaderboard(page, server):
    page.goto(server)
    page.locator('#player-name').fill('E2E-район-топ')
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option('petrogradsky')
    page.locator('#district-confirm-btn').click()
    page.locator('#start-btn').click()
    _finish_game(page)
    with page.expect_request(lambda request: _is_board(
            request, difficulty='district', district_id='petrogradsky')) as board_request:
        page.locator('#final-leaderboard-btn').click()
    assert 'period' not in _query(board_request.value.url)
    expect(page.locator('#lb-district-select')).to_have_value('petrogradsky')
    expect(page.locator('#leaderboard-filter [data-difficulty="district"]')).to_have_class(
        re.compile('active'))
    expect(page.locator('#lb-period-filter [data-period="all"]')).to_have_class(
        re.compile('active'))
    expect(page.locator('#leaderboard-table')).to_contain_text('E2E-район-топ')
    assert page.evaluate('window.__ymapsStats.mapsActive') == 0

    # Просмотр чужого районного топа не меняет территорию следующей игры.
    with page.expect_response(lambda response: _is_board(
            response.request, difficulty='district', district_id='kronshtadtsky')):
        page.locator('#lb-district-select').select_option('kronshtadtsky')
    page.locator('#back-btn').click()
    expect(page.locator('#start-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#territory-value')).to_have_text('Петроградский район')
    page.locator('#district-picker-btn').click()
    expect(page.locator('#district-list')).to_have_value('petrogradsky')


@pytest.mark.parametrize('difficulty', ['center', 'medium', 'hard', 'daily'])
def test_completed_game_opens_matching_leaderboard_and_home_keeps_all(page, server, difficulty):
    page.goto(server)
    player_name = f'E2E-топ-{difficulty}'
    page.locator('#player-name').fill(player_name)
    if difficulty == 'daily':
        # Daily выбирает medium сам, даже если до запуска был выбран весь город.
        page.locator('[data-territory-index="2"]').click()
        page.locator('#daily-btn').click()
    else:
        territory_index = ['center', 'medium', 'hard'].index(difficulty)
        page.locator(f'[data-territory-index="{territory_index}"]').click()
        page.locator('#start-btn').click()
    _finish_game(page)

    def expected_board(request):
        if difficulty == 'daily':
            return urlsplit(request.url).path == '/api/daily/leaderboard'
        return _is_board(request, difficulty=difficulty)

    with page.expect_request(expected_board) as board_request:
        page.locator('#final-leaderboard-btn').click()
    query = _query(board_request.value.url)
    assert 'district_id' not in query
    assert 'period' not in query
    period = 'daily' if difficulty == 'daily' else 'all'
    expect(page.locator(f'#lb-period-filter [data-period="{period}"]')).to_have_class(
        re.compile('active'))
    expect(page.locator('#lb-district-filter')).to_be_hidden()
    if difficulty == 'daily':
        expect(page.locator('#leaderboard-filter')).to_be_hidden()
    else:
        expect(page.locator(f'#leaderboard-filter [data-difficulty="{difficulty}"]')) \
            .to_have_class(re.compile('active'))
    expect(page.locator('#leaderboard-table')).to_contain_text(player_name)

    page.locator('#back-btn').click()
    with page.expect_request(lambda request: _is_board(request)
                             and not _query(request.url)):
        page.locator('#show-leaderboard-btn').click()
    expect(page.locator('#lb-period-filter [data-period="all"]')).to_have_class(
        re.compile('active'))
    expect(page.locator('#leaderboard-filter')).to_be_visible()
    expect(page.locator('#leaderboard-filter [data-difficulty="all"]')).to_have_class(
        re.compile('active'))
    expect(page.locator('#leaderboard-table')).to_contain_text(player_name)


def test_district_leaderboard_fits_narrow_mobile_viewports(page, server):
    page.route('**/api/leaderboard*', lambda route: _board_response(route, 'ПетербургскийИгрок'))
    page.goto(server)
    page.locator('#show-leaderboard-btn').click()
    page.locator('#leaderboard-filter [data-difficulty="district"]').click()
    select = page.locator('#lb-district-select')
    select.select_option('krasnogvardeysky')
    expect(page.locator('#leaderboard-table')).to_contain_text('ПетербургскийИгрок')

    for width in (320, 390, 430, 480):
        page.set_viewport_size({'width': width, 'height': 844})
        expect(select).to_be_visible()
        expect(select).to_have_value('krasnogvardeysky')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        for selector in (
            '#lb-period-filter button', '#leaderboard-filter button',
            '#lb-district-select', '#leaderboard-table',
        ):
            assert page.locator(selector).evaluate_all('''elements => elements.every(el => {
                const rect = el.getBoundingClientRect();
                return rect.left >= 0 && rect.right <= innerWidth;
            })'''), f'{selector} выходит за экран шириной {width}'
        assert page.locator('#leaderboard-table .leaderboard-row').evaluate_all('''
            rows => rows.every(row => row.scrollWidth <= row.clientWidth + 1)
        '''), f'содержимое строки обрезается на ширине {width}'
        assert page.locator('#leaderboard-table .leaderboard-row').evaluate_all('''
            rows => rows.every(row => {
                const bounds = row.getBoundingClientRect();
                const fields = Array.from(row.querySelectorAll(
                    '.leaderboard-name, .leaderboard-score, .leaderboard-date'
                )).map(field => field.getBoundingClientRect())
                    .filter(rect => rect.width && rect.height);
                return fields.every((rect, index) =>
                    rect.left >= bounds.left - 1 && rect.right <= bounds.right + 1 &&
                    rect.top >= bounds.top - 1 && rect.bottom <= bounds.bottom + 1 &&
                    fields.slice(index + 1).every(other =>
                        Math.min(rect.right, other.right) - Math.max(rect.left, other.left) <= 1 ||
                        Math.min(rect.bottom, other.bottom) - Math.max(rect.top, other.top) <= 1)
                );
            })
        '''), f'имя, очки или дата перекрываются либо выходят из строки на ширине {width}'
