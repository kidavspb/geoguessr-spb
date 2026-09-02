import json
from pathlib import Path

from shapely import coverage_is_valid
from shapely.geometry import Point, shape


DATA_PATH = Path(__file__).resolve().parents[1] / 'data' / 'spb_districts.geojson'

EXPECTED_DISTRICTS = {
    'admiralteysky': ('Адмиралтейский район', 1114193),
    'vasileostrovsky': ('Василеостровский район', 1114252),
    'vyborgsky': ('Выборгский район', 1114354),
    'kalininsky': ('Калининский район', 1114806),
    'kirovsky': ('Кировский район', 1114809),
    'kolpinsky': ('Колпинский район', 337424),
    'krasnogvardeysky': ('Красногвардейский район', 1114895),
    'krasnoselsky': ('Красносельский район', 363103),
    'kronshtadtsky': ('Кронштадтский район', 1115082),
    'kurortny': ('Курортный район', 1115366),
    'moskovsky': ('Московский район', 338636),
    'nevsky': ('Невский район', 368287),
    'petrogradsky': ('Петроградский район', 1114905),
    'petrodvortsovy': ('Петродворцовый район', 367375),
    'primorsky': ('Приморский район', 1115367),
    'pushkinsky': ('Пушкинский район', 338635),
    'frunzensky': ('Фрунзенский район', 369514),
    'tsentralny': ('Центральный район', 1114902),
}

CONTROL_POINTS = {
    # landmark: (latitude, longitude, district id)
    'Исаакиевский собор': (59.9343, 30.3061, 'admiralteysky'),
    'Стрелка Васильевского острова': (59.9443, 30.3065, 'vasileostrovsky'),
    'центр Колпино': (59.7480, 30.5880, 'kolpinsky'),
    'центр Кронштадта': (59.9911, 29.7770, 'kronshtadtsky'),
    'центр Сестрорецка': (60.0980, 29.9638, 'kurortny'),
    'Петропавловская крепость': (59.9500, 30.3167, 'petrogradsky'),
    'Большой Петергофский дворец': (59.8845, 29.9084, 'petrodvortsovy'),
    'Екатерининский дворец': (59.7154, 30.3957, 'pushkinsky'),
    'Эрмитаж': (59.9398, 30.3146, 'tsentralny'),
}


def load_features():
    document = json.loads(DATA_PATH.read_text(encoding='utf-8'))
    assert document['type'] == 'FeatureCollection'
    assert set(document) == {'type', 'features'}
    return document['features']


def test_district_dataset_has_exact_stable_catalog():
    features = load_features()

    assert len(features) == 18
    actual = {}
    relation_ids = []
    for feature in features:
        assert feature['type'] == 'Feature'
        assert set(feature) == {'type', 'properties', 'geometry'}
        assert set(feature['properties']) == {'id', 'name', 'osm_relation_id'}
        district_id = feature['properties']['id']
        assert district_id not in actual
        actual[district_id] = (
            feature['properties']['name'],
            feature['properties']['osm_relation_id'],
        )
        relation_ids.append(feature['properties']['osm_relation_id'])

    assert actual == EXPECTED_DISTRICTS
    assert len(relation_ids) == len(set(relation_ids))


def test_district_geometries_are_valid_non_overlapping_coverage():
    geometries = [shape(feature['geometry']) for feature in load_features()]

    assert all(not geometry.is_empty for geometry in geometries)
    assert all(geometry.is_valid for geometry in geometries)
    assert all(geometry.geom_type in {'Polygon', 'MultiPolygon'} for geometry in geometries)
    assert bool(coverage_is_valid(geometries))

    for index, first in enumerate(geometries):
        for second in geometries[index + 1:]:
            assert first.intersection(second).area <= 1e-14


def test_district_multipolygons_keep_islands_and_hole():
    geometries = {
        feature['properties']['id']: shape(feature['geometry'])
        for feature in load_features()
    }

    assert geometries['krasnoselsky'].geom_type == 'MultiPolygon'
    assert len(geometries['krasnoselsky'].geoms) == 2
    assert sum(len(polygon.interiors) for polygon in geometries['krasnoselsky'].geoms) == 1
    assert geometries['kronshtadtsky'].geom_type == 'MultiPolygon'
    assert len(geometries['kronshtadtsky'].geoms) == 15
    assert geometries['petrodvortsovy'].geom_type == 'MultiPolygon'
    assert len(geometries['petrodvortsovy'].geoms) == 2


def test_known_landmarks_resolve_to_expected_districts():
    geometries = {
        feature['properties']['id']: shape(feature['geometry'])
        for feature in load_features()
    }

    for label, (latitude, longitude, expected_id) in CONTROL_POINTS.items():
        point = Point(longitude, latitude)
        matches = [
            district_id
            for district_id, geometry in geometries.items()
            if geometry.covers(point)
        ]
        assert matches == [expected_id], label


def test_canonical_geojson_payload_stays_bounded():
    # Guards against accidentally checking in raw Overpass/Nominatim metadata
    # or an excessively detailed, multi-megabyte geometry response.
    assert 100_000 < DATA_PATH.stat().st_size < 400_000
