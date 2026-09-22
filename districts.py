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
from functools import lru_cache
from pathlib import Path
from typing import Any

from shapely import coverage_union_all
from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.prepared import prep


DISTRICTS_GEOJSON_PATH = Path(__file__).resolve().parent / 'data' / 'spb_districts.geojson'

# Порядок стабилен: он также разрешает теоретическую ничью для
# точки, лежащей ровно на общей границе двух районов.
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


class DistrictDataError(RuntimeError):
    """Канонический dataset отсутствует или не прошёл валидацию."""


@dataclass(frozen=True)
class District:
    id: str
    name: str
    osm_relation_id: int
    geometry: Polygon | MultiPolygon = field(repr=False)
    prepared: Any = field(repr=False)
    components: tuple[Polygon, ...] = field(repr=False)
    component_areas: tuple[float, ...] = field(repr=False)

    @property
    def bounds(self):
        return self.geometry.bounds


@dataclass(frozen=True)
class CityTerritory:
    """Производная граница города, собранная из canonical районов."""

    geometry: Polygon | MultiPolygon = field(repr=False)
    prepared: Any = field(repr=False)
    components: tuple[Polygon, ...] = field(repr=False)
    component_areas: tuple[float, ...] = field(repr=False)

    @property
    def bounds(self):
        return self.geometry.bounds


def _load_geojson(path: Path) -> dict:
    try:
        with path.open(encoding='utf-8') as source:
            payload = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise DistrictDataError(f'Не удалось загрузить {path}: {exc}') from exc
    if not isinstance(payload, dict) or payload.get('type') != 'FeatureCollection':
        raise DistrictDataError('spb_districts.geojson должен быть GeoJSON FeatureCollection')
    return payload


@lru_cache(maxsize=4)
def load_districts(path: str | Path = DISTRICTS_GEOJSON_PATH) -> tuple[District, ...]:
    """Загрузить и проверить каноническую геометрию районов."""
    path = Path(path)
    features = _load_geojson(path).get('features')
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
        relation_id = properties.get('osm_relation_id')
        if not isinstance(relation_id, int) or relation_id <= 0:
            raise DistrictDataError(f'Нет osm_relation_id для {district_id}')

        try:
            geometry = shape(feature.get('geometry'))
        except Exception as exc:
            raise DistrictDataError(f'Неверная геометрия {district_id}: {exc}') from exc
        if not isinstance(geometry, (Polygon, MultiPolygon)):
            raise DistrictDataError(f'{district_id}: ожидался Polygon/MultiPolygon')
        if geometry.is_empty or not geometry.is_valid or geometry.area <= 0:
            raise DistrictDataError(f'{district_id}: пустая или невалидная геометрия')

        min_lon, min_lat, max_lon, max_lat = geometry.bounds
        if not (20 <= min_lon < max_lon <= 40 and 50 <= min_lat < max_lat <= 70):
            raise DistrictDataError(f'{district_id}: неожиданные WGS84 bounds {geometry.bounds}')

        components = ((geometry,) if isinstance(geometry, Polygon)
                      else tuple(geometry.geoms))
        by_id[district_id] = District(
            id=district_id,
            name=name,
            osm_relation_id=relation_id,
            geometry=geometry,
            prepared=prep(geometry),
            components=components,
            component_areas=tuple(component.area for component in components),
        )

    missing = set(DISTRICT_IDS) - set(by_id)
    if missing:
        raise DistrictDataError(f'Нет районов: {", ".join(sorted(missing))}')
    return tuple(by_id[district_id] for district_id in DISTRICT_IDS)


@lru_cache(maxsize=4)
def district_map(path: str | Path = DISTRICTS_GEOJSON_PATH) -> dict[str, District]:
    return {district.id: district for district in load_districts(path)}


