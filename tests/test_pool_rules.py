"""Правила пула: пополнение только из сыгранных раундов, неудачи — по играм.

Раньше каждый вызов /validate_panorama добавлял точку (200 вызовов за раунд —
200 точек), а неудачи одной игры могли накопиться до удаления точки.
"""
import pool
from helpers import round_body
from models import GameRound, VerifiedPoint, db

PALACE_SQUARE = (59.9386, 30.3141)


def _start(client, **settings):
    return client.post('/api/game/start', json={
        'difficulty': 'center', **settings,
    }).get_json()['location']


def _panorama_near(location):
    return location['latitude'] + 0.001, location['longitude']


def _answer(client, location, pano, **fields):
    return client.post('/api/game/guess', json=round_body(
        location, latitude=pano[0], longitude=pano[1],
        panorama_latitude=pano[0], panorama_longitude=pano[1], **fields,
    ))


def test_validate_panorama_alone_does_not_grow_the_pool(client, app):
    location = _start(client)
    # Соседние кадры в 20+ м друг от друга — разные ключи пула.
    for step in range(1, 6):
        response = client.post('/api/game/validate_panorama', json=round_body(
            location, latitude=location['latitude'] + 0.0002 * step,
            longitude=location['longitude'],
        ))
        assert response.get_json() == {'valid': True}
    with app.app_context():
        assert VerifiedPoint.query.count() == 0


def test_played_round_adds_its_panorama_once(client, app):
    location = _start(client)
    pano = _panorama_near(location)
    assert client.post('/api/game/ready', json=round_body(location)).status_code == 200

    assert _answer(client, location, pano).status_code == 200
    # Повтор потерянного ответа ничего не добавляет.
    assert _answer(client, location, pano).get_json()['replayed'] is True

    with app.app_context():
        points = VerifiedPoint.query.all()
        assert [(p.latitude, p.longitude) for p in points] == [pano]


def test_answer_without_shown_panorama_does_not_add_to_the_pool(client, app):
    location = _start(client)
    assert _answer(client, location, _panorama_near(location)).status_code == 200
    with app.app_context():
        assert VerifiedPoint.query.count() == 0


def test_pool_point_is_removed_only_after_failures_in_three_games(app):
    with app.app_context():
        pool.add_verified_point(*PALACE_SQUARE)
        for game_id, expected in ((1, 1), (1, 1), (2, 2), (2, 2)):
            pool.mark_point_failed(*PALACE_SQUARE, game_id)
            db.session.commit()
            assert VerifiedPoint.query.one().fail_count == expected

        pool.mark_point_failed(*PALACE_SQUARE, 3)
        db.session.commit()
        assert VerifiedPoint.query.count() == 0


def test_skips_in_one_game_count_as_one_failure_per_point(client, app):
    location = _start(client)
    with app.app_context():
        pool.add_verified_point(location['latitude'], location['longitude'])
        game_id = db.session.get(GameRound, location['round_id']).session_id
        # Та же игра уже записала неудачу этой точки (например, в прошлом раунде).
        pool.mark_point_failed(location['latitude'], location['longitude'], game_id)
        db.session.commit()

    response = client.post('/api/game/skip_location', json=round_body(
        location, reason='no_coverage',
    ))

    assert response.status_code == 200
    with app.app_context():
        assert VerifiedPoint.query.one().fail_count == 1


def test_successful_round_forgives_earlier_failures(client, app):
    with app.app_context():
        pool.add_verified_point(*PALACE_SQUARE)
        pool.mark_point_failed(*PALACE_SQUARE, 1001)
        pool.mark_point_failed(*PALACE_SQUARE, 1002)
        db.session.commit()
    location = _start(client)
    # Пул из одной точки не включается в обычной игре: ставим её в раунд сами.
    with app.app_context():
        rnd = db.session.get(GameRound, location['round_id'])
        rnd.gen_latitude, rnd.gen_longitude = PALACE_SQUARE
        rnd.actual_latitude, rnd.actual_longitude = PALACE_SQUARE
        db.session.commit()
    assert client.post('/api/game/ready', json=round_body(location)).status_code == 200

    assert _answer(client, location, PALACE_SQUARE).status_code == 200

    with app.app_context():
        point = VerifiedPoint.query.one()
        assert point.fail_count == 0
        assert point.last_failed_game_id is None
