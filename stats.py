"""Статистика сложности мест — из накопленных игровых данных.

В game_rounds копятся пары «догадка → ответ». Сгруппировав промахи по
точкам пула (ключ — те же округлённые координаты, что и в дедупликации),
получаем фактическую сложность каждого места: средний промах игроков.
"""
from models import db, GameRound

# Минимум сыгранных раундов, чтобы судить о сложности точки
MIN_GAMES_FOR_STATS = 3


def _coord_key(lat, lon):
    """Ключ точки — как в пуле (округление ~10 м)."""
    return int(round(lat * 10000)), int(round(lon * 10000))


def point_difficulty_map():
    """Средний промах по каждой точке: {(lat_key, lon_key): (avg_km, games)}.

    Считаем только по раундам с ответом и известной точкой панорамы.
    Группировку и среднее считает СУБД; Python получает только готовые места.
    """
    # Агрегируем в СУБД, а не вытаскиваем всю историю раундов в Python на
    # каждый /guess. Объём переданных данных теперь пропорционален числу мест,
    # а не числу всех сыгранных раундов.
    lat_key = GameRound.actual_latitude * 10000
    lon_key = GameRound.actual_longitude * 10000
    lat_key_rounded = db.func.round(lat_key)
    lon_key_rounded = db.func.round(lon_key)
    rows = (GameRound.query
            .filter(GameRound.answered_at.isnot(None),
                    GameRound.distance_km.isnot(None),
                    GameRound.actual_latitude.isnot(None),
                    GameRound.actual_longitude.isnot(None))
            .with_entities(
                lat_key_rounded,
                lon_key_rounded,
                db.func.avg(GameRound.distance_km),
                db.func.count(GameRound.id),
            )
            .group_by(lat_key_rounded, lon_key_rounded)
            .having(db.func.count(GameRound.id) >= MIN_GAMES_FOR_STATS)
            .all())

    return {(int(lat), int(lon)): (float(avg), int(games))
            for lat, lon, avg, games in rows}


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
