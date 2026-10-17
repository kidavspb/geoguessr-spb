"""Тесты статистики сложности мест, режима «без перемещения»,
отложенного старта таймера и админ-сводки."""
from helpers import guess_at_target, round_body

ADMIN_HEADERS = {'X-Admin-Key': 'test-admin-key'}


def _seed_point_with_misses(app_module, lat, lon, misses):
    """Точка пула + сыгранные раунды с заданными промахами (км)."""
    from models import db, VerifiedPoint, GameSession, GameRound, utcnow

    with app_module.app.app_context():
        db.session.add(VerifiedPoint(
            latitude=lat, longitude=lon,
            lat_key=int(round(lat * 10000)), lon_key=int(round(lon * 10000)),
            dist_from_center_km=1.0,
        ))
        session = GameSession(player_name='Сеятель', current_round=5)
        db.session.add(session)
        db.session.flush()
        for i, miss in enumerate(misses, start=1):
            db.session.add(GameRound(
                session_id=session.id, round_number=i,
                gen_latitude=lat, gen_longitude=lon,
                actual_latitude=lat, actual_longitude=lon,
                guess_latitude=lat + 0.01, guess_longitude=lon,
                distance_km=miss, score=1000,
                answered_at=utcnow(),
            ))
        db.session.commit()


# --------------------------------------------------------------------------
# Сложность мест из данных
# --------------------------------------------------------------------------

def test_difficulty_percentile(app, app_module):
    # 5 точек: промахи от 1 до 5 км (по 3 раунда на точку)
    coords = [(59.90 + i * 0.01, 30.30 + i * 0.01) for i in range(5)]
    for i, (lat, lon) in enumerate(coords):
        _seed_point_with_misses(app_module, lat, lon, [i + 1.0] * 3)

    from stats import difficulty_percentile
    with app_module.app.app_context():
        # Самая сложная точка (промах 5 км) сложнее всех остальных
        assert difficulty_percentile(*coords[4]) == 100
        # Самая простая — проще всех
        assert difficulty_percentile(*coords[0]) == 0
        # Точка без статистики — None
        assert difficulty_percentile(59.99, 30.49) is None



def _seed_easy_place_with_harder_background(app_module, lat, lon):
    _seed_point_with_misses(app_module, lat, lon, [1.0] * 3)
    for i in range(3):
        _seed_point_with_misses(
            app_module, lat + (i + 1) * 0.02, lon + 0.02, [2.0] * 3,
        )


def test_replayed_guess_returns_the_same_percentile(client, app_module):
    location = client.post('/api/game/start', json={'difficulty': 'center'}).get_json()['location']
    _seed_easy_place_with_harder_background(
        app_module, location['latitude'], location['longitude'],
    )
    # Промах ~20 км делает место самым сложным, но только если раунд игрока
    # учтён: до исправления первый ответ его не учитывал, а повтор — учитывал.
    body = {
        'round_id': location['round_id'],
        'location_version': 0,
        'latitude': location['latitude'] + 0.18,
        'longitude': location['longitude'],
        # Клиент всегда присылает позицию открытой панорамы вместе с ответом.
        'panorama_latitude': location['latitude'],
        'panorama_longitude': location['longitude'],
    }
    first = client.post('/api/game/guess', json=body).get_json()
    replay = client.post('/api/game/guess', json=body).get_json()
    assert replay['replayed'] is True
    assert first['difficulty_percentile'] == replay['difficulty_percentile'] == 100


def test_percentile_is_computed_without_holding_the_write_lock(client, app_module, monkeypatch):
    """Полный GROUP BY по раундам не должен держать блокировку записи SQLite."""
    from models import db

    holds_write_lock = []

    def spy(lat, lon):
        raw = db.session.connection().connection.dbapi_connection
        holds_write_lock.append(raw.in_transaction)
        return None

    monkeypatch.setattr(app_module, 'difficulty_percentile', spy)
    location = client.post('/api/game/start', json={'difficulty': 'center'}).get_json()['location']
    body = {
        'round_id': location['round_id'],
        'location_version': 0,
        'latitude': location['latitude'],
        'longitude': location['longitude'],
    }
    client.post('/api/game/guess', json=body)
    client.post('/api/game/guess', json=body)  # повтор после потерянного ответа
    assert holds_write_lock == [False, False]


