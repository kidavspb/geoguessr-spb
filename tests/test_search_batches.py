"""Продолжение поиска сохраняет прогресс, таймер и версию точки."""
import pytest

from models import GameRound, GameSession, db


def _exhaust(client, location):
    for _ in range(location['max_location_skips'] - location['location_version']):
        response = client.post('/api/game/skip_location', json=location)
        assert response.status_code == 200
        location = response.get_json()
    response = client.post('/api/game/skip_location', json=location)
    assert response.status_code == 429
    assert response.get_json()['reason'] == 'search_batch_exhausted'
    return location


@pytest.mark.parametrize('territory', [
    {'difficulty': 'medium'},
    {'difficulty': 'district', 'district_id': 'kurortny'},
])
def test_continue_search_preserves_game_and_bounds_each_series(client, app, territory):
    start = client.post('/api/game/start', json={**territory, 'time_limit': 60}).get_json()
    first = start['location']
    assert client.post('/api/game/validate_panorama', json=first).get_json()['valid']
    assert client.post('/api/game/ready', json=first).status_code == 200
    result = client.post('/api/game/guess', json=first).get_json()
    location = result['next_location']
    exhausted = _exhaust(client, location)

    for batch in (2, 3):
        response = client.post('/api/game/continue_search', json=exhausted)
        assert response.status_code == 200
        continued = response.get_json()
        assert continued['round_id'] == location['round_id']
        assert continued['round'] == 2
        assert continued['search_batch'] == batch
        assert continued['max_location_skips'] == batch * 10
        assert continued['location_version'] == exhausted['location_version']
        assert continued.get('district_id') == territory.get('district_id')
        with app.app_context():
            game = GameSession.query.one()
            assert game.current_round == game.rounds_played == 1
            assert game.total_score == result['score'] == 5000
            assert game.difficulty == territory['difficulty']
            assert game.district_id == territory.get('district_id')
            assert db.session.get(GameRound, location['round_id']).started_at is None
        exhausted = _exhaust(client, continued)
        # Поздний повтор старой кнопки не открывает ещё одну серию.
        replay = client.post('/api/game/continue_search', json=location).get_json()
        assert replay['replayed']
        assert replay['search_batch'] == batch


def test_continuation_requires_exhaustion_and_explicit_current_identifiers(client, app):
    location = client.post('/api/game/start', json={}).get_json()['location']
    assert client.post('/api/game/continue_search', json=location).status_code == 409
    exhausted = _exhaust(client, location)
    for missing in ('round_id', 'search_batch', 'location_version'):
        body = {key: value for key, value in exhausted.items() if key != missing}
        assert client.post('/api/game/continue_search', json=body).status_code == 400
    for changes in ({'search_batch': 2}, {'location_version': 9}, {'round_id': 999999}):
        response = client.post('/api/game/continue_search', json={**exhausted, **changes})
        assert response.status_code == 409
    with app.app_context():
        assert db.session.get(GameRound, location['round_id']).search_batch == 1


def test_started_round_cannot_reroll_or_extend_search(client, app):
    location = client.post('/api/game/start', json={'time_limit': 60}).get_json()['location']
    exhausted = _exhaust(client, location)
    assert client.post('/api/game/ready', json=exhausted).status_code == 200
    assert client.post('/api/game/continue_search', json=exhausted).status_code == 409
    skip = client.post('/api/game/skip_location', json=exhausted)
    assert skip.status_code == 409
    assert skip.get_json()['reason'] == 'round_started'
    with app.app_context():
        rnd = db.session.get(GameRound, location['round_id'])
        assert rnd.started_at is not None
        assert rnd.skips == 10
        assert rnd.search_batch == 1


def test_old_version_stays_invalid_after_search_continuation(client):
    location = client.post('/api/game/start', json={}).get_json()['location']
    exhausted = _exhaust(client, location)
    continued = client.post('/api/game/continue_search', json=exhausted).get_json()
    replacement = client.post('/api/game/skip_location', json=continued).get_json()
    assert replacement['location_version'] == 11
    for endpoint in ('ready', 'guess', 'validate_panorama', 'panorama_metric'):
        assert client.post(f'/api/game/{endpoint}', json=exhausted).status_code == 409
    assert client.post('/api/game/guess', json=replacement).status_code == 200
    assert client.post('/api/game/continue_search', json=continued).status_code == 409
    client.post('/api/game/start', json={})
    assert client.post('/api/game/continue_search', json=continued).status_code == 409
