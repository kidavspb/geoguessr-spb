"""Backend/API integration административных районов."""

import pytest


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
    from districts import district_map
    assert body['district_bounds'] == list(district_map()['petrogradsky'].bounds)
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


def test_small_district_pool_does_not_repeat_generated_round_points(
        app, monkeypatch):
    """Даже повторные ответы генератора не создают одинаковые раунды."""
    from models import VerifiedPoint, db
    from pool import choose_round_candidates

    points = [
        (59.947, 30.3159),
        (59.948, 30.3159),
        (59.949, 30.3159),
        (59.950, 30.3159),
        (59.951, 30.3159),
    ]
    generated = iter(point for point in points for _ in range(2))
    monkeypatch.setattr('pool.generate_district_point', lambda _district: next(generated))
    monkeypatch.setattr('pool.random.random', lambda: 0.0)

    with app.app_context():
        db.session.add(VerifiedPoint(
            latitude=points[0][0], longitude=points[0][1],
            lat_key=int(round(points[0][0] * 10000)),
            lon_key=int(round(points[0][1] * 10000)),
            dist_from_center_km=1.0,
            district_id='petrogradsky',
        ))
        db.session.commit()
        candidates = choose_round_candidates(
            'district', 5, district_id='petrogradsky'
        )

    assert [(lat, lon) for lat, lon, _source in candidates] == points
    assert [source for _lat, _lon, source in candidates] == [
        'pool', 'explore', 'explore', 'explore', 'explore'
    ]


@pytest.mark.parametrize('latitude_offset', [0.0, 0.00005])
def test_duplicate_panorama_is_replaced_without_poisoning_pool(
        client, app_module, monkeypatch, latitude_offset):
    """Две близкие исходные точки не должны показать одну съёмку дважды."""
    from models import GameRound, VerifiedPoint

    started = _start_district(client).get_json()
    assert started['location']['round'] == 1
    first_seed = (59.947, 30.3159)
    second_seed = (59.948, 30.3159)
    panorama = (59.9475, 30.3159)
    replacement = (59.952, 30.3159)

    with app_module.app.app_context():
        rounds = GameRound.query.order_by(GameRound.round_number).all()
        rounds[0].gen_latitude, rounds[0].gen_longitude = first_seed
        rounds[1].gen_latitude, rounds[1].gen_longitude = second_seed
        app_module.db.session.commit()

    first = client.get('/api/game/location').get_json()
    validated = client.post('/api/game/validate_panorama', json={
        'round_id': first['round_id'],
        'location_version': first['location_version'],
        'latitude': panorama[0], 'longitude': panorama[1],
    })
    assert validated.status_code == 200
    assert validated.get_json() == {'valid': True}
    guessed = client.post('/api/game/guess', json={
        'round_id': first['round_id'],
        'latitude': panorama[0], 'longitude': panorama[1],
        'panorama_latitude': panorama[0],
        'panorama_longitude': panorama[1],
    })
    assert guessed.status_code == 200

    second = client.get('/api/game/location').get_json()
    assert (second['latitude'], second['longitude']) == second_seed
    duplicate = client.post('/api/game/validate_panorama', json={
        'round_id': second['round_id'],
        'location_version': second['location_version'],
        'latitude': panorama[0] + latitude_offset,
        'longitude': panorama[1],
    })
    assert duplicate.status_code == 200
    assert duplicate.get_json() == {
        'valid': False, 'reason': 'duplicate_panorama'
    }

    monkeypatch.setattr('pool.generate_district_point', lambda _district: replacement)
    skipped = client.post('/api/game/skip_location', json={
        'round_id': second['round_id'],
        'location_version': second['location_version'],
        'reason': 'duplicate_panorama',
    })
    assert skipped.status_code == 200
    assert (skipped.get_json()['latitude'], skipped.get_json()['longitude']) == replacement
    with app_module.app.app_context():
        assert VerifiedPoint.query.one().fail_count == 0
        assert app_module.db.session.get(GameRound, second['round_id']).actual_latitude is None


