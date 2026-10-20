"""Восстановление раунда из пула не возвращается к только что провалившимся точкам."""
import pool
from models import VerifiedPoint

POOL_POINTS = [
    (59.938600, 30.314100),  # Дворцовая площадь
    (59.934300, 30.335100),  # Невский проспект
    (59.931100, 30.360900),  # площадь Восстания
]


def _pool_point(lat, lon):
    for point in POOL_POINTS:
        if abs(point[0] - lat) < 1e-6 and abs(point[1] - lon) < 1e-6:
            return point
    return None


def test_no_coverage_recovery_never_reoffers_a_failed_pool_point(client, app):
    with app.app_context():
        for lat, lon in POOL_POINTS:
            pool.add_verified_point(lat, lon)

    location = client.post('/api/game/start', json={'difficulty': 'center'}).get_json()['location']
    offered = []
    for _ in range(5):
        response = client.post('/api/game/skip_location', json={
            'round_id': location['round_id'],
            'location_version': location['location_version'],
            'reason': 'no_coverage',
        })
        assert response.status_code == 200
        location = response.get_json()
        point = _pool_point(location['latitude'], location['longitude'])
        if point is not None:
            offered.append(point)

    # Три точки пула — четыре попытки восстановления: старый код обязан был
    # повторить одну из уже провалившихся точек.
    assert len(offered) == len(set(offered)) == len(POOL_POINTS)
    with app.app_context():
        # Каждая точка провалилась ровно один раз и осталась в пуле.
        assert sorted(p.fail_count for p in VerifiedPoint.query.all()) == [1, 1, 1]
