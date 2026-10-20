"""Если сервер уже знает место панорамы, клиент не может его подменить.

Точки пула, вызова дня и челленджа — это ранее сыгранные съёмки. Раньше
клиент мог объявить «панорамой» любое место в радиусе 1 км (например, свою
догадку) и получить 5000 очков, а друг в челлендже — сдвинуть точки автора.
"""
import pytest

import pool
from game_logic import KNOWN_PANORAMA_DRIFT_KM, MAX_ACTUAL_POINT_DRIFT_KM
from helpers import round_body
from models import GameRound, VerifiedPoint, db

# Сетка 4×4 с шагом ~220 × 220 м у Дворцовой: все точки внутри center (3 км).
POOL_GRID = [(59.930 + 0.002 * i, 30.300 + 0.004 * j)
             for i in range(4) for j in range(4)]
# ~0.9 км к северу: в пределах старого допуска «дрейфа» в 1 км.
FAKE_SHIFT_LAT = 0.008


@pytest.fixture()
def full_pool(app, monkeypatch):
    """Пул достаточного размера, из которого обычная игра берёт все раунды."""
    with app.app_context():
        for lat, lon in POOL_GRID:
            pool.add_verified_point(lat, lon)
    monkeypatch.setattr(pool, 'POOL_USE_PROBABILITY', 1.0)


def _is_pool_point(lat, lon):
    return any(abs(lat - p_lat) < 1e-9 and abs(lon - p_lon) < 1e-9
               for p_lat, p_lon in POOL_GRID)


def _ready(client, location):
    response = client.post('/api/game/ready', json=round_body(location))
    assert response.status_code == 200


def _cheat_guess(client, location):
    """Догадка и «панорама» в одной и той же точке в 0,9 км от выданной."""
    fake = (location['latitude'] + FAKE_SHIFT_LAT, location['longitude'])
    return client.post('/api/game/guess', json=round_body(
        location, latitude=fake[0], longitude=fake[1],
        panorama_latitude=fake[0], panorama_longitude=fake[1],
    )).get_json()


def test_pool_round_scores_against_the_pool_point(client, full_pool):
    location = client.post('/api/game/start', json={'difficulty': 'center'}).get_json()['location']
    assert _is_pool_point(location['latitude'], location['longitude'])
    # Клиент сразу считает съёмку в 100+ м от известного места исчезнувшей.
    assert location['max_panorama_drift_km'] == KNOWN_PANORAMA_DRIFT_KM
    _ready(client, location)

    result = _cheat_guess(client, location)

    assert result['correct_location'] == {
        'latitude': location['latitude'], 'longitude': location['longitude'],
    }
    assert result['distance_km'] == pytest.approx(0.89, abs=0.01)
    assert result['score'] < 5000


def test_explore_round_still_uses_the_reported_panorama(client):
    location = client.post('/api/game/start', json={'difficulty': 'center'}).get_json()['location']
    assert location['source'] == 'explore'
    assert location['max_panorama_drift_km'] == MAX_ACTUAL_POINT_DRIFT_KM
    pano = (location['latitude'] + 0.004, location['longitude'])

    result = client.post('/api/game/guess', json=round_body(
        location, latitude=pano[0], longitude=pano[1],
        panorama_latitude=pano[0], panorama_longitude=pano[1],
    )).get_json()

    assert result['correct_location'] == {'latitude': pano[0], 'longitude': pano[1]}
    assert result['score'] == 5000


def test_challenge_friend_cannot_move_the_authors_points(client, app):
    author_location = client.post('/api/game/start', json={
        'difficulty': 'center', 'player_name': 'Автор',
    }).get_json()['location']
    author_panoramas = []
    for _ in range(5):
        pano = (author_location['latitude'] + 0.001, author_location['longitude'])
        author_panoramas.append(pano)
        result = client.post('/api/game/guess', json=round_body(
            author_location, latitude=pano[0], longitude=pano[1],
            panorama_latitude=pano[0], panorama_longitude=pano[1],
        )).get_json()
        author_location = result.get('next_location')
    token = client.get('/api/game/results').get_json()['challenge_token']

    friend = app.test_client()
    location = friend.post('/api/game/start', json={
        'challenge_token': token, 'player_name': 'Друг',
    }).get_json()['location']
    for pano in author_panoramas:
        assert (location['latitude'], location['longitude']) == pano
        assert location['max_panorama_drift_km'] == KNOWN_PANORAMA_DRIFT_KM
        result = _cheat_guess(friend, location)
        assert result['correct_location'] == {'latitude': pano[0], 'longitude': pano[1]}
        assert result['score'] < 5000
        location = result.get('next_location')


def test_daily_pool_points_are_known(client, full_pool):
    location = client.post('/api/game/start', json={'daily': True}).get_json()['location']
    assert _is_pool_point(location['latitude'], location['longitude'])
    assert location['max_panorama_drift_km'] == KNOWN_PANORAMA_DRIFT_KM

    result = _cheat_guess(client, location)

    assert result['correct_location'] == {
        'latitude': location['latitude'], 'longitude': location['longitude'],
    }
    assert result['score'] < 5000


def test_validate_rejects_a_moved_panorama_and_keeps_the_known_point(client, app, full_pool):
    location = client.post('/api/game/start', json={'difficulty': 'center'}).get_json()['location']
    known = (location['latitude'], location['longitude'])

    moved = client.post('/api/game/validate_panorama', json=round_body(
        location, latitude=known[0] + 0.003, longitude=known[1],
    )).get_json()
    # Клиент заменит такую точку как пропавшую съёмку (no_coverage).
    assert moved == {'valid': False, 'reason': 'panorama_moved'}

    nearby = client.post('/api/game/validate_panorama', json=round_body(
        location, latitude=known[0] + 0.00005, longitude=known[1],
    )).get_json()
    assert nearby == {'valid': True}
    with app.app_context():
        rnd = db.session.get(GameRound, location['round_id'])
        assert (rnd.actual_latitude, rnd.actual_longitude) == known


def test_pool_replacement_after_no_coverage_is_known(client, app):
    with app.app_context():
        pool.add_verified_point(*POOL_GRID[0])
    location = client.post('/api/game/start', json={'difficulty': 'center'}).get_json()['location']
    assert location['source'] == 'explore'

    replaced = client.post('/api/game/skip_location', json=round_body(
        location, reason='no_coverage',
    )).get_json()
    assert (replaced['latitude'], replaced['longitude']) == POOL_GRID[0]
    assert replaced['max_panorama_drift_km'] == KNOWN_PANORAMA_DRIFT_KM

    result = _cheat_guess(client, replaced)
    assert result['correct_location'] == {
        'latitude': POOL_GRID[0][0], 'longitude': POOL_GRID[0][1],
    }


def test_explore_replacement_forgets_the_previous_known_point(client, app, full_pool):
    location = client.post('/api/game/start', json={'difficulty': 'center'}).get_json()['location']
    assert location['max_panorama_drift_km'] == KNOWN_PANORAMA_DRIFT_KM
    # Пул исчерпан: восстановление генерирует новое место.
    with app.app_context():
        VerifiedPoint.query.delete()
        db.session.commit()

    replaced = client.post('/api/game/skip_location', json=round_body(
        location, reason='no_coverage',
    )).get_json()

    assert replaced['max_panorama_drift_km'] == MAX_ACTUAL_POINT_DRIFT_KM
    with app.app_context():
        rnd = db.session.get(GameRound, location['round_id'])
        assert rnd.actual_latitude is None