def test_hard_start_generates_all_rounds_inside_exact_city(client, app_module):
    from districts import point_in_city
    from models import GameRound, GameSession

    response = client.post('/api/game/start', json={'difficulty': 'hard'})

    assert response.status_code == 200
    body = response.get_json()
    assert body['difficulty'] == 'hard'
    assert body['difficulty_name'] == 'Весь город'
    assert body['location']['requires_spatial_validation'] is True
    with app_module.app.app_context():
        game = GameSession.query.one()
        rounds = GameRound.query.order_by(GameRound.round_number).all()
        assert game.district_id is None
        assert len(rounds) == 5
        assert all(point_in_city(
            rnd.gen_latitude, rnd.gen_longitude
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


def test_hard_preflight_ready_and_guess_enforce_exact_city(client, app_module):
    from models import GameRound, GameSession

    location = client.post(
        '/api/game/start', json={'difficulty': 'hard'}
    ).get_json()['location']
    # Две близкие точки у западной границы Кировского района: первая внутри
    # canonical union, вторая примерно в 150 м от неё, но уже снаружи.
    inside = (59.9140, 30.2422)
    outside = (59.915318, 30.242204)
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
    assert rejected.get_json() == {'valid': False, 'reason': 'outside_city'}

    accepted = client.post('/api/game/validate_panorama', json={
        'round_id': location['round_id'], 'location_version': 0,
        'latitude': inside[0], 'longitude': inside[1],
    })
    assert accepted.get_json() == {'valid': True}
    assert client.post('/api/game/ready', json={
        'round_id': location['round_id'],
    }).status_code == 200

    rejected_guess = client.post('/api/game/guess', json={
        'round_id': location['round_id'],
        'latitude': inside[0], 'longitude': inside[1],
        'panorama_latitude': outside[0], 'panorama_longitude': outside[1],
    })
    assert rejected_guess.status_code == 409
    assert rejected_guess.get_json()['reason'] == 'outside_city'
    with app_module.app.app_context():
        rnd = app_module.db.session.get(GameRound, location['round_id'])
        game = GameSession.query.one()
        assert (rnd.actual_latitude, rnd.actual_longitude) == inside
        assert rnd.answered_at is None
        assert game.current_round == 0


def test_outside_city_skip_does_not_poison_pool(client, app_module):
    from districts import point_in_city
    from models import VerifiedPoint
    from pool import add_verified_point

    location = client.post(
        '/api/game/start', json={'difficulty': 'hard'}
    ).get_json()['location']
    with app_module.app.app_context():
        add_verified_point(location['latitude'], location['longitude'])
        original = VerifiedPoint.query.one()
        original_id = original.id

    skipped = client.post('/api/game/skip_location', json={
        'round_id': location['round_id'],
        'location_version': location['location_version'],
        'reason': 'outside_city',
    })

    assert skipped.status_code == 200
    assert point_in_city(skipped.get_json()['latitude'], skipped.get_json()['longitude'])
    with app_module.app.app_context():
        assert app_module.db.session.get(VerifiedPoint, original_id).fail_count == 0


def test_city_pool_uses_any_inside_district_and_excludes_outside_point(
        app, app_module, monkeypatch):
    from models import VerifiedPoint
    from pool import add_verified_point, choose_round_candidates

    kronstadt = (59.9911, 29.7770)
    outside_city = (59.915318, 30.242204)
    with app.app_context():
        add_verified_point(*kronstadt)
        add_verified_point(*outside_city)
        points = VerifiedPoint.query.order_by(VerifiedPoint.id).all()
        assert [point.district_id for point in points] == ['kronshtadtsky', None]

        monkeypatch.setattr('random.random', lambda: 0.0)
        district_candidate = choose_round_candidates(
            'district', 1, district_id='kronshtadtsky'
        )[0]
        hard_candidate = choose_round_candidates('hard', 1, prefer_pool=True)[0]

    assert district_candidate == (*kronstadt, 'pool')
    assert hard_candidate == (*kronstadt, 'pool_recovery')


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


def test_legacy_hard_challenge_with_stored_outside_point_remains_playable(
        client, app_module):
    from models import GameRound, GameSession, utcnow

    outside_city = (59.915318, 30.242204)
    with app_module.app.app_context():
        source = GameSession(
            player_name='Автор старого челленджа',
            difficulty='hard',
            total_score=25000,
            rounds_played=5,
            current_round=5,
            challenge_token='legacy-hard-challenge',
            completed_at=utcnow(),
        )
        app_module.db.session.add(source)
        app_module.db.session.flush()
        for round_number in range(1, 6):
            app_module.db.session.add(GameRound(
                session_id=source.id,
                round_number=round_number,
                gen_latitude=outside_city[0],
                gen_longitude=outside_city[1],
                actual_latitude=outside_city[0],
                actual_longitude=outside_city[1],
                answered_at=utcnow(),
                score=5000,
            ))
        app_module.db.session.commit()

    started = client.post('/api/game/start', json={
        'challenge_token': 'legacy-hard-challenge',
    })

    assert started.status_code == 200
    location = started.get_json()['location']
    assert (location['latitude'], location['longitude']) == outside_city
    assert location['requires_spatial_validation'] is False
    assert client.post('/api/game/ready', json={
        'round_id': location['round_id'],
    }).status_code == 200
    guessed = client.post('/api/game/guess', json={
        'round_id': location['round_id'],
        'latitude': outside_city[0], 'longitude': outside_city[1],
        'panorama_latitude': outside_city[0],
        'panorama_longitude': outside_city[1],
    })
    assert guessed.status_code == 200
