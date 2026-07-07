"""Пул проверенных точек — мест, где панорама Яндекса точно существует.

Пополняется автоматически из честных игр (см. set_actual_point): каждая
панорама, прошедшая антифрод, попадает в пул. Новые игры берут точки отсюда —
раунд стартует сразу, без перебора случайных точек в поисках панорамы.
"""
import logging
import random

from models import db, VerifiedPoint
from game_logic import SPB_BOUNDS, SPB_CENTER, generate_random_point, haversine_distance

logger = logging.getLogger(__name__)

# Пул начинаем использовать, когда точек достаточно, и оставляем долю
# свежесгенерированных, чтобы пул продолжал расти.
POOL_MIN_SIZE = 15
POOL_USE_PROBABILITY = 0.7
# Максимальное расстояние точки пула от центра для режима сложности (км);
# None — без ограничения (весь город).
POOL_RADIUS_KM = {'center': 3.0, 'medium': 6.5, 'hard': None}
# После скольких неудачных поисков панорамы подряд точка выбывает из пула
POOL_MAX_FAILS = 3


def _point_by_coords(lat, lon):
    """Точка пула по координатам (с точностью ключа дедупликации ~10 м)."""
    return VerifiedPoint.query.filter_by(
        lat_key=int(round(lat * 10000)),
        lon_key=int(round(lon * 10000)),
    ).first()


def mark_point_failed(lat, lon):
    """Панорама у точки не нашлась: считаем неудачи, на пороге удаляем.

    Вызывается при перегенерации точки раунда — если исходная точка была
    из пула, значит панорама там пропала (Яндекс иногда убирает съёмку).
    """
    point = _point_by_coords(lat, lon)
    if point is None:
        return
    point.fail_count = (point.fail_count or 0) + 1
    if point.fail_count >= POOL_MAX_FAILS:
        db.session.delete(point)
        logger.info('Точка пула удалена после %d неудач: %.5f, %.5f',
                    POOL_MAX_FAILS, lat, lon)
    db.session.commit()


def choose_round_points(difficulty, count):
    """Точки для раундов игры: смесь пула проверенных панорам и новых случайных.

    Пока пул мал — все точки случайные. Когда точек достаточно, большинство
    раундов стартует с проверенной точки (панорама точно есть), но часть
    по-прежнему генерируется — так пул растёт.
    """
    query = VerifiedPoint.query
    radius = POOL_RADIUS_KM.get(difficulty)
    if radius is not None:
        query = query.filter(VerifiedPoint.dist_from_center_km <= radius)

    pool = []
    if query.count() >= POOL_MIN_SIZE:
        pool = query.order_by(db.func.random()).limit(count).all()

    points = []
    pool_iter = iter(pool)
    for _ in range(count):
        picked = next(pool_iter, None) if random.random() < POOL_USE_PROBABILITY else None
        if picked is not None:
            points.append((picked.latitude, picked.longitude))
        else:
            points.append(generate_random_point(difficulty))
    return points


def add_verified_point(lat, lon):
    """Добавить панораму в пул проверенных точек (с дедупликацией ~10 м)."""
    if not (SPB_BOUNDS['lat_min'] - 0.01 <= lat <= SPB_BOUNDS['lat_max'] + 0.01 and
            SPB_BOUNDS['lon_min'] - 0.02 <= lon <= SPB_BOUNDS['lon_max'] + 0.02):
        return
    lat_key, lon_key = int(round(lat * 10000)), int(round(lon * 10000))
    existing = VerifiedPoint.query.filter_by(lat_key=lat_key, lon_key=lon_key).first()
    if existing is not None:
        # Панорама подтверждена живой — прощаем прошлые неудачи
        if existing.fail_count:
            existing.fail_count = 0
            db.session.commit()
        return
    try:
        db.session.add(VerifiedPoint(
            latitude=lat,
            longitude=lon,
            lat_key=lat_key,
            lon_key=lon_key,
            dist_from_center_km=haversine_distance(lat, lon, *SPB_CENTER),
        ))
        db.session.commit()
    except Exception:
        # Гонка на unique-ключе или временная блокировка SQLite — точка пула
        # не критична, просто пропускаем.
        db.session.rollback()
        logger.debug('Точка пула не добавлена (гонка/блокировка)', exc_info=True)
