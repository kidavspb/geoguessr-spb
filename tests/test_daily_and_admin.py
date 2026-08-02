"""Тесты ежедневного вызова, самоочистки/модерации пула, периодов лидерборда
и OG-тегов челлендж-ссылок."""
from datetime import timedelta

ADMIN_HEADERS = {'X-Admin-Key': 'test-admin-key'}


def _finish_game(client, rounds=5):
    """Доиграть текущую игру идеальными ответами; вернуть точки раундов."""
    points = []
    for _ in range(rounds):
        loc = client.get('/api/game/location').get_json()
        points.append((loc['latitude'], loc['longitude']))
        client.post('/api/game/guess',
                    json={'latitude': loc['latitude'], 'longitude': loc['longitude']})
    return points


# --------------------------------------------------------------------------
# Ежедневный вызов
# --------------------------------------------------------------------------

def test_daily_same_points_for_everyone(client, app):
    start = client.post('/api/game/start', json={'player_name': 'Первый', 'daily': True})
    assert start.status_code == 200
    data = start.get_json()
    assert data['daily'] is True
    assert data['time_limit'] is None
    first_points = _finish_game(client)

    other = app.test_client()
    other.post('/api/game/start', json={'player_name': 'Второй', 'daily': True})
    second_points = _finish_game(other)

    assert first_points == second_points


def test_daily_single_attempt_per_cookie(client):
    client.post('/api/game/start', json={'player_name': 'Дейлик', 'daily': True})
    _finish_game(client)

    again = client.post('/api/game/start', json={'daily': True})
    assert again.status_code == 409
    body = again.get_json()
    assert body['already_played'] is True
    assert body['total_score'] == 25000


def test_daily_unfinished_game_resumes(client):
    client.post('/api/game/start', json={'player_name': 'Недоигравший', 'daily': True})
    loc = client.get('/api/game/location').get_json()
    client.post('/api/game/guess',
                json={'latitude': loc['latitude'], 'longitude': loc['longitude']})

    resumed = client.post('/api/game/start', json={'daily': True}).get_json()
    assert resumed['resumed'] is True
    assert resumed['total_score'] == 5000
    # Продолжаем со второго раунда
    assert client.get('/api/game/location').get_json()['round'] == 2


def test_daily_info_and_leaderboard(client, app):
    info = client.get('/api/daily').get_json()
    assert info['played'] is False
    assert info['players_today'] == 0

    client.post('/api/game/start', json={'player_name': 'Чемпион дня', 'daily': True})
    _finish_game(client)

    info = client.get('/api/daily').get_json()
    assert info['played'] is True
    assert info['your_score'] == 25000
    assert info['players_today'] == 1

    board = client.get('/api/daily/leaderboard').get_json()
    assert board['leaderboard'][0]['player_name'] == 'Чемпион дня'

    # Обычные игры в топ дня не попадают
    other = app.test_client()
    other.post('/api/game/start', json={'player_name': 'Обычный'})
    for _ in range(5):
        loc = other.get('/api/game/location').get_json()
        other.post('/api/game/guess',
                   json={'latitude': loc['latitude'], 'longitude': loc['longitude']})
    board = client.get('/api/daily/leaderboard').get_json()
    assert all(e['player_name'] != 'Обычный' for e in board['leaderboard'])


# --------------------------------------------------------------------------
# Самоочистка пула
# --------------------------------------------------------------------------

def test_pool_point_removed_after_repeated_failures(client, app_module):
    from models import GameRound, VerifiedPoint, db

    client.post('/api/game/start', json={'difficulty': 'medium'})
    client.get('/api/game/location')

    with app_module.app.app_context():
        # Точка пула, совпадающая с текущей точкой раунда, — как будто
        # раунд стартовал из пула, а панорама там пропала
        rnd = GameRound.query.order_by(GameRound.id.desc()).filter_by(round_number=1).first()
        lat, lon = rnd.gen_latitude, rnd.gen_longitude
        db.session.add(VerifiedPoint(
            latitude=lat, longitude=lon,
            lat_key=int(round(lat * 10000)), lon_key=int(round(lon * 10000)),
            dist_from_center_km=1.0,
        ))
        db.session.commit()

    # Первый скип: счётчик неудач растёт
    client.post('/api/game/skip_location')
    with app_module.app.app_context():
        point = VerifiedPoint.query.first()
        assert point.fail_count == 1

        # Доводим до порога вручную (следующие скипы уже с другой точки)
        from pool import POOL_MAX_FAILS
        point.fail_count = POOL_MAX_FAILS - 1
        db.session.commit()
        lat, lon = point.latitude, point.longitude

    from pool import mark_point_failed
    with app_module.app.app_context():
        mark_point_failed(lat, lon)
        assert VerifiedPoint.query.count() == 0


