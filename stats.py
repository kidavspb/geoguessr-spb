"""Статистика сложности мест — из накопленных игровых данных.

В game_rounds копятся пары «догадка → ответ». Сгруппировав промахи по
точкам пула (ключ — те же округлённые координаты, что и в дедупликации),
получаем фактическую сложность каждого места: средний промах игроков.
"""
from models import GameRound, VerifiedPoint

# Минимум сыгранных раундов, чтобы судить о сложности точки
MIN_GAMES_FOR_STATS = 3


def _coord_key(lat, lon):
    """Ключ точки — как в пуле (округление ~10 м)."""
    return int(round(lat * 10000)), int(round(lon * 10000))


def point_difficulty_map():
    """Средний промах по каждой точке: {(lat_key, lon_key): (avg_km, games)}.

    Считаем только по раундам с ответом и известной точкой панорамы.
    Объём данных здесь небольшой (тысячи строк) — агрегируем в Python.
    """
    rounds = (GameRound.query
              .filter(GameRound.answered_at.isnot(None),
                      GameRound.distance_km.isnot(None),
                      GameRound.actual_latitude.isnot(None))
              .with_entities(GameRound.actual_latitude,
                             GameRound.actual_longitude,
                             GameRound.distance_km)
              .all())

    sums = {}
    for lat, lon, dist in rounds:
        key = _coord_key(lat, lon)
        total, count = sums.get(key, (0.0, 0))
        sums[key] = (total + dist, count + 1)

    return {key: (total / count, count)
            for key, (total, count) in sums.items()
            if count >= MIN_GAMES_FOR_STATS}


def difficulty_percentile(lat, lon):
    """Насколько место сложнее остальных: 0–100 или None, если данных мало.

    75 означает «промахи здесь больше, чем у 75% изученных мест города».
    """
    stats = point_difficulty_map()
    key = _coord_key(lat, lon)
    if key not in stats or len(stats) < 4:
        return None
    avg = stats[key][0]
    easier = sum(1 for k, (other_avg, _) in stats.items() if k != key and other_avg < avg)
    return int(round(100 * easier / (len(stats) - 1)))


def hardest_places(limit=10):
    """Самые сложные места города — для публичной страницы-шоукейса.

    Возвращает точки пула с наибольшим средним промахом (и числом игр).
    """
    stats = point_difficulty_map()
    if not stats:
        return []

    # Подтягиваем координаты из пула (в статистике — только ключи)
    points = VerifiedPoint.query.all()
    by_key = {(p.lat_key, p.lon_key): p for p in points}

    places = []
    for key, (avg_km, games) in stats.items():
        point = by_key.get(key)
        if point is None:
            continue  # точка выбыла из пула — в шоукейс не попадает
        places.append({
            'latitude': point.latitude,
            'longitude': point.longitude,
            'avg_miss_km': round(avg_km, 2),
            'games': games,
        })

    places.sort(key=lambda p: p['avg_miss_km'], reverse=True)
    return places[:limit]


def hardest_pool_points(count, min_pool=10):
    """Точки для режима «Хардкор»: случайная выборка из трети самых сложных.

    Возвращает [(lat, lon), ...] или None, если изученных точек ещё мало —
    тогда вызывающий код падает обратно на обычную генерацию по всему городу.
    """
    import random

    stats = point_difficulty_map()
    points = VerifiedPoint.query.all()
    by_key = {(p.lat_key, p.lon_key): p for p in points}

    ranked = sorted(
        ((stats[key][0], by_key[key]) for key in stats if key in by_key),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if len(ranked) < min_pool:
        return None

    top = ranked[:max(count, len(ranked) // 3)]
    chosen = random.sample(top, min(count, len(top)))
    return [(p.latitude, p.longitude) for _, p in chosen]