def test_guess_returns_difficulty_percentile(client, app_module):
    client.post('/api/game/start', json={'difficulty': 'center'})
    loc = client.get('/api/game/location').get_json()

    # История: в этой точке уже промахивались, плюс три «лёгкие» точки для фона
    _seed_point_with_misses(app_module, loc['latitude'], loc['longitude'], [8.0] * 3)
    for i in range(3):
        # Фон строим относительно случайной точки раунда: он гарантированно
        # не сольётся с ней в один округлённый ключ статистики.
        _seed_point_with_misses(
            app_module,
            loc['latitude'] + (i + 1) * 0.02,
            loc['longitude'] + 0.02,
            [0.5] * 3,
        )

    result = guess_at_target(client, loc, panorama_latitude=loc['latitude'],
                             panorama_longitude=loc['longitude'])
    assert result['difficulty_percentile'] == 100




# --------------------------------------------------------------------------
# Режим «без перемещения»
# --------------------------------------------------------------------------

def test_no_move_flag_flows_to_challenge_and_leaderboard(client, app):
    start = client.post('/api/game/start', json={
        'player_name': 'Стоик', 'difficulty': 'center', 'no_move': True
    }).get_json()
    assert start['no_move'] is True

    for _ in range(5):
        loc = client.get('/api/game/location').get_json()
        assert loc['no_move'] is True
        guess_at_target(client, loc)

    token = client.get('/api/game/results').get_json()['challenge_token']

    # Челлендж наследует режим
    info = client.get(f'/api/challenge/{token}').get_json()
    assert info['no_move'] is True
    friend = app.test_client()
    fstart = friend.post('/api/game/start', json={'challenge_token': token}).get_json()
    assert fstart['no_move'] is True

    # Лидерборд отдаёт флаг для бейджа
    entry = next(e for e in client.get('/api/leaderboard').get_json()['leaderboard']
                 if e['player_name'] == 'Стоик')
    assert entry['no_move'] is True


# --------------------------------------------------------------------------
# Предзагрузка следующего раунда
# --------------------------------------------------------------------------

def test_location_does_not_start_timer_until_panorama_ready(client, app_module):
    from models import GameRound

    started = client.post('/api/game/start', json={'difficulty': 'center', 'time_limit': 60})
    with app_module.app.app_context():
        rnd = GameRound.query.filter_by(round_number=1).order_by(GameRound.id.desc()).first()
        assert rnd.started_at is None  # таймер ещё не пошёл

    normal = client.get('/api/game/location').get_json()
    assert normal['latitude'] == started.get_json()['location']['latitude']
    with app_module.app.app_context():
        rnd = GameRound.query.filter_by(round_number=1).order_by(GameRound.id.desc()).first()
        assert rnd.started_at is None

    ready = client.post('/api/game/ready', json=round_body(normal))
    assert ready.status_code == 200
    assert ready.get_json()['deadline_ms'] is not None
    with app_module.app.app_context():
        rnd = GameRound.query.filter_by(round_number=1).order_by(GameRound.id.desc()).first()
        assert rnd.started_at is not None


# --------------------------------------------------------------------------
# Админ-сводка
# --------------------------------------------------------------------------

def test_admin_stats(client):
    assert client.get('/api/admin/stats').status_code == 403

    client.post('/api/game/start', json={'player_name': 'Игрок'})
    for _ in range(5):
        guess_at_target(client, client.get('/api/game/location').get_json())

    stats = client.get('/api/admin/stats', headers=ADMIN_HEADERS).get_json()
    assert stats['games_total'] == 1
    assert stats['games_completed_7d'] == 1
    assert stats['completion_rate_7d'] == 1.0


def test_completion_rate_uses_the_same_started_cohort(client, app):
    from datetime import timedelta
    from models import GameSession, db, utcnow

    with app.app_context():
        now = utcnow()
        db.session.add_all([
            GameSession(created_at=now - timedelta(days=10), completed_at=now),
            GameSession(created_at=now - timedelta(days=1), completed_at=now),
            GameSession(created_at=now),
        ])
        db.session.commit()
    stats = client.get('/api/admin/stats', headers=ADMIN_HEADERS).get_json()
    assert stats['games_started_7d'] == stats['games_completed_7d'] == 2
    assert stats['completion_rate_7d'] == 0.5
