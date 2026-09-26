"""Overlapping requests use separate sessions, as in multiple Gunicorn workers."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread

import pytest
from sqlalchemy import event

from models import GameRound, GameSession, db


@pytest.fixture
def overlapping_posts(app, client):
    """Hold the first request after reading its round; start another meanwhile.

    A second round SELECT reveals an unsafe stale read. A correct transaction
    blocks the second worker until the first commits, so we release after a
    bounded wait as well. No barrier is put *inside* the transaction lock.
    """
    def run(first, second):
        first_read = Event()
        second_read = Event()
        release = Event()
        cookie = client.get_cookie('session').value

        def after_execute(conn, cursor, statement, parameters, context, executemany):
            if not statement.lstrip().upper().startswith('SELECT') or 'game_rounds' not in statement:
                return
            if current_thread().name.endswith('_0') and not first_read.is_set():
                first_read.set()
                assert release.wait(5)
            elif current_thread().name.endswith('_1'):
                second_read.set()

        def post(request):
            worker = app.test_client()
            worker.set_cookie('session', cookie)
            return worker.post(request[0], json=request[1])

        with app.app_context():
            engine = db.engine
        event.listen(engine, 'after_cursor_execute', after_execute)
        try:
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix='round-test') as executor:
                pending_first = executor.submit(post, first)
                try:
                    assert first_read.wait(5)
                    pending_second = executor.submit(post, second)
                    second_read.wait(0.3)
                finally:
                    release.set()
                return pending_first.result(timeout=10), pending_second.result(timeout=10)
        finally:
            release.set()
            event.remove(engine, 'after_cursor_execute', after_execute)
    return run


def test_concurrent_guesses_preserve_first_answer(client, app, overlapping_posts):
    location = client.post('/api/game/start', json={}).get_json()['location']
    first = {'round_id': location['round_id'], 'latitude': location['latitude'],
             'longitude': location['longitude']}
    second = {**first, 'latitude': first['latitude'] + 0.05}
    accepted, replayed = overlapping_posts(('/api/game/guess', first),
                                           ('/api/game/guess', second))
    assert accepted.status_code == replayed.status_code == 200
    assert replayed.get_json()['replayed'] is True
    assert accepted.get_json()['score'] == replayed.get_json()['score'] == 5000
    with app.app_context():
        game = GameSession.query.one()
        assert game.total_score == 5000
        assert game.current_round == game.rounds_played == 1
        assert db.session.get(GameRound, location['round_id']).guess_latitude == first['latitude']


def test_concurrent_skips_return_same_candidate(client, overlapping_posts):
    location = client.post('/api/game/start', json={}).get_json()['location']
    body = {'round_id': location['round_id'], 'location_version': 0}
    first, replay = overlapping_posts(('/api/game/skip_location', body),
                                      ('/api/game/skip_location', body))
    assert first.status_code == replay.status_code == 200
    assert replay.get_json()['replayed'] is True
    for key in ('latitude', 'longitude', 'location_version'):
        assert first.get_json()[key] == replay.get_json()[key]


def test_skip_cannot_replace_an_answered_round(client, app, overlapping_posts):
    location = client.post('/api/game/start', json={}).get_json()['location']
    body = {'round_id': location['round_id'], 'location_version': 0,
            'latitude': location['latitude'], 'longitude': location['longitude']}
    guess, skip = overlapping_posts(('/api/game/guess', body),
                                    ('/api/game/skip_location', body))
    assert guess.status_code == 200
    assert skip.status_code == 409
    with app.app_context():
        rnd = db.session.get(GameRound, location['round_id'])
        assert rnd.skips == 0
        assert rnd.gen_latitude == location['latitude']


@pytest.mark.parametrize('first_endpoint', ['continue_search', 'ready'])
def test_concurrent_search_continuations_are_serialized(client, app, overlapping_posts,
                                                       first_endpoint):
    location = client.post('/api/game/start', json={}).get_json()['location']
    for _ in range(10):
        location = client.post('/api/game/skip_location', json=location).get_json()
    first, second = overlapping_posts((f'/api/game/{first_endpoint}', location),
                                      ('/api/game/continue_search', location))
    assert first.status_code == 200
    if first_endpoint == 'ready':
        assert second.status_code == 409
    else:
        assert second.status_code == 200
        assert second.get_json()['replayed']
        assert second.get_json()['max_location_skips'] == 20
    with app.app_context():
        rnd = db.session.get(GameRound, location['round_id'])
        assert rnd.skips == 10
        assert rnd.search_batch == (1 if first_endpoint == 'ready' else 2)