def test_network_error_does_not_poison_verified_pool(client, app_module):
    from models import GameRound, VerifiedPoint, db

    client.post('/api/game/start', json={'difficulty': 'medium'})
    loc = client.get('/api/game/location').get_json()
    with app_module.app.app_context():
        rnd = db.session.get(GameRound, loc['round_id'])
        db.session.add(VerifiedPoint(
            latitude=rnd.gen_latitude, longitude=rnd.gen_longitude,
            lat_key=int(round(rnd.gen_latitude * 10000)),
            lon_key=int(round(rnd.gen_longitude * 10000)),
            dist_from_center_km=1.0,
        ))
        db.session.commit()

    skipped = client.post('/api/game/skip_location', json={
        'round_id': loc['round_id'], 'reason': 'network_error'
    })
    assert skipped.status_code == 200
    assert skipped.get_json()['source'] == f"{loc['source']}_recovery"
    with app_module.app.app_context():
        assert VerifiedPoint.query.first().fail_count == 0


def test_confirmed_panorama_resets_fail_count(client, app_module):
    from models import VerifiedPoint, db

    client.post('/api/game/start', json={'difficulty': 'medium'})
    loc = client.get('/api/game/location').get_json()

    # Точка уже в пуле и однажды «сбоила»
    with app_module.app.app_context():
        db.session.add(VerifiedPoint(
            latitude=loc['latitude'], longitude=loc['longitude'],
            lat_key=int(round(loc['latitude'] * 10000)),
            lon_key=int(round(loc['longitude'] * 10000)),
            dist_from_center_km=1.0, fail_count=2,
        ))
        db.session.commit()

    # Панорама нашлась и подтвердилась антифродом — неудачи прощены
    client.post('/api/game/set_actual_point',
                json={'latitude': loc['latitude'], 'longitude': loc['longitude']})
    with app_module.app.app_context():
        assert VerifiedPoint.query.first().fail_count == 0


# --------------------------------------------------------------------------
# Модерация пула
# --------------------------------------------------------------------------

def test_admin_requires_key(client):
    assert client.get('/api/admin/points').status_code == 403
    assert client.get('/api/admin/points',
                      headers={'X-Admin-Key': 'wrong'}).status_code == 403
    assert client.get('/api/admin/points', headers=ADMIN_HEADERS).status_code == 200
    assert client.get('/admin').status_code == 200


def test_admin_disabled_without_key(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, 'ADMIN_KEY', '')
    assert client.get('/api/admin/points', headers=ADMIN_HEADERS).status_code == 404
    assert client.get('/admin').status_code == 404


def test_admin_delete_point(client, app_module):
    from models import VerifiedPoint, db

    with app_module.app.app_context():
        db.session.add(VerifiedPoint(
            latitude=59.94, longitude=30.31,
            lat_key=599400, lon_key=303100, dist_from_center_km=0.5,
        ))
        db.session.commit()
        point_id = VerifiedPoint.query.first().id

    points = client.get('/api/admin/points', headers=ADMIN_HEADERS).get_json()['points']
    assert len(points) == 1

    resp = client.delete(f'/api/admin/points/{point_id}', headers=ADMIN_HEADERS)
    assert resp.get_json()['success'] is True
    with app_module.app.app_context():
        assert VerifiedPoint.query.count() == 0

    assert client.delete(f'/api/admin/points/{point_id}',
                         headers=ADMIN_HEADERS).status_code == 404


# --------------------------------------------------------------------------
# Периоды лидерборда
# --------------------------------------------------------------------------

def test_leaderboard_periods(client, app_module):
    from models import GameSession, db

    client.post('/api/game/start', json={'player_name': 'Ветеран', 'difficulty': 'center'})
    _finish_game(client)

    # Состариваем игру: месяц назад
    with app_module.app.app_context():
        game = GameSession.query.filter_by(player_name='Ветеран').first()
        game.completed_at = game.completed_at - timedelta(days=10)
        db.session.commit()

    week = client.get('/api/leaderboard?period=week').get_json()
    assert week['period'] == 'week'
    assert all(e['player_name'] != 'Ветеран' for e in week['leaderboard'])

    month = client.get('/api/leaderboard?period=month').get_json()
    assert any(e['player_name'] == 'Ветеран' for e in month['leaderboard'])

    bogus = client.get('/api/leaderboard?period=bogus').get_json()
    assert bogus['period'] == 'all'


# --------------------------------------------------------------------------
# OG-теги челлендж-ссылок
# --------------------------------------------------------------------------

def test_challenge_link_renders_og_tags(client):
    client.post('/api/game/start', json={'player_name': 'Хвастун', 'difficulty': 'center'})
    _finish_game(client)
    token = client.get('/api/game/results').get_json()['challenge_token']

    html = client.get(f'/?challenge={token}').get_data(as_text=True)
    assert 'Хвастун набрал 25000 очков' in html

    # Невалидный токен — обычные OG-теги, без 500-х
    html = client.get('/?challenge=nope').get_data(as_text=True)
    assert 'og:title' in html
