"""Тесты новой архитектуры и фич: состояние игры в БД, пул проверенных точек,
челлендж-ссылки, таймер раунда, серверные лимиты, статистика игрока."""
from datetime import timedelta


def _play_full_game(client, name='Игрок', difficulty='center', extra=None):
    """Пройти игру целиком с идеальными ответами; вернуть точки раундов."""
    body = {'player_name': name, 'difficulty': difficulty}
    if extra:
        body.update(extra)
    start = client.post('/api/game/start', json=body)
    assert start.status_code == 200
    points = []
    for _ in range(5):
        loc = client.get('/api/game/location').get_json()
        points.append((loc['latitude'], loc['longitude']))
        client.post('/api/game/guess',
                    json={'latitude': loc['latitude'], 'longitude': loc['longitude']})
    return points


# --------------------------------------------------------------------------
# Состояние игры на сервере
# --------------------------------------------------------------------------

def test_no_coordinates_in_cookie_session(client):
    """Cookie подписана, но не зашифрована — координат в ней быть не должно."""
    client.post('/api/game/start', json={'difficulty': 'medium'})
    with client.session_transaction() as sess:
        assert 'game_id' in sess
        assert 'random_points' not in sess
        assert 'actual_points' not in sess
        assert 'current_round' not in sess


def test_replayed_cookie_cannot_replay_round(client):
    """Реплей старой cookie не откатывает игру: прогресс хранится в БД."""
    client.post('/api/game/start', json={'difficulty': 'medium'})
    old_cookie = client.get_cookie('session')
    assert old_cookie is not None

    loc1 = client.get('/api/game/location').get_json()
    assert loc1['round'] == 1
    client.post('/api/game/guess',
                json={'latitude': loc1['latitude'], 'longitude': loc1['longitude']})

    # «Подсовываем» cookie, сохранённую до первого ответа
    client.set_cookie('session', old_cookie.value)
    loc2 = client.get('/api/game/location').get_json()
    assert loc2['round'] == 2  # раунд 1 переиграть нельзя


def test_guess_after_game_over_rejected(client):
    _play_full_game(client)
    resp = client.post('/api/game/guess', json={'latitude': 59.9, 'longitude': 30.3})
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# Серверный лимит перегенераций точки
# --------------------------------------------------------------------------

def test_skip_limit_enforced_server_side(client, app_module):
    client.post('/api/game/start', json={'difficulty': 'medium'})
    client.get('/api/game/location')
    for _ in range(app_module.MAX_SKIPS_PER_ROUND):
        assert client.post('/api/game/skip_location').status_code == 200
    over = client.post('/api/game/skip_location')
    assert over.status_code == 429


# --------------------------------------------------------------------------
# Пул проверенных точек
# --------------------------------------------------------------------------

def test_actual_point_feeds_verified_pool(client, app_module):
    client.post('/api/game/start', json={'difficulty': 'medium'})
    loc = client.get('/api/game/location').get_json()

    resp = client.post('/api/game/set_actual_point',
                       json={'latitude': loc['latitude'], 'longitude': loc['longitude']})
    assert resp.get_json()['success'] is True

    from models import VerifiedPoint
    with app_module.app.app_context():
        assert VerifiedPoint.query.count() == 1

    # Та же точка второй раз не дублируется (дедупликация ~10 м)
    client.post('/api/game/set_actual_point',
                json={'latitude': loc['latitude'], 'longitude': loc['longitude']})
    with app_module.app.app_context():
        assert VerifiedPoint.query.count() == 1


def test_rejected_actual_point_not_in_pool(client, app_module):
    client.post('/api/game/start', json={'difficulty': 'medium'})
    loc = client.get('/api/game/location').get_json()
    resp = client.post('/api/game/set_actual_point',
                       json={'latitude': loc['latitude'], 'longitude': loc['longitude'] + 0.5})
    assert resp.get_json()['success'] is False

    from models import VerifiedPoint
    with app_module.app.app_context():
        assert VerifiedPoint.query.count() == 0


