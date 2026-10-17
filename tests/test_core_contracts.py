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
