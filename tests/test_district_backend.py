"""Backend/API integration административных районов."""


def _start_district(client, district_id='petrogradsky', **extra):
    payload = {'difficulty': 'district', 'district_id': district_id}
    payload.update(extra)
    return client.post('/api/game/start', json=payload)


def _validate_location(client, location):
    return client.post('/api/game/validate_panorama', json={
        'round_id': location['round_id'],
        'location_version': location['location_version'],
        'latitude': location['latitude'],
        'longitude': location['longitude'],
    })


def _finish_district_game(client):
    locations = []
    for _ in range(5):
        location = client.get('/api/game/location').get_json()
        locations.append(location)
        assert _validate_location(client, location).get_json() == {'valid': True}
        result = client.post('/api/game/guess', json={
            'round_id': location['round_id'],
            'latitude': location['latitude'],
            'longitude': location['longitude'],
            'panorama_latitude': location['latitude'],
            'panorama_longitude': location['longitude'],
        })
        assert result.status_code == 200
    return locations


def test_district_geometry_route_is_local_and_conditionally_cached(client):
    response = client.get('/api/districts/geometry')

    assert response.status_code == 200
    assert response.mimetype == 'application/geo+json'
    assert len(response.get_json()['features']) == 18
    assert response.headers.get('ETag')
    assert 'no-cache' in response.headers.get('Cache-Control', '')

    cached = client.get('/api/districts/geometry', headers={
        'If-None-Match': response.headers['ETag'],
    })
    assert cached.status_code == 304


def test_start_accepts_valid_district_and_generates_all_rounds_inside(client, app_module):
    from districts import point_in_district
    from models import GameRound, GameSession

    response = _start_district(client)
    assert response.status_code == 200
    body = response.get_json()
    assert body['difficulty'] == 'district'
    assert body['district_id'] == 'petrogradsky'
    assert body['district_name'] == 'Петроградский район'
    assert body['difficulty_name'] == 'Петроградский район'
    assert body['location']['district_id'] == 'petrogradsky'

    with app_module.app.app_context():
        game = GameSession.query.one()
        assert (game.difficulty, game.district_id) == ('district', 'petrogradsky')
        rounds = GameRound.query.order_by(GameRound.round_number).all()
        assert len(rounds) == 5
        assert all(point_in_district(
            rnd.gen_latitude, rnd.gen_longitude, game.district_id
        ) for rnd in rounds)


def test_start_rejects_unknown_or_ambiguous_district(client):
    missing = client.post('/api/game/start', json={'difficulty': 'district'})
    unknown = _start_district(client, 'not-a-district')
    ambiguous = client.post('/api/game/start', json={
        'difficulty': 'hard', 'district_id': 'petrogradsky',
    })

    assert missing.status_code == 400
    assert unknown.status_code == 400
    assert ambiguous.status_code == 400

    # Обратная совместимость старого API не изменилась.
    legacy = client.post('/api/game/start', json={'difficulty': 'bogus'})
    assert legacy.status_code == 200
    assert legacy.get_json()['difficulty'] == 'medium'
    assert legacy.get_json()['district_id'] is None


def test_skip_keeps_replacement_inside_same_district(client):
    from districts import point_in_district

    first = _start_district(client, 'kronshtadtsky').get_json()['location']
    skipped = client.post('/api/game/skip_location', json={
        'round_id': first['round_id'],
        'location_version': first['location_version'],
        'reason': 'no_coverage',
    })

    assert skipped.status_code == 200
    location = skipped.get_json()
    assert location['district_id'] == 'kronshtadtsky'
    assert location['location_version'] == 1
    assert point_in_district(
        location['latitude'], location['longitude'], 'kronshtadtsky'
    )


def test_validate_panorama_rejects_neighbouring_district_before_ready(client, app_module):
    from models import GameRound

    location = _start_district(client).get_json()['location']
    # Две точки в 22 м по разные стороны общей границы
    # Петроградского и Василеостровского районов.
    inside = (59.9457, 30.3159)
    outside = (59.9455, 30.3159)
    with app_module.app.app_context():
        rnd = app_module.db.session.get(GameRound, location['round_id'])
        rnd.gen_latitude, rnd.gen_longitude = inside
        app_module.db.session.commit()

    not_ready = client.post('/api/game/ready', json={'round_id': location['round_id']})
    assert not_ready.status_code == 409
    assert not_ready.get_json()['reason'] == 'panorama_not_validated'

    rejected = client.post('/api/game/validate_panorama', json={
        'round_id': location['round_id'], 'location_version': 0,
        'latitude': outside[0], 'longitude': outside[1],
    })
    assert rejected.status_code == 200
    assert rejected.get_json() == {'valid': False, 'reason': 'outside_district'}

    accepted = client.post('/api/game/validate_panorama', json={
        'round_id': location['round_id'], 'location_version': 0,
        'latitude': inside[0], 'longitude': inside[1],
    })
    assert accepted.get_json() == {'valid': True}
    assert client.post('/api/game/ready', json={
        'round_id': location['round_id'],
    }).status_code == 200


