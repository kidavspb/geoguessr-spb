"""Old browser tabs must reload before starting games with new rules."""
import pytest

from models import CURRENT_PANORAMA_RULES_VERSION, GameRound, GameSession, db


@pytest.mark.parametrize('headers', [
    {'Origin': 'http://localhost'},
    {'Sec-Fetch-Mode': 'cors'},
])
@pytest.mark.parametrize('capability', [{}, {'client_panorama_rules_version': 0}])
def test_outdated_browser_cannot_create_game(client, app, headers, capability):
    response = client.post('/api/game/start', headers=headers, json={
        'difficulty': 'hard', **capability,
    })

    assert response.status_code == 409
    assert response.get_json() == {
        'error': 'Игра обновилась. Обновите страницу перед началом новой игры',
        'reason': 'client_outdated',
    }
    with app.app_context():
        assert GameSession.query.count() == 0
        assert GameRound.query.count() == 0
    with client.session_transaction() as session:
        assert 'game_id' not in session


def test_current_browser_starts_game_with_current_rules(client, app):
    response = client.post('/api/game/start', headers={
        'Origin': 'http://localhost', 'Sec-Fetch-Mode': 'cors',
    }, json={
        'difficulty': 'hard',
        'client_panorama_rules_version': CURRENT_PANORAMA_RULES_VERSION,
        'panorama_rules_version': 0,
    })

    assert response.status_code == 200
    result = response.get_json()
    assert result['location']['requires_spatial_validation'] is True
    with app.app_context():
        game = db.session.get(GameSession, result['game_id'])
        assert game.panorama_rules_version == CURRENT_PANORAMA_RULES_VERSION


@pytest.mark.parametrize('capability', [{}, {'client_panorama_rules_version': 0}])
def test_direct_api_keeps_current_rules_without_browser_capability(
        client, app, capability):
    response = client.post('/api/game/start', json={
        'difficulty': 'hard', 'panorama_rules_version': 0, **capability,
    })

    assert response.status_code == 200
    result = response.get_json()
    assert result['location']['requires_spatial_validation'] is True
    with app.app_context():
        game = db.session.get(GameSession, result['game_id'])
        assert game.panorama_rules_version == CURRENT_PANORAMA_RULES_VERSION
    # Отсутствующий или нулевой capability не отменяет обязательный preflight.
    ready = client.post('/api/game/ready', json={
        'round_id': result['location']['round_id'],
    })
    assert ready.status_code == 409
    assert ready.get_json()['reason'] == 'panorama_not_validated'
