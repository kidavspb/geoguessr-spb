"""Пул проверенных точек — мест, где панорама Яндекса точно существует.

Пополняется из validate_panorama и guess (set_actual_point — старый API).
Клиентские координаты ограничены серверной точкой и выбранной территорией;
это базовая проверка, а не подтверждение панорамы независимым источником.
"""
import logging
import random

from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError

from models import db, VerifiedPoint
from game_logic import (
    MIN_ROUND_LOCATION_DISTANCE_KM, SPB_BOUNDS, SPB_CENTER,
    generate_random_point, haversine_distance,
)
from districts import (
    DistrictDataError, district_for_point, generate_district_point,
    is_valid_district_id,
)

logger = logging.getLogger(__name__)

# Пул начинаем использовать, когда точек достаточно, и оставляем долю
# свежесгенерированных, чтобы пул продолжал расти.
POOL_MIN_SIZE = 15
POOL_USE_PROBABILITY = 0.7
# Максимальное расстояние точки пула от центра для прежних центральных режимов.
# Hard отбирается отдельно по предвычисленному district_id.
POOL_RADIUS_KM = {'center': 3.0, 'medium': 6.5, 'hard': None}
# Исторический охват пула остаётся неизменным для center/medium.
LEGACY_POOL_BOUNDS = {
    'lat_min': SPB_BOUNDS['lat_min'] - 0.01,
    'lat_max': SPB_BOUNDS['lat_max'] + 0.01,
    'lon_min': SPB_BOUNDS['lon_min'] - 0.02,
    'lon_max': SPB_BOUNDS['lon_max'] + 0.02,
}
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
    except SQLAlchemyError:
        if not commit:
            # Внешняя транзакция владеет также сменой точки и lock игры.
            # Нельзя молча откатить её и продолжить skip без защиты от гонок.
            raise
        db.session.rollback()
        logger.warning('Не удалось обновить статус точки пула', exc_info=True)


def choose_round_candidates(difficulty, count, *, district_id=None,
                            pool_only=False, prefer_pool=False, exclude=None):
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
    if difficulty == 'district':
        if not is_valid_district_id(district_id):
            raise ValueError(f'Неизвестный district id: {district_id!r}')
        query = query.filter(VerifiedPoint.district_id == district_id)
    elif difficulty == 'hard':
        # district_id уже вычисляется один раз при добавлении точки и был
        # backfill-нут миграцией. Non-NULL эквивалентен membership в union 18
        # canonical районов, поэтому GEOS не нужен на каждую строку/запрос.
        query = query.filter(VerifiedPoint.district_id.isnot(None))
    else:
        # Center/medium сохраняют исторические bounds и радиус.
        query = query.filter(
            VerifiedPoint.latitude >= LEGACY_POOL_BOUNDS['lat_min'],
            VerifiedPoint.latitude <= LEGACY_POOL_BOUNDS['lat_max'],
            VerifiedPoint.longitude >= LEGACY_POOL_BOUNDS['lon_min'],
            VerifiedPoint.longitude <= LEGACY_POOL_BOUNDS['lon_max'],
        )
        radius = POOL_RADIUS_KM.get(difficulty)
        if radius is not None:
            query = query.filter(VerifiedPoint.dist_from_center_km <= radius)

    excluded_coords = list(exclude or ())
    pool = []
    pool_count = query.count()
    # Районный пул на старте может быть мал: берём любые известные
    # панорамы и добиваем партию exploration-точками. Порог старых
    # режимов остаётся без изменений.
    enough_for_regular = (pool_count > 0 if difficulty == 'district'
                          else pool_count >= POOL_MIN_SIZE)
    enough_for_pool_only = pool_count >= count
    if enough_for_regular or (pool_only and enough_for_pool_only) or (prefer_pool and pool_count):
        # Несколько записей пула могут лежать рядом с уже сыгранной съёмкой.
        # Берём запас кандидатов, чтобы найти другую точку до fallback-генерации.
        pool = query.order_by(db.func.random()).limit(
            min(pool_count, max(count * 10, 30))
        ).all()

    points = []
    pool_iter = iter(pool)

    def is_repeated(lat, lon):
        return any(
            haversine_distance(lat, lon, other_lat, other_lon)
            < MIN_ROUND_LOCATION_DISTANCE_KM
            for other_lat, other_lon in excluded_coords
        )

    for _ in range(count):
        should_use_pool = pool_only or prefer_pool or random.random() < POOL_USE_PROBABILITY
        picked = None
        if should_use_pool:
            for candidate in pool_iter:
                if not is_repeated(candidate.latitude, candidate.longitude):
                    picked = candidate
                    break
        if picked is not None:
            source = 'pool_recovery' if prefer_pool else 'pool'
            lat, lon = picked.latitude, picked.longitude
        else:
            # Генератор обычно выдаёт новое место сразу. Повторные попытки
            # нужны для небольших районов и пересечения с точками пула.
            for _attempt in range(100):
                if difficulty == 'district':
                    lat, lon = generate_district_point(district_id)
                else:
                    lat, lon = generate_random_point(difficulty)
                if not is_repeated(lat, lon):
                    break
            else:
                raise ValueError('Не удалось подобрать неповторяющуюся точку раунда')
            source = 'explore'
        points.append((lat, lon, source))
        excluded_coords.append((lat, lon))
    return points


def choose_round_points(difficulty, count, **kwargs):
    """Координаты для общего набора daily, без диагностического source."""
    return [(lat, lon) for lat, lon, _source in
            choose_round_candidates(difficulty, count, **kwargs)]


def add_verified_point(lat, lon):
    """Добавить панораму в пул проверенных точек (с дедупликацией ~10 м)."""
    in_legacy_bounds = (
        LEGACY_POOL_BOUNDS['lat_min'] <= lat <= LEGACY_POOL_BOUNDS['lat_max'] and
        LEGACY_POOL_BOUNDS['lon_min'] <= lon <= LEGACY_POOL_BOUNDS['lon_max']
    )
    try:
        point_district_id = district_for_point(lat, lon)
    except DistrictDataError:
        # Повреждённый optional dataset не должен ломать старые режимы,
        # но вне прежних bounds без валидации ничего не сохраняем.
        logger.error('Не удалось классифицировать точку пула', exc_info=True)
        point_district_id = None
    if not in_legacy_bounds and point_district_id is None:
        return
    try:
        lat_key, lon_key = int(round(lat * 10000)), int(round(lon * 10000))
        existing = _point_by_coords(lat, lon)
        if existing is not None:
            # Панорама подтверждена живой — прощаем прошлые неудачи
            changed = False
            if existing.fail_count:
                existing.fail_count = 0
                changed = True
            if existing.district_id is None and point_district_id is not None:
                existing.district_id = point_district_id
                changed = True
            if changed:
                db.session.commit()
            return
        db.session.add(VerifiedPoint(
            latitude=lat,
            longitude=lon,
            lat_key=lat_key,
            lon_key=lon_key,
            dist_from_center_km=haversine_distance(lat, lon, *SPB_CENTER),
            district_id=point_district_id,
        ))
        db.session.commit()
    except SQLAlchemyError:
        # Гонка на unique-ключе или временная блокировка SQLite — точка пула
        # не критична, просто пропускаем.
        db.session.rollback()
        logger.warning('Точка пула не добавлена (гонка/блокировка)', exc_info=True)