def test_choose_round_points_uses_pool(app, app_module, monkeypatch):
    from models import VerifiedPoint, db

    seeded = set()
    with app.app_context():
        for i in range(app_module.POOL_MIN_SIZE + 5):
            lat = 59.9300 + i * 0.0005
            lon = 30.3100 + i * 0.0005
            db.session.add(VerifiedPoint(
                latitude=lat, longitude=lon,
                lat_key=int(round(lat * 10000)), lon_key=int(round(lon * 10000)),
                dist_from_center_km=app_module.haversine_distance(lat, lon, *app_module.SPB_CENTER),
            ))
            seeded.add((lat, lon))
        db.session.commit()

        # random() < POOL_USE_PROBABILITY всегда → каждая точка берётся из пула
        monkeypatch.setattr(app_module.random, 'random', lambda: 0.0)
        points = app_module.choose_round_points('center', 5)

    assert len(points) == 5
    for p in points:
        assert p in seeded


def test_choose_round_points_generates_when_pool_small(app, app_module):
    with app.app_context():
        points = app_module.choose_round_points('center', 5)
    assert len(points) == 5
    for lat, lon in points:
        assert app_module.SPB_BOUNDS['lat_min'] <= lat <= app_module.SPB_BOUNDS['lat_max']


# --------------------------------------------------------------------------
# Таймер раунда
# --------------------------------------------------------------------------

def test_time_limit_saved_and_validated(client):
    ok = client.post('/api/game/start', json={'time_limit': 60}).get_json()
    assert ok['time_limit'] == 60

    bogus = client.post('/api/game/start', json={'time_limit': 'abc'}).get_json()
    assert bogus['time_limit'] is None

    too_big = client.post('/api/game/start', json={'time_limit': 100000}).get_json()
    assert too_big['time_limit'] is None


def test_late_guess_scores_zero(client, app_module):
    client.post('/api/game/start', json={'difficulty': 'center', 'time_limit': 60})
    loc = client.get('/api/game/location').get_json()

    # Отматываем старт раунда далеко в прошлое — лимит и запас точно истекли
    from models import GameRound, db
    with app_module.app.app_context():
        rnd = GameRound.query.filter_by(round_number=1).order_by(GameRound.id.desc()).first()
        rnd.started_at = rnd.started_at - timedelta(seconds=300)
        db.session.commit()

    result = client.post('/api/game/guess', json={
        'latitude': loc['latitude'], 'longitude': loc['longitude']
    }).get_json()
    assert result['score'] == 0
    assert result['timed_out'] is True


def test_timeout_round_without_guess(client):
    client.post('/api/game/start', json={'difficulty': 'center', 'time_limit': 60})
    client.get('/api/game/location')

    result = client.post('/api/game/guess', json={'timed_out': True})
    assert result.status_code == 200
    data = result.get_json()
    assert data['score'] == 0
    assert data['guess'] is None
    assert data['distance_m'] is None
    assert data['timed_out'] is True

    # Игра продолжается со следующего раунда
    loc = client.get('/api/game/location').get_json()
    assert loc['round'] == 2


def test_guess_without_coords_and_without_timeout_rejected(client):
    client.post('/api/game/start', json={'difficulty': 'center'})
    client.get('/api/game/location')
    assert client.post('/api/game/guess', json={}).status_code == 400


# --------------------------------------------------------------------------
# Челлендж-ссылки
# --------------------------------------------------------------------------

