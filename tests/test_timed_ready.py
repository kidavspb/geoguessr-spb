"""В игре с таймером ответ принимается только после /ready.

Без отметки начала сервер не мог проверить лимит времени вовсе. Это не
барьер против скрипта (он пришлёт /ready сам), но у каждого раунда с
таймером теперь есть started_at, и время ответа можно разобрать позже.
"""
from helpers import round_body
from models import GameRound, db


def _start(client, **settings):
    return client.post('/api/game/start', json={
        'difficulty': 'center', **settings,
    }).get_json()['location']


def _guess(client, location, **fields):
    return client.post('/api/game/guess', json=round_body(
        location, latitude=location['latitude'],
        longitude=location['longitude'], **fields,
    ))


def test_timed_guess_before_ready_is_refused_without_ending_the_round(client, app):
    location = _start(client, time_limit=60)

    early = _guess(client, location)

    assert early.status_code == 409
    assert early.get_json()['reason'] == 'round_not_started'
    with app.app_context():
        assert db.session.get(GameRound, location['round_id']).answered_at is None

    assert client.post('/api/game/ready', json=round_body(location)).status_code == 200
    answered = _guess(client, location)
    assert answered.status_code == 200
    assert answered.get_json()['score'] == 5000


def test_timed_out_report_also_needs_ready(client):
    location = _start(client, time_limit=30)
    response = client.post('/api/game/guess', json=round_body(location, timed_out=True))
    assert response.status_code == 409
    assert response.get_json()['reason'] == 'round_not_started'


def test_untimed_game_accepts_an_answer_without_ready(client):
    location = _start(client)
    response = _guess(client, location)
    assert response.status_code == 200
    assert response.get_json()['score'] == 5000
