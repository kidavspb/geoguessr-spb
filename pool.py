"""Пул проверенных точек — мест, где панорама Яндекса точно существует.

Пополняется автоматически из честных игр (см. set_actual_point): каждая
панорама, прошедшая антифрод, попадает в пул. Новые игры берут точки отсюда —
раунд стартует сразу, без перебора случайных точек в поисках панорамы.
"""
import logging
import random

from sqlalchemy import or_

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


def mark_point_failed(lat, lon, *, commit=True):
    """Панорама у точки не нашлась: считаем неудачи, на пороге удаляем.

    Вызывается при перегенерации точки раунда — если исходная точка была
    из пула, значит панорама там пропала (Яндекс иногда убирает съёмку).
    """
    try:
        point = _point_by_coords(lat, lon)
        if point is None:
            return
        point.fail_count = (point.fail_count or 0) + 1
        if point.fail_count >= POOL_MAX_FAILS:
            db.session.delete(point)
            logger.info('Точка пула удалена после %d неудач: %.5f, %.5f',
                        POOL_MAX_FAILS, lat, lon)
        if commit:
            db.session.commit()
    except Exception:
        db.session.rollback()
        logger.debug('Не удалось обновить статус точки пула', exc_info=True)


def choose_round_candidates(difficulty, count, *, pool_only=False,
                            prefer_pool=False, exclude=None):
    """Кандидаты раундов вместе с источником точки.

    Обычная игра сохраняет прежнюю долю исследовательских точек — молодой
    проект продолжает открывать новые места. ``prefer_pool`` используется
    после подтверждённого отсутствия панорамы: исследовательская попытка уже
    состоялась, и дальше важнее быстро восстановить раунд. ``pool_only`` нужен
    для честных общих наборов (вызов дня), если в пуле хватает точек.
    ``exclude`` не даёт восстановлению повторить место из той же партии.

    Возвращает ``[(latitude, longitude, source), ...]``.
    """
    query = VerifiedPoint.query
    for exclude_lat, exclude_lon in (exclude or ()):
        exclude_lat_key = int(round(exclude_lat * 10000))
        exclude_lon_key = int(round(exclude_lon * 10000))
        query = query.filter(or_(
            VerifiedPoint.lat_key != exclude_lat_key,
            VerifiedPoint.lon_key != exclude_lon_key,
        ))
    radius = POOL_RADIUS_KM.get(difficulty)
    if radius is not None:
        query = query.filter(VerifiedPoint.dist_from_center_km <= radius)

    pool = []
    pool_count = query.count()
    enough_for_regular = pool_count >= POOL_MIN_SIZE
    enough_for_pool_only = pool_count >= count
    if enough_for_regular or (pool_only and enough_for_pool_only) or (prefer_pool and pool_count):
        pool = query.order_by(db.func.random()).limit(count).all()

    points = []
    pool_iter = iter(pool)
    for _ in range(count):
        should_use_pool = pool_only or prefer_pool or random.random() < POOL_USE_PROBABILITY
        picked = next(pool_iter, None) if should_use_pool else None
        if picked is not None:
            source = 'pool_recovery' if prefer_pool else 'pool'
            points.append((picked.latitude, picked.longitude, source))
        else:
            lat, lon = generate_random_point(difficulty)
            points.append((lat, lon, 'explore'))
    return points


def choose_round_points(difficulty, count, **kwargs):
    """Совместимая обёртка, возвращающая только пары координат."""
    return [(lat, lon) for lat, lon, _source in
            choose_round_candidates(difficulty, count, **kwargs)]


def add_verified_point(lat, lon):
    """Добавить панораму в пул проверенных точек (с дедупликацией ~10 м)."""
    if not (SPB_BOUNDS['lat_min'] - 0.01 <= lat <= SPB_BOUNDS['lat_max'] + 0.01 and
            SPB_BOUNDS['lon_min'] - 0.02 <= lon <= SPB_BOUNDS['lon_max'] + 0.02):
        return
    try:
        lat_key, lon_key = int(round(lat * 10000)), int(round(lon * 10000))
        existing = VerifiedPoint.query.filter_by(lat_key=lat_key, lon_key=lon_key).first()
        if existing is not None:
            # Панорама подтверждена живой — прощаем прошлые неудачи
            if existing.fail_count:
                existing.fail_count = 0
                db.session.commit()
            return
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