def test_stale_panorama_validation_cannot_mutate_skipped_location(client):
    first = _start_district(client).get_json()['location']
    client.post('/api/game/skip_location', json={
        'round_id': first['round_id'], 'location_version': 0,
        'reason': 'outside_district',
    })

    stale = _validate_location(client, first)
    assert stale.status_code == 409


def test_guess_rechecks_district_and_keeps_prevalidated_point(client, app_module):
    from models import GameRound, GameSession

    location = _start_district(client).get_json()['location']
    inside = (59.9457, 30.3159)
    outside = (59.9455, 30.3159)
    with app_module.app.app_context():
        rnd = app_module.db.session.get(GameRound, location['round_id'])
        rnd.gen_latitude, rnd.gen_longitude = inside
        app_module.db.session.commit()

    client.post('/api/game/validate_panorama', json={
        'round_id': location['round_id'], 'location_version': 0,
        'latitude': inside[0], 'longitude': inside[1],
    })
    rejected = client.post('/api/game/guess', json={
        'round_id': location['round_id'],
        'latitude': inside[0], 'longitude': inside[1],
        'panorama_latitude': outside[0], 'panorama_longitude': outside[1],
    })
    assert rejected.status_code == 409
    assert rejected.get_json()['reason'] == 'outside_district'

    with app_module.app.app_context():
        rnd = app_module.db.session.get(GameRound, location['round_id'])
        game = GameSession.query.one()
        assert (rnd.actual_latitude, rnd.actual_longitude) == inside
        assert rnd.answered_at is None
        assert game.current_round == 0


def test_outer_district_point_enters_district_pool_but_not_legacy_hard(app, app_module,
                                                                      monkeypatch):
    from models import VerifiedPoint
    from pool import add_verified_point, choose_round_candidates

    kronstadt = (59.9911, 29.7770)
    with app.app_context():
        add_verified_point(*kronstadt)
        point = VerifiedPoint.query.one()
        assert point.district_id == 'kronshtadtsky'

        monkeypatch.setattr('random.random', lambda: 0.0)
        district_candidate = choose_round_candidates(
            'district', 1, district_id='kronshtadtsky'
        )[0]
        hard_candidate = choose_round_candidates('hard', 1, prefer_pool=True)[0]

    assert district_candidate == (*kronstadt, 'pool')
    assert hard_candidate[2] == 'explore'
    assert hard_candidate[:2] != kronstadt


def test_district_challenge_results_and_leaderboard_keep_metadata(client, app):
    started = _start_district(client, player_name='Автор').get_json()
    assert started['district_id'] == 'petrogradsky'
    author_locations = _finish_district_game(client)
    results = client.get('/api/game/results').get_json()
    token = results['challenge_token']
    assert results['district_id'] == 'petrogradsky'
    assert results['district_name'] == 'Петроградский район'

    info = client.get(f'/api/challenge/{token}').get_json()
    assert info['difficulty'] == 'district'
    assert info['district_id'] == 'petrogradsky'

    friend = app.test_client()
    friend_start = friend.post('/api/game/start', json={
        'challenge_token': token, 'district_id': 'kurortny',
    }).get_json()
    assert friend_start['difficulty'] == 'district'
    assert friend_start['district_id'] == 'petrogradsky'
    friend_points = []
    for _ in range(5):
        location = friend.get('/api/game/location').get_json()
        friend_points.append((location['latitude'], location['longitude']))
        assert location['district_id'] == 'petrogradsky'
        friend.post('/api/game/guess', json={
            'round_id': location['round_id'],
            'latitude': location['latitude'], 'longitude': location['longitude'],
        })
    assert friend_points == [
        (location['latitude'], location['longitude']) for location in author_locations
    ]

    board = client.get(
        '/api/leaderboard?difficulty=district&district_id=petrogradsky'
    ).get_json()
    assert board['district_id'] == 'petrogradsky'
    assert board['leaderboard'][0]['district_name'] == 'Петроградский район'
