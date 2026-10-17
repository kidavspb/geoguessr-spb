"""Точные контракты ядра игры, которые раньше не закреплял ни один тест.

Мутационная проверка показала, что набор пропускал изменение формулы очков,
расстояния по долготе, границ таймера и принадлежности раунда игре.
"""
import math
from datetime import timedelta

import pytest

import game_logic


@pytest.mark.parametrize('distance_km, score', [
    (0, 5000),
    (0.05, 5000),
    (0.051, 4915),
    (0.2, 4677),
    (1, 3582),
    (3, 1839),
    (10, 178),
    (25, 1),
    (25.6, 0),
])
def test_score_table(distance_km, score):
    assert game_logic.calculate_score(distance_km) == score


def test_haversine_along_parallel():
    # На широте Петербурга градус долготы почти вдвое короче градуса широты.
    distance = game_logic.haversine_distance(59.94, 30.0, 59.94, 31.0)
    assert math.isclose(distance, 55.70, abs_tol=0.01)


def _start(client, **extra):
    body = {'difficulty': 'center', **extra}
    data = client.post('/api/game/start', json=body).get_json()
    return data['location']


def _shift_round_start(app_module, round_id, seconds):
    from models import GameRound, db
    with app_module.app.app_context():
        rnd = db.session.get(GameRound, round_id)
        rnd.started_at = rnd.started_at - timedelta(seconds=seconds)
        db.session.commit()


@pytest.mark.parametrize('endpoint', ['guess', 'ready', 'skip_location'])
def test_round_of_another_game_is_rejected(app, endpoint):
    owner = app.test_client()
    intruder = app.test_client()
    owner_location = _start(owner)
    _start(intruder)

    response = intruder.post(f'/api/game/{endpoint}', json={
        'round_id': owner_location['round_id'],
        'location_version': 0,
        'latitude': owner_location['latitude'],
        'longitude': owner_location['longitude'],
    })
    assert response.status_code == 409

    # Игра владельца не изменилась.
    current = owner.get('/api/game/location').get_json()
    assert current['round_id'] == owner_location['round_id']
    assert current['location_version'] == 0


def test_future_round_is_rejected(client):
    first = _start(client)
    response = client.post('/api/game/guess', json={
        'round_id': first['round_id'] + 1,
        'latitude': first['latitude'],
        'longitude': first['longitude'],
    })
    assert response.status_code == 409
    assert client.get('/api/game/location').get_json()['round'] == 1


def test_ready_deadline_is_started_plus_limit_and_stable(client):
    location = _start(client, time_limit=60)
    first = client.post('/api/game/ready', json={
        'round_id': location['round_id'], 'location_version': 0,
    }).get_json()
    assert first['deadline_ms'] == first['started_at_ms'] + 60_000

    again = client.post('/api/game/ready', json={
        'round_id': location['round_id'], 'location_version': 0,
    }).get_json()
    assert again['started_at_ms'] == first['started_at_ms']
    assert again['deadline_ms'] == first['deadline_ms']


@pytest.mark.parametrize('elapsed, expected_remaining_s', [(0, 60), (25, 35), (90, 0)])
def test_ready_reports_remaining_time(client, app_module, elapsed, expected_remaining_s):
    """Клиент считает остаток своими часами, поэтому сервер отдаёт длительность."""
    location = _start(client, time_limit=60)
    body = {'round_id': location['round_id'], 'location_version': 0}
    client.post('/api/game/ready', json=body)
    if elapsed:
        _shift_round_start(app_module, location['round_id'], elapsed)

    remaining_ms = client.post('/api/game/ready', json=body).get_json()['remaining_ms']
    assert abs(remaining_ms - expected_remaining_s * 1000) < 2000


def test_ready_without_time_limit_has_no_remaining_time(client):
    location = _start(client)
    data = client.post('/api/game/ready', json={
        'round_id': location['round_id'], 'location_version': 0,
    }).get_json()
    assert data['remaining_ms'] is None


# Запас задан литералом: тест должен заметить изменение самой константы.
@pytest.mark.parametrize('elapsed, timed_out', [
    (60 + 20 - 3, False),
    (60 + 20 + 3, True),
])
def test_time_limit_boundary_includes_grace(client, app_module, elapsed, timed_out):
    location = _start(client, time_limit=60)
    client.post('/api/game/ready', json={
        'round_id': location['round_id'], 'location_version': 0,
    })
    _shift_round_start(app_module, location['round_id'], elapsed)

    result = client.post('/api/game/guess', json={
        'round_id': location['round_id'],
        'location_version': 0,
        'latitude': location['latitude'],
        'longitude': location['longitude'],
    }).get_json()
    assert result['timed_out'] is timed_out
    assert (result['score'] == 0) is timed_out


