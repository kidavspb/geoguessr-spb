"""Геометрия 18 административных районов Санкт-Петербурга.

Единственный source of truth — ``data/spb_districts.geojson``. Модуль
загружает и валидирует его один раз на процесс. GeoJSON хранит
координаты как [longitude, latitude]; публичные функции принимают
привычный для проекта порядок (latitude, longitude).
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from shapely import coverage_union_all
from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.prepared import prep


DISTRICTS_GEOJSON_PATH = Path(__file__).resolve().parent / 'data' / 'spb_districts.geojson'

# Порядок стабилен: точка ровно на общей границе двух районов
# относится к первому из них в этом списке.
DISTRICT_NAMES = {
    'admiralteysky': 'Адмиралтейский район',
    'vasileostrovsky': 'Василеостровский район',
    'vyborgsky': 'Выборгский район',
    'kalininsky': 'Калининский район',
    'kirovsky': 'Кировский район',
    'kolpinsky': 'Колпинский район',
    'krasnogvardeysky': 'Красногвардейский район',
    'krasnoselsky': 'Красносельский район',
    'kronshtadtsky': 'Кронштадтский район',
    'kurortny': 'Курортный район',
    'moskovsky': 'Московский район',
    'nevsky': 'Невский район',
    'petrogradsky': 'Петроградский район',
    'petrodvortsovy': 'Петродворцовый район',
    'primorsky': 'Приморский район',
    'pushkinsky': 'Пушкинский район',
    'frunzensky': 'Фрунзенский район',
    'tsentralny': 'Центральный район',
}
DISTRICT_IDS = tuple(DISTRICT_NAMES)

MAX_SAMPLE_ATTEMPTS = 10000


class DistrictDataError(RuntimeError):
    """Канонический dataset отсутствует или не прошёл валидацию."""


@dataclass(frozen=True)
class Territory:
    """Polygon/MultiPolygon в WGS84 с подготовленной геометрией для проверок."""

    geometry: Polygon | MultiPolygon = field(repr=False)
    prepared: Any = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, 'prepared', prep(self.geometry))

    def covers(self, latitude, longitude) -> bool:
        """Входит ли точка в территорию; граница считается допустимой."""
        point = _point(latitude, longitude)
        return bool(point is not None and self.prepared.covers(point))

    def sample(self) -> tuple[float, float]:
        """Случайная точка, равномерная по площади (rejection в общем bbox).

        Все части MultiPolygon, включая острова, получают долю по площади
        без отдельных весов. На масштабе СПб разница между площадью в
        градусах и проекционной площадью несущественна.
        """
        min_lon, min_lat, max_lon, max_lat = self.geometry.bounds
        for _ in range(MAX_SAMPLE_ATTEMPTS):
            latitude = round(random.uniform(min_lat, max_lat), 6)
            longitude = round(random.uniform(min_lon, max_lon), 6)
            if self.prepared.covers(Point(longitude, latitude)):
                return latitude, longitude
        raise DistrictDataError('Не удалось сгенерировать точку внутри территории')


@dataclass(frozen=True)
class District(Territory):
    id: str = ''
    name: str = ''


def _load_geojson(path: Path) -> dict:
    try:
        with path.open(encoding='utf-8') as source:
            payload = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise DistrictDataError(f'Не удалось загрузить {path}: {exc}') from exc
    if not isinstance(payload, dict) or payload.get('type') != 'FeatureCollection':
        raise DistrictDataError('spb_districts.geojson должен быть GeoJSON FeatureCollection')
    return payload


def _valid_geometry(geometry, label):
    if not isinstance(geometry, (Polygon, MultiPolygon)):
        raise DistrictDataError(f'{label}: ожидался Polygon/MultiPolygon')
    if geometry.is_empty or not geometry.is_valid or geometry.area <= 0:
        raise DistrictDataError(f'{label}: пустая или невалидная геометрия')
    return geometry


@cache
def load_districts() -> tuple[District, ...]:
    """Загрузить и проверить каноническую геометрию районов."""
    features = _load_geojson(DISTRICTS_GEOJSON_PATH).get('features')
    if not isinstance(features, list) or len(features) != len(DISTRICT_IDS):
        count = len(features) if isinstance(features, list) else 0
        raise DistrictDataError(f'Ожидалось 18 районов, получено {count}')

    by_id = {}
    for feature in features:
        if not isinstance(feature, dict) or feature.get('type') != 'Feature':
            raise DistrictDataError('Каждый район должен быть GeoJSON Feature')
        properties = feature.get('properties') or {}
        district_id = properties.get('id')
        if district_id not in DISTRICT_NAMES:
            raise DistrictDataError(f'Неизвестный district id: {district_id!r}')
        if district_id in by_id:
            raise DistrictDataError(f'Дублируется district id: {district_id}')

        name = properties.get('name')
        if name != DISTRICT_NAMES[district_id]:
            raise DistrictDataError(
                f'Неверное название {district_id}: {name!r}'
            )
        # Происхождение данных (data/README.md) — часть контракта dataset.
        relation_id = properties.get('osm_relation_id')
        if not isinstance(relation_id, int) or relation_id <= 0:
            raise DistrictDataError(f'Нет osm_relation_id для {district_id}')

        try:
            geometry = shape(feature.get('geometry'))
        except Exception as exc:
            raise DistrictDataError(f'Неверная геометрия {district_id}: {exc}') from exc
        _valid_geometry(geometry, district_id)

        min_lon, min_lat, max_lon, max_lat = geometry.bounds
        if not (20 <= min_lon < max_lon <= 40 and 50 <= min_lat < max_lat <= 70):
            raise DistrictDataError(f'{district_id}: неожиданные WGS84 bounds {geometry.bounds}')

        by_id[district_id] = District(geometry=geometry, id=district_id, name=name)

    missing = set(DISTRICT_IDS) - set(by_id)
    if missing:
        raise DistrictDataError(f'Нет районов: {", ".join(sorted(missing))}')
    return tuple(by_id[district_id] for district_id in DISTRICT_IDS)


@cache
def district_map() -> dict[str, District]:
    return {district.id: district for district in load_districts()}


@cache
def city_territory() -> Territory:
    """Точная территория СПб как union всех 18 районов.

    Union вычисляется лениво один раз на процесс; ``coverage_union_all``
    рассчитан на неперекрывающиеся районы и не попадает в горячий путь.
    """
    geometry = coverage_union_all([district.geometry for district in load_districts()])
    return Territory(geometry=_valid_geometry(geometry, 'Union районов'))


def is_valid_district_id(district_id) -> bool:
    return isinstance(district_id, str) and district_id in DISTRICT_NAMES


def district_name(district_id: str) -> str | None:
    return DISTRICT_NAMES.get(district_id)


def _point(latitude, longitude) -> Point | None:
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return Point(longitude, latitude)


def point_in_district(latitude, longitude, district_id: str) -> bool:
    """Входит ли точка в район; общая граница считается допустимой."""
    district = district_map().get(district_id)
    return bool(district is not None and district.covers(latitude, longitude))


def point_in_city(latitude, longitude) -> bool:
    """Входит ли точка в union административных районов Санкт-Петербурга."""
    return city_territory().covers(latitude, longitude)


def district_for_point(latitude, longitude) -> str | None:
    """Район точки; точка на общей границе относится к первому по DISTRICT_IDS."""
    point = _point(latitude, longitude)
    if point is None:
        return None
    for district in load_districts():
        if district.prepared.covers(point):
            return district.id
    return None


def generate_district_point(district_id: str):
    """Равномерный по площади кандидат внутри района."""
    district = district_map().get(district_id)
    if district is None:
        raise ValueError(f'Неизвестный district id: {district_id!r}')
    return district.sample()


def generate_city_point():
    """Равномерный по площади кандидат внутри территории Санкт-Петербурга."""
    return city_territory().sample()


def clear_district_caches():
    """Освободить prepared GEOS objects после one-shot операции.

    Обычно кэш живёт весь срок воркера. Alembic backfill очищает его,
    чтобы gunicorn --preload не форкал уже созданные GEOS handles.
    """
    city_territory.cache_clear()
    district_map.cache_clear()
    load_districts.cache_clear()
