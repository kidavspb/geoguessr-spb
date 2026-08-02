"""Тесты бэкенда GeoGuessr СПб.

Покрывают чистую логику (расстояние, очки, валидация координат, генерация
точек) и ключевые сценарии API (полный проход игры, антифрод, фильтр лидеров).
"""
import math


# --------------------------------------------------------------------------
# Чистые функции
# --------------------------------------------------------------------------

def test_haversine_same_point_is_zero(app_module):
    assert app_module.haversine_distance(59.94, 30.31, 59.94, 30.31) == 0


def test_haversine_one_degree_latitude(app_module):
    # 1° широты ≈ 111.2 км по меридиану.
    dist = app_module.haversine_distance(59.0, 30.0, 60.0, 30.0)
    assert math.isclose(dist, 111.19, rel_tol=0.01)


def test_calculate_score_bounds(app_module):
    max_score = app_module.MAX_SCORE_PER_ROUND
    assert app_module.calculate_score(0) == max_score          # точно в цель
    assert app_module.calculate_score(0.04) == max_score       # в пределах 50 м
    assert app_module.calculate_score(app_module.MAX_DISTANCE_KM) == 0
    assert app_module.calculate_score(1000) == 0               # очень далеко
    mid = app_module.calculate_score(3)
    assert 0 < mid < max_score


def test_calculate_score_is_monotonic(app_module):
    scores = [app_module.calculate_score(d) for d in (0.1, 1, 3, 5, 10, 20)]
    assert scores == sorted(scores, reverse=True)


def test_parse_coords_valid(app_module):
    assert app_module.parse_coords({'latitude': 59.9, 'longitude': 30.3}) == (59.9, 30.3)
    # Строки, приводимые к float, тоже принимаются.
    assert app_module.parse_coords({'latitude': '59.9', 'longitude': '30.3'}) == (59.9, 30.3)


def test_parse_coords_invalid(app_module):
    assert app_module.parse_coords(None) is None
    assert app_module.parse_coords('not a dict') is None
    assert app_module.parse_coords({'latitude': 59.9}) is None            # нет долготы
    assert app_module.parse_coords({'latitude': 'x', 'longitude': 1}) is None
    assert app_module.parse_coords({'latitude': 200, 'longitude': 30}) is None  # вне диапазона
    assert app_module.parse_coords({'latitude': float('nan'), 'longitude': 30}) is None


def test_generate_random_point_within_bounds(app_module):
    bounds = app_module.SPB_BOUNDS
    for difficulty in app_module.DIFFICULTY_SETTINGS:
        for _ in range(200):
            lat, lon = app_module.generate_random_point(difficulty)
            assert bounds['lat_min'] <= lat <= bounds['lat_max']
            assert bounds['lon_min'] <= lon <= bounds['lon_max']


def test_difficulty_name(app_module):
    assert app_module.difficulty_name('center') == 'Центр'
    assert app_module.difficulty_name('unknown') == 'unknown'


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

def test_location_requires_started_game(client):
    resp = client.get('/api/game/location')
    assert resp.status_code == 400


def test_guess_requires_started_game(client):
    resp = client.post('/api/game/guess', json={'latitude': 59.9, 'longitude': 30.3})
    assert resp.status_code == 400


def test_start_game_normalizes_unknown_difficulty(client):
    resp = client.post('/api/game/start', json={'difficulty': 'bogus'})
    assert resp.status_code == 200
    assert resp.get_json()['difficulty'] == 'medium'


def test_full_game_flow_perfect_score(client):
    rounds = 5
    start = client.post('/api/game/start',
                        json={'player_name': 'Тестер', 'difficulty': 'center'})
    assert start.status_code == 200
    assert start.get_json()['total_rounds'] == rounds

    last = None
    for i in range(rounds):
        loc = client.get('/api/game/location').get_json()
        # Угадываем ровно в сгенерированную точку → расстояние 0, максимум очков.
        last = client.post('/api/game/guess', json={
            'latitude': loc['latitude'],
            'longitude': loc['longitude'],
        }).get_json()
        assert last['score'] == 5000

    assert last['is_game_over'] is True

    results = client.get('/api/game/results').get_json()
    assert results['player_name'] == 'Тестер'
    assert results['total_score'] == 5000 * rounds
    assert results['difficulty'] == 'center'
    assert results['difficulty_name'] == 'Центр'
    assert len(results['rounds']) == rounds


def test_leaderboard_filters_by_difficulty(client):
    # Доводим одну игру в режиме center до конца.
    client.post('/api/game/start', json={'player_name': 'Гонщик', 'difficulty': 'center'})
    for _ in range(5):
        loc = client.get('/api/game/location').get_json()
        client.post('/api/game/guess',
                    json={'latitude': loc['latitude'], 'longitude': loc['longitude']})

    all_board = client.get('/api/leaderboard').get_json()
    assert any(e['player_name'] == 'Гонщик' for e in all_board['leaderboard'])
    entry = next(e for e in all_board['leaderboard'] if e['player_name'] == 'Гонщик')
    assert entry['difficulty'] == 'center'
    assert entry['difficulty_name'] == 'Центр'

    center_board = client.get('/api/leaderboard?difficulty=center').get_json()
    assert center_board['difficulty'] == 'center'
    assert any(e['player_name'] == 'Гонщик' for e in center_board['leaderboard'])

    hard_board = client.get('/api/leaderboard?difficulty=hard').get_json()
    assert all(e['player_name'] != 'Гонщик' for e in hard_board['leaderboard'])


def test_unfinished_game_not_in_leaderboard(client):
    client.post('/api/game/start', json={'player_name': 'Недоигравший', 'difficulty': 'medium'})
    client.get('/api/game/location')  # старт без единого ответа
    board = client.get('/api/leaderboard').get_json()
    assert all(e['player_name'] != 'Недоигравший' for e in board['leaderboard'])


def test_set_actual_point_antifraud(client):
    client.post('/api/game/start', json={'difficulty': 'medium'})
    loc = client.get('/api/game/location').get_json()
    gen_lat, gen_lon = loc['latitude'], loc['longitude']

    # Точка далеко от серверной (≈ полградуса долготы ≈ 28 км) — отклоняется.
    far = client.post('/api/game/set_actual_point',
                      json={'latitude': gen_lat, 'longitude': gen_lon + 0.5})
    assert far.status_code == 200
    assert far.get_json()['success'] is False
    assert far.get_json()['reason'] == 'too_far'

    # Точка рядом с серверной — принимается.
    near = client.post('/api/game/set_actual_point',
                       json={'latitude': gen_lat, 'longitude': gen_lon})
    assert near.get_json()['success'] is True


def test_set_actual_point_rejects_bad_coords(client):
    client.post('/api/game/start', json={'difficulty': 'medium'})
    client.get('/api/game/location')
    resp = client.post('/api/game/set_actual_point', json={'latitude': 'nope'})
    assert resp.status_code == 400


def test_non_finite_coordinates_are_rejected(client):
    client.post('/api/game/start', json={'difficulty': 'medium'})
    response = client.post('/api/game/guess', json={
        'latitude': 'Infinity', 'longitude': 30.3,
    })
    assert response.status_code == 400


def test_skip_location_regenerates_point(client):
    client.post('/api/game/start', json={'difficulty': 'medium'})
    first = client.get('/api/game/location').get_json()
    skipped = client.post('/api/game/skip_location').get_json()
    # Раунд тот же, но координаты пересгенерированы (с подавляющей вероятностью иные).
    assert skipped['round'] == first['round']
    assert (skipped['latitude'], skipped['longitude']) != (first['latitude'], first['longitude'])