@pytest.mark.parametrize('endpoint', ['validate_panorama', 'ready', 'guess', 'skip_location'])
def test_stale_location_version_is_marked_for_resync(client, endpoint):
    """Клиент, потерявший ответ на замену, должен понять, что надо перечитать точку."""
    location = _start(client)
    client.post('/api/game/skip_location', json={
        'round_id': location['round_id'], 'location_version': 0, 'reason': 'no_coverage',
    })
    stale_version = 5 if endpoint == 'skip_location' else 0
    response = client.post(f'/api/game/{endpoint}', json={
        'round_id': location['round_id'],
        'location_version': stale_version,
        'latitude': location['latitude'],
        'longitude': location['longitude'],
    })
    assert response.status_code == 409
    assert response.get_json()['reason'] == 'stale_location'


ROUND_ENDPOINTS = ['guess', 'ready', 'skip_location', 'validate_panorama',
                   'continue_search', 'set_address', 'panorama_metric']


@pytest.mark.parametrize('endpoint', ROUND_ENDPOINTS)
def test_round_id_is_required(client, endpoint):
    location = _start(client)
    response = client.post(f'/api/game/{endpoint}', json={
        'location_version': 0, 'search_batch': 1, 'address': 'Невский пр., 1',
        'latitude': location['latitude'], 'longitude': location['longitude'],
    })
    assert response.status_code == 400
    assert response.get_json() == {'error': 'Нужно поле round_id'}


@pytest.mark.parametrize('endpoint', [e for e in ROUND_ENDPOINTS if e != 'set_address'])
def test_location_version_is_required(client, endpoint):
    """Без версии повтор запроса нельзя отличить от нового — запрос отклоняется."""
    location = _start(client)
    response = client.post(f'/api/game/{endpoint}', json={
        'round_id': location['round_id'], 'search_batch': 1,
        'latitude': location['latitude'], 'longitude': location['longitude'],
    })
    assert response.status_code == 400
    assert response.get_json() == {'error': 'Нужно поле location_version'}
    current = client.get('/api/game/location').get_json()
    assert (current['round'], current['location_version']) == (1, 0)


def test_post_without_json_body_is_rejected(client):
    response = client.post('/api/game/start')
    assert response.status_code == 400
    assert response.get_json() == {'error': 'Ожидается JSON-объект'}


def test_removed_set_actual_point_endpoint_is_gone(client):
    location = _start(client)
    response = client.post('/api/game/set_actual_point', json={
        'round_id': location['round_id'], 'location_version': 0,
        'latitude': location['latitude'], 'longitude': location['longitude'],
    })
    assert response.status_code == 404


def _completed_game(app_module, name, score, completed_at, **fields):
    from models import GameSession, db
    with app_module.app.app_context():
        db.session.add(GameSession(
            player_name=name, total_score=score, difficulty='medium',
            current_round=5, completed_at=completed_at, **fields,
        ))
        db.session.commit()


def test_leaderboard_ties_keep_the_earliest_finish_first(client, app_module):
    from datetime import datetime
    _completed_game(app_module, 'Поздний', 20000, datetime(2026, 10, 10, 12, 0))
    _completed_game(app_module, 'Ранний', 20000, datetime(2026, 10, 9, 12, 0))
    _completed_game(app_module, 'Лидер', 21000, datetime(2026, 10, 11, 12, 0))

    names = [row['player_name'] for row in client.get('/api/leaderboard').get_json()['leaderboard']]
    assert names == ['Лидер', 'Ранний', 'Поздний']


def test_daily_leaderboard_ties_keep_the_earliest_finish_first(client, app_module):
    from datetime import datetime
    from daily import today_msk
    today = today_msk()
    _completed_game(app_module, 'Поздний', 20000, datetime(2026, 10, 10, 12, 0), daily_date=today)
    _completed_game(app_module, 'Ранний', 20000, datetime(2026, 10, 10, 9, 0), daily_date=today)

    rows = client.get('/api/daily/leaderboard').get_json()['leaderboard']
    assert [row['player_name'] for row in rows] == ['Ранний', 'Поздний']


def test_leaderboard_date_is_moscow_date(client, app_module):
    from datetime import datetime
    # 22:30 UTC = 01:30 следующего дня по Москве.
    _completed_game(app_module, 'Полуночник', 15000, datetime(2026, 10, 17, 22, 30))
    row = client.get('/api/leaderboard').get_json()['leaderboard'][0]
    assert row['date'] == '18.10.2026'


def test_location_after_last_round_reports_game_over(client):
    location = _start(client)
    for _ in range(game_logic.ROUNDS_PER_GAME):
        result = client.post('/api/game/guess', json={
            'round_id': location['round_id'],
            'location_version': location['location_version'],
            'latitude': location['latitude'],
            'longitude': location['longitude'],
        }).get_json()
        location = result.get('next_location')
    assert result['is_game_over'] is True

    response = client.get('/api/game/location')
    assert response.status_code == 400
    assert response.get_json()['game_over'] is True
