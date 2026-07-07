"""Статистика сложности мест — из накопленных игровых данных.

В game_rounds копятся пары «догадка → ответ». Сгруппировав промахи по
точкам пула (ключ — те же округлённые координаты, что и в дедупликации),
получаем фактическую сложность каждого места: средний промах игроков.
"""
from models import GameRound

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


