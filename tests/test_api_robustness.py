"""Malformed requests must not mutate games or turn input errors into 500s."""
import math

import pytest

from game_logic import haversine_distance, parse_coords, parse_time_limit
from models import GameSession, db


@pytest.mark.parametrize('endpoint', [
    'start', 'ready', 'skip_location', 'set_actual_point', 'validate_panorama',
    'set_address', 'panorama_metric', 'guess',
])
@pytest.mark.parametrize('body', ['[1]', 'true', '42', '"text"', 'null', '{broken'])
def test_game_api_rejects_non_object_json(client, app, endpoint, body):
    client.post('/api/game/start', json={})
    response = client.post(f'/api/game/{endpoint}', data=body,
                           content_type='application/json')
    assert response.status_code == 400
    assert response.is_json
    with app.app_context():
        assert GameSession.query.count() == 1
        assert GameSession.query.first().current_round == 0


@pytest.mark.parametrize('value', [[], {}, True, 1.5, float('inf'), 10**100])
def test_invalid_round_id_is_rejected(client, value):
    client.post('/api/game/start', json={})
    response = client.post('/api/game/ready', json={'round_id': value})
    assert response.status_code == 400
    assert response.is_json


@pytest.mark.parametrize('value', [[], {}, True, -1, 0.5, float('inf'), 10**100])
@pytest.mark.parametrize('endpoint', ['skip_location', 'validate_panorama', 'ready', 'guess'])
def test_invalid_location_version_is_rejected(client, endpoint, value):
    location = client.post('/api/game/start', json={}).get_json()['location']
    response = client.post(f'/api/game/{endpoint}', json={
        'round_id': location['round_id'], 'location_version': value,
        'latitude': location['latitude'], 'longitude': location['longitude'],
    })
    assert response.status_code == 400


@pytest.mark.parametrize('value', [[], {}, True, 1.5])
def test_difficulty_must_be_a_string(client, value):
    response = client.post('/api/game/start', json={'difficulty': value})
    assert response.status_code == 400


def test_oversized_coordinates_and_nonfinite_time_limit():
    assert parse_coords({'latitude': 10**400, 'longitude': 30}) is None
    assert parse_coords({'latitude': True, 'longitude': 30}) is None
    assert parse_time_limit(float('inf')) is None


def test_antipodal_distance_is_finite():
    assert math.isclose(haversine_distance(0.08, 30.3, -0.08, -149.7),
                        math.pi * 6371)


def test_non_ascii_admin_header_is_denied(client):
    response = client.get('/api/admin/points', headers={'X-Admin-Key': 'é'})
    assert response.status_code == 403


@pytest.mark.parametrize('endpoint', ['ready', 'guess', 'panorama_metric'])
def test_old_candidate_cannot_change_replacement(client, app, endpoint):
    location = client.post('/api/game/start', json={'time_limit': 60}).get_json()['location']
    client.post('/api/game/skip_location', json={
        'round_id': location['round_id'], 'location_version': 0,
    })
    response = client.post(f'/api/game/{endpoint}', json={
        'round_id': location['round_id'], 'location_version': 0,
        'latitude': location['latitude'], 'longitude': location['longitude'],
        'status': 'ready', 'ready_ms': 12,
    })
    assert response.status_code == 409
    with app.app_context():
        from models import GameRound
        rnd = db.session.get(GameRound, location['round_id'])
        assert rnd.started_at is None
        assert rnd.answered_at is None
        assert rnd.panorama_status is None
