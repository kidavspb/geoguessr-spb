"""Тесты статистики сложности мест, режимов «Хардкор» и «без перемещения»,
предзагрузки (?peek=1) и админ-сводки."""

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

def test_difficulty_percentile_and_hardest_places(app, app_module):
    # 5 точек: промахи от 1 до 5 км (по 3 раунда на точку)
    coords = [(59.90 + i * 0.01, 30.30 + i * 0.01) for i in range(5)]
    for i, (lat, lon) in enumerate(coords):
        _seed_point_with_misses(app_module, lat, lon, [i + 1.0] * 3)

    from stats import difficulty_percentile, hardest_places
    with app_module.app.app_context():
        # Самая сложная точка (промах 5 км) сложнее всех остальных
        assert difficulty_percentile(*coords[4]) == 100
        # Самая простая — проще всех
        assert difficulty_percentile(*coords[0]) == 0
        # Точка без статистики — None
        assert difficulty_percentile(59.99, 30.49) is None

        places = hardest_places()
        assert places[0]['avg_miss_km'] == 5.0
        assert places[0]['games'] == 3
        assert [p['avg_miss_km'] for p in places] == sorted(
            (p['avg_miss_km'] for p in places), reverse=True)


def test_hardest_places_endpoint_and_page(client):
    resp = client.get('/api/places/hardest')
    assert resp.status_code == 200
    assert resp.get_json()['places'] == []  # данных нет — пустой список, не 500

    html = client.get('/places').get_data(as_text=True)
    assert 'Самые неузнаваемые места' in html


def test_guess_returns_difficulty_percentile(client, app_module):
    client.post('/api/game/start', json={'difficulty': 'center'})
    loc = client.get('/api/game/location').get_json()

    # История: в этой точке уже промахивались, плюс три «лёгкие» точки для фона
    _seed_point_with_misses(app_module, loc['latitude'], loc['longitude'], [8.0] * 3)
    for i in range(3):
        _seed_point_with_misses(app_module, 59.90 + i * 0.01, 30.20, [0.5] * 3)

    client.post('/api/game/set_actual_point',
                json={'latitude': loc['latitude'], 'longitude': loc['longitude']})
    result = client.post('/api/game/guess', json={
        'latitude': loc['latitude'], 'longitude': loc['longitude']
    }).get_json()
    assert result['difficulty_percentile'] == 100


def test_hardcore_uses_hardest_points(app, app_module):
    # 12 изученных точек с разными промахами — хардкор должен брать сложные
    seeded = {}
    for i in range(12):
        lat, lon = 59.90 + i * 0.005, 30.30 + i * 0.005
        _seed_point_with_misses(app_module, lat, lon, [float(i)] * 3)
        seeded[(round(lat, 6), round(lon, 6))] = i

    with app_module.app.app_context():
        points = app_module.choose_round_points('hardcore', 4)

    assert len(points) == 4
    for lat, lon in points:
        rank = seeded[(round(lat, 6), round(lon, 6))]
        assert rank >= 4  # точки из «лёгкой» части не попадают


def test_hardcore_falls_back_without_data(app, app_module):
    with app_module.app.app_context():
        points = app_module.choose_round_points('hardcore', 5)
    assert len(points) == 5  # обычная генерация, без падений


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
        client.post('/api/game/guess',
                    json={'latitude': loc['latitude'], 'longitude': loc['longitude']})

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

def test_peek_does_not_start_round_timer(client, app_module):
    from models import GameRound

    client.post('/api/game/start', json={'difficulty': 'center', 'time_limit': 60})

    peek = client.get('/api/game/location?peek=1')
    assert peek.status_code == 200
    with app_module.app.app_context():
        rnd = GameRound.query.filter_by(round_number=1).order_by(GameRound.id.desc()).first()
        assert rnd.started_at is None  # таймер ещё не пошёл

    normal = client.get('/api/game/location').get_json()
    assert normal['latitude'] == peek.get_json()['latitude']
    with app_module.app.app_context():
        rnd = GameRound.query.filter_by(round_number=1).order_by(GameRound.id.desc()).first()
        assert rnd.started_at is not None  # обычный запрос запустил отсчёт


# --------------------------------------------------------------------------
# Админ-сводка
# --------------------------------------------------------------------------

def test_admin_stats(client):
    assert client.get('/api/admin/stats').status_code == 403

    client.post('/api/game/start', json={'player_name': 'Игрок'})
    for _ in range(5):
        loc = client.get('/api/game/location').get_json()
        client.post('/api/game/guess',
                    json={'latitude': loc['latitude'], 'longitude': loc['longitude']})

    stats = client.get('/api/admin/stats', headers=ADMIN_HEADERS).get_json()
    assert stats['games_total'] == 1
    assert stats['games_completed_7d'] == 1
    assert stats['completion_rate_7d'] == 1.0