def test_challenge_full_flow(client, app):
    author_points = _play_full_game(client, name='Автор', difficulty='center',
                                    extra={'time_limit': 180})
    author_results = client.get('/api/game/results').get_json()
    token = author_results['challenge_token']
    assert token

    # Второй игрок с чистыми cookie
    friend = app.test_client()
    info = friend.get(f'/api/challenge/{token}').get_json()
    assert info['player_name'] == 'Автор'
    assert info['difficulty'] == 'center'
    assert info['time_limit'] == 180

    start = friend.post('/api/game/start', json={
        'player_name': 'Друг', 'challenge_token': token,
        'difficulty': 'hard',  # игнорируется: параметры фиксированы челленджем
    }).get_json()
    assert start['difficulty'] == 'center'
    assert start['time_limit'] == 180
    assert start['challenge']['opponent_name'] == 'Автор'

    friend_points = []
    for _ in range(5):
        loc = friend.get('/api/game/location').get_json()
        friend_points.append((loc['latitude'], loc['longitude']))
        friend.post('/api/game/guess',
                    json={'latitude': loc['latitude'], 'longitude': loc['longitude']})

    # Точки те же, что и у автора
    assert friend_points == author_points

    results = friend.get('/api/game/results').get_json()
    assert results['challenge']['opponent_name'] == 'Автор'
    assert results['challenge']['opponent_score'] == author_results['total_score']
    assert results['challenge']['your_score'] == results['total_score']


def test_challenge_unknown_token(client):
    assert client.get('/api/challenge/nope').status_code == 404
    resp = client.post('/api/game/start', json={'challenge_token': 'nope'})
    assert resp.status_code == 404


def test_challenge_requires_completed_game(client, app):
    client.post('/api/game/start', json={'player_name': 'Недоигравший'})
    with client.session_transaction() as sess:
        game_id = sess['game_id']

    from models import GameSession, db
    with app.app_context():
        token = db.session.get(GameSession, game_id).challenge_token

    # Игра не завершена — челлендж по её токену недоступен
    assert client.get(f'/api/challenge/{token}').status_code == 404


def test_results_hide_token_until_game_over(client):
    client.post('/api/game/start', json={'difficulty': 'center'})
    loc = client.get('/api/game/location').get_json()
    client.post('/api/game/guess',
                json={'latitude': loc['latitude'], 'longitude': loc['longitude']})
    results = client.get('/api/game/results').get_json()
    assert results['challenge_token'] is None


# --------------------------------------------------------------------------
# Результаты: координаты для итоговой карты
# --------------------------------------------------------------------------

def test_results_include_round_coordinates(client):
    points = _play_full_game(client)
    results = client.get('/api/game/results').get_json()
    assert len(results['rounds']) == 5
    for r, (lat, lon) in zip(results['rounds'], points):
        assert r['guess'] == {'latitude': lat, 'longitude': lon}
        assert r['actual'] == {'latitude': lat, 'longitude': lon}
        assert r['timed_out'] is False


# --------------------------------------------------------------------------
# Статистика игрока
# --------------------------------------------------------------------------

def test_player_stats(client, app):
    _play_full_game(client, name='Статистик')  # 25000
    second = app.test_client()
    start = second.post('/api/game/start', json={'player_name': 'Статистик'})
    assert start.status_code == 200
    for _ in range(5):
        loc = second.get('/api/game/location').get_json()
        # Промахиваемся на ~3 км к северу — очки меньше максимума
        second.post('/api/game/guess',
                    json={'latitude': loc['latitude'] + 0.027, 'longitude': loc['longitude']})

    stats = client.get('/api/player/stats?name=Статистик').get_json()
    assert stats['games'] == 2
    assert stats['best_score'] == 25000
    assert stats['avg_score'] < 25000

    empty = client.get('/api/player/stats?name=Никто').get_json()
    assert empty['games'] == 0
    assert empty['best_score'] is None

    assert client.get('/api/player/stats').status_code == 400


# --------------------------------------------------------------------------
# Геокодер (в тестах выключен — ключа нет)
# --------------------------------------------------------------------------

def test_reverse_geocode_disabled_without_key(app_module):
    assert app_module.reverse_geocode(59.939, 30.315) is None


# --------------------------------------------------------------------------
# Лидерборд: лимит времени в выдаче
# --------------------------------------------------------------------------

def test_leaderboard_exposes_time_limit(client):
    _play_full_game(client, name='Скоростной', extra={'time_limit': 60})
    board = client.get('/api/leaderboard').get_json()
    entry = next(e for e in board['leaderboard'] if e['player_name'] == 'Скоростной')
    assert entry['time_limit'] == 60