@lru_cache(maxsize=4)
def city_territory(path: str | Path = DISTRICTS_GEOJSON_PATH) -> CityTerritory:
    """Собрать точную территорию СПб как union всех 18 районов.

    Union вычисляется лениво один раз на процесс. ``coverage_union_all``
    использует проверенный неперекрывающийся coverage и не попадает в горячий
    путь каждого игрового запроса.
    """
    geometry = coverage_union_all([
        district.geometry for district in load_districts(path)
    ])
    if not isinstance(geometry, (Polygon, MultiPolygon)):
        raise DistrictDataError('Union районов не образует Polygon/MultiPolygon')
    if geometry.is_empty or not geometry.is_valid or geometry.area <= 0:
        raise DistrictDataError('Union районов пуст или невалиден')

    components = ((geometry,) if isinstance(geometry, Polygon)
                  else tuple(geometry.geoms))
    return CityTerritory(
        geometry=geometry,
        prepared=prep(geometry),
        components=components,
        component_areas=tuple(component.area for component in components),
    )


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
    point = _point(latitude, longitude)
    return bool(district is not None and point is not None and district.prepared.covers(point))


def point_in_city(latitude, longitude) -> bool:
    """Входит ли точка в union административных районов Санкт-Петербурга."""
    point = _point(latitude, longitude)
    return bool(point is not None and city_territory().prepared.covers(point))


@lru_cache(maxsize=32768)
def _district_for_rounded_point(latitude: float, longitude: float) -> str | None:
    point = Point(longitude, latitude)
    districts = load_districts()
    # Обычная interior-точка попадает ровно в один район.
    for district in districts:
        if district.geometry.contains(point):
            return district.id
    # Граница принадлежит closure обоих polygon; выбираем стабильно.
    for district in districts:
        if district.prepared.covers(point):
            return district.id
    return None


def district_for_point(latitude, longitude) -> str | None:
    point = _point(latitude, longitude)
    if point is None:
        return None
    # Стабильные координаты пула не требуют повторного PIP в этом воркере.
    return _district_for_rounded_point(round(point.y, 7), round(point.x, 7))


def generate_district_point(district_id: str, *, max_attempts: int = 10000):
    """Равномерный кандидат внутри Polygon/MultiPolygon в WGS84.

    Компонент MultiPolygon выбирается пропорционально площади, затем
    идёт rejection sampling в его bbox. На масштабе СПб разница между
    площадью в градусах и проекционной площадью несущественна.
    """
    district = district_map().get(district_id)
    if district is None:
        raise ValueError(f'Неизвестный district id: {district_id!r}')
    component = random.choices(
        district.components, weights=district.component_areas, k=1
    )[0]
    for _ in range(max_attempts):
        min_lon, min_lat, max_lon, max_lat = component.bounds
        latitude = round(random.uniform(min_lat, max_lat), 6)
        longitude = round(random.uniform(min_lon, max_lon), 6)
        point = Point(longitude, latitude)
        if component.covers(point) and district.prepared.covers(point):
            return latitude, longitude
    raise DistrictDataError(f'Не удалось сгенерировать точку в {district_id}')


def generate_city_point(*, max_attempts: int = 10000):
    """Равномерный по площади кандидат внутри территории Санкт-Петербурга.

    Disconnected-компоненты union (включая острова Кронштадта) выбираются
    пропорционально площади. Выбранный компонент не меняется после rejection,
    иначе узкие polygons получили бы заниженный вес.
    """
    territory = city_territory()
    component = random.choices(
        territory.components, weights=territory.component_areas, k=1
    )[0]
    min_lon, min_lat, max_lon, max_lat = component.bounds
    for _ in range(max_attempts):
        latitude = round(random.uniform(min_lat, max_lat), 6)
        longitude = round(random.uniform(min_lon, max_lon), 6)
        point = Point(longitude, latitude)
        if component.covers(point) and territory.prepared.covers(point):
            return latitude, longitude
    raise DistrictDataError('Не удалось сгенерировать точку в границах Санкт-Петербурга')


def clear_district_caches():
    """Освободить prepared GEOS objects после one-shot операции.

    Обычно кэш живёт весь срок воркера. Alembic backfill очищает его,
    чтобы Docker gunicorn --preload не форкал уже созданные GEOS handles.
    """
    _district_for_rounded_point.cache_clear()
    city_territory.cache_clear()
    district_map.cache_clear()
    load_districts.cache_clear()
