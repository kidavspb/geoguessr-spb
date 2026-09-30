"""Compatibility of games opened before the panorama rules rollout."""
import pytest

from models import GameRound, GameSession, db, utcnow


@pytest.mark.parametrize('already_started', [False, True])
@pytest.mark.parametrize('stored_actual', [False, True])
def test_legacy_hard_game_finishes_with_outside_and_repeated_panorama(
        client, app, already_started, stored_actual):
    inside = (59.9140, 30.2422)
    outside = (59.915318, 30.242204)
    with app.app_context():
        game = GameSession(difficulty='hard', panorama_rules_version=0)
        db.session.add(game)
        db.session.flush()
        game_id = game.id
        for number in range(1, 6):
            db.session.add(GameRound(
                session_id=game_id, round_number=number,
                gen_latitude=inside[0], gen_longitude=inside[1],
                actual_latitude=outside[0] if stored_actual else None,
                actual_longitude=outside[1] if stored_actual else None,
                started_at=utcnow() if already_started and number == 1 else None,
            ))
        db.session.commit()
    with client.session_transaction() as session:
        session['game_id'] = game_id

    for number in range(1, 6):
        location = client.get('/api/game/location').get_json()
        assert location['round'] == number
        assert location['requires_spatial_validation'] is False
        assert client.post('/api/game/ready', json={
            'round_id': location['round_id'],
        }).status_code == 200
        payload = {
            'round_id': location['round_id'],
            'latitude': outside[0], 'longitude': outside[1],
            'panorama_latitude': outside[0], 'panorama_longitude': outside[1],
        }
        response = client.post('/api/game/guess', json=payload)
        assert response.status_code == 200
        result = response.get_json()
        assert result['score'] == 5000
        assert result['total_score'] == number * 5000
        assert client.post('/api/game/guess', json=payload).get_json()['replayed']
    assert result['is_game_over'] is True


def test_start_cannot_opt_out_of_current_panorama_rules(client, app):
    result = client.post('/api/game/start', json={
        'difficulty': 'hard', 'panorama_rules_version': 0,
    }).get_json()
    assert result['location']['requires_spatial_validation'] is True
    with app.app_context():
        assert db.session.get(GameSession, result['game_id']).panorama_rules_version == 1


@pytest.mark.parametrize('rules_version', [0, 1])
def test_challenge_inherits_source_rules(client, app, rules_version):
    with app.app_context():
        source = GameSession(
            difficulty='hard', panorama_rules_version=rules_version,
            completed_at=utcnow(), current_round=5, rounds_played=5,
            challenge_token='rules-source',
        )
        db.session.add(source)
        db.session.flush()
        for number in range(1, 6):
            db.session.add(GameRound(
                session_id=source.id, round_number=number,
                gen_latitude=59.94 + number * 0.002, gen_longitude=30.32,
                actual_latitude=59.94 + number * 0.002, actual_longitude=30.32,
                answered_at=utcnow(),
            ))
        db.session.commit()
    result = client.post('/api/game/start', json={
        'challenge_token': 'rules-source',
        'panorama_rules_version': 1 - rules_version,
    }).get_json()
    assert result['location']['requires_spatial_validation'] is bool(rules_version)
    with app.app_context():
        assert db.session.get(GameSession, result['game_id']).panorama_rules_version == rules_version


def test_legacy_rules_keep_district_boundary_and_distance_checks(client, app):
    result = client.post('/api/game/start', json={
        'difficulty': 'district', 'district_id': 'petrogradsky',
    }).get_json()
    with app.app_context():
        game = db.session.get(GameSession, result['game_id'])
        game.panorama_rules_version = 0
        rnd = db.session.get(GameRound, result['location']['round_id'])
        rnd.gen_latitude, rnd.gen_longitude = 59.9457, 30.3159
        db.session.commit()
    location = client.get('/api/game/location').get_json()
    assert location['requires_spatial_validation'] is True
    for point, reason in [((59.9455, 30.3159), 'outside_district'), ((60.1, 30.4), 'too_far')]:
        response = client.post('/api/game/validate_panorama', json={
            'round_id': location['round_id'],
            'latitude': point[0], 'longitude': point[1],
        })
        assert response.get_json() == {'valid': False, 'reason': reason}
