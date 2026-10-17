import random

import pytest
from shapely import coverage_union_all
from shapely.geometry import MultiPolygon, Point, Polygon

import districts
from districts import (
    DISTRICT_IDS,
    District,
    Territory,
    city_territory,
    district_for_point,
    district_map,
    generate_city_point,
    generate_district_point,
    point_in_city,
    point_in_district,
)


def _parts(geometry):
    return tuple(geometry.geoms) if hasattr(geometry, 'geoms') else (geometry,)


def test_public_spatial_helpers_are_boundary_inclusive_and_exclude_holes():
    district = district_map()['krasnoselsky']
    polygon_with_hole = next(
        polygon for polygon in _parts(district.geometry) if polygon.interiors
    )

    outer_boundary = Point(polygon_with_hole.exterior.coords[0])
    hole_boundary = Point(polygon_with_hole.interiors[0].coords[0])
    hole_interior = Polygon(polygon_with_hole.interiors[0]).representative_point()

    assert point_in_district(outer_boundary.y, outer_boundary.x, district.id)
    assert point_in_district(hole_boundary.y, hole_boundary.x, district.id)
    assert not point_in_district(hole_interior.y, hole_interior.x, district.id)
    assert point_in_city(outer_boundary.y, outer_boundary.x)
    assert point_in_city(hole_boundary.y, hole_boundary.x)
    assert not point_in_city(hole_interior.y, hole_interior.x)


def test_city_territory_is_cached_union_with_disconnected_islands_and_hole():
    territory = city_territory()
    expected = coverage_union_all([
        district.geometry for district in district_map().values()
    ])

    assert territory is city_territory()
    assert territory.geometry.equals(expected)
    assert territory.geometry.geom_type == 'MultiPolygon'
    components = _parts(territory.geometry)
    assert len(components) == 17
    assert sum(len(component.interiors) for component in components) == 1

    # Даже самые маленькие disconnected-компоненты derived union не теряются.
    for component in components:
        representative = component.representative_point()
        assert point_in_city(representative.y, representative.x)


@pytest.mark.parametrize(('latitude', 'longitude', 'district_id'), (
    (59.9398, 30.3146, 'tsentralny'),       # центр
    (60.0980, 29.9638, 'kurortny'),         # север / Сестрорецк
    (59.9911, 29.7770, 'kronshtadtsky'),    # Кронштадт
    (59.8845, 29.9084, 'petrodvortsovy'),   # Петергоф
    (59.7154, 30.3957, 'pushkinsky'),       # Пушкин
    (59.7480, 30.5880, 'kolpinsky'),        # Колпино
))
def test_remote_city_control_points_are_inside_exact_union(
        latitude, longitude, district_id):
    assert district_for_point(latitude, longitude) == district_id
    assert point_in_city(latitude, longitude)


def test_classifier_has_stable_semantics_for_boundary_and_unassigned_points():
    loaded = district_map()
    shared_point = None
    matches = None

    def coordinate_points(geometry):
        if hasattr(geometry, 'geoms'):
            for part in geometry.geoms:
                yield from coordinate_points(part)
        elif hasattr(geometry, 'coords'):
            for longitude, latitude in geometry.coords:
                yield Point(longitude, latitude)

    for index, first_id in enumerate(DISTRICT_IDS):
        for second_id in DISTRICT_IDS[index + 1:]:
            shared = loaded[first_id].geometry.boundary.intersection(
                loaded[second_id].geometry.boundary
            )
            if not shared.is_empty:
                for candidate in coordinate_points(shared):
                    candidate_matches = [
                        district_id for district_id in DISTRICT_IDS
                        if loaded[district_id].geometry.covers(candidate)
                    ]
                    if len(candidate_matches) >= 2:
                        shared_point = candidate
                        matches = candidate_matches
                        break
            if shared_point is not None:
                break
        if shared_point is not None:
            break

    assert shared_point is not None
    assert district_for_point(shared_point.y, shared_point.x) == matches[0]
    assert district_for_point(0, 0) is None
    assert district_for_point(float('nan'), 30.3) is None
    assert not point_in_district(59.9, 30.3, 'unknown-district')


@pytest.mark.parametrize('district_id', DISTRICT_IDS)
def test_generated_points_stay_in_requested_district_after_rounding(district_id):
    for _ in range(20):
        latitude, longitude = generate_district_point(district_id)

        assert latitude == round(latitude, 6)
        assert longitude == round(longitude, 6)
        assert point_in_district(latitude, longitude, district_id)


def test_generated_city_points_stay_inside_exact_union_after_rounding():
    for _ in range(100):
        latitude, longitude = generate_city_point()

        assert latitude == round(latitude, 6)
        assert longitude == round(longitude, 6)
        assert point_in_city(latitude, longitude)


def test_multipolygon_sampling_weights_components_by_polygon_area(monkeypatch):
    # Both components have area 1, but the triangle fills only half of its
    # bounding box. Re-selecting the component after every rejected proposal
    # would therefore bias accepted points 2:1 towards the square.
    square = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    triangle = Polygon([(3, 0), (5, 0), (3, 1)])
    geometry = MultiPolygon([square, triangle])
    synthetic = District(geometry=geometry, id='synthetic', name='Synthetic')
    monkeypatch.setattr(districts, 'district_map', lambda: {'synthetic': synthetic})
    monkeypatch.setattr(districts, 'random', random.Random(20260901))

    component_hits = [0, 0]
    for _ in range(3000):
        latitude, longitude = generate_district_point('synthetic')
        point = Point(longitude, latitude)
        component_hits[0 if square.covers(point) else 1] += 1

    square_share = component_hits[0] / sum(component_hits)
    assert 0.46 <= square_share <= 0.54


def test_city_sampling_weights_components_by_polygon_area(monkeypatch):
    square = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    triangle = Polygon([(3, 0), (5, 0), (3, 1)])
    geometry = MultiPolygon([square, triangle])
    synthetic = Territory(geometry=geometry)
    monkeypatch.setattr(districts, 'city_territory', lambda: synthetic)
    monkeypatch.setattr(districts, 'random', random.Random(20260903))

    component_hits = [0, 0]
    for _ in range(3000):
        latitude, longitude = generate_city_point()
        point = Point(longitude, latitude)
        component_hits[0 if square.covers(point) else 1] += 1

    square_share = component_hits[0] / sum(component_hits)
    assert 0.46 <= square_share <= 0.54


def test_unknown_district_cannot_be_sampled():
    with pytest.raises(ValueError, match='district id'):
        generate_district_point('unknown-district')


def test_every_shared_vertex_belongs_to_the_first_district_in_order():
    """Точка на общей границе стабильно относится к первому району списка."""
    loaded = district_map()
    checked = 0
    for district in loaded.values():
        for part in _parts(district.geometry):
            for longitude, latitude in part.exterior.coords:
                point = Point(longitude, latitude)
                owners = [d for d in DISTRICT_IDS if loaded[d].geometry.covers(point)]
                if len(owners) > 1:
                    assert district_for_point(latitude, longitude) == owners[0]
                    checked += 1
    assert checked > 1000
