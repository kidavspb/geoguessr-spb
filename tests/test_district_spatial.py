import random

import pytest
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.prepared import prep

import districts
from districts import (
    DISTRICT_IDS,
    District,
    district_for_point,
    district_map,
    generate_district_point,
    point_in_district,
)


def test_public_spatial_helpers_are_boundary_inclusive_and_exclude_holes():
    district = district_map()['krasnoselsky']
    polygon_with_hole = next(
        polygon for polygon in district.components if polygon.interiors
    )

    outer_boundary = Point(polygon_with_hole.exterior.coords[0])
    hole_boundary = Point(polygon_with_hole.interiors[0].coords[0])
    hole_interior = Polygon(polygon_with_hole.interiors[0]).representative_point()

    assert point_in_district(outer_boundary.y, outer_boundary.x, district.id)
    assert point_in_district(hole_boundary.y, hole_boundary.x, district.id)
    assert not point_in_district(hole_interior.y, hole_interior.x, district.id)


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
                # Match the classifier cache key exactly; canonical vertices
                # have at most six decimal places, so this keeps them on the
                # shared boundary instead of testing a near-boundary float.
                yield Point(round(longitude, 7), round(latitude, 7))

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


def test_multipolygon_sampling_weights_components_by_polygon_area(monkeypatch):
    # Both components have area 1, but the triangle fills only half of its
    # bounding box. Re-selecting the component after every rejected proposal
    # would therefore bias accepted points 2:1 towards the square.
    square = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    triangle = Polygon([(3, 0), (5, 0), (3, 1)])
    geometry = MultiPolygon([square, triangle])
    synthetic = District(
        id='synthetic',
        name='Synthetic',
        osm_relation_id='0',
        geometry=geometry,
        prepared=prep(geometry),
        components=(square, triangle),
        component_areas=(square.area, triangle.area),
    )
    monkeypatch.setattr(districts, 'district_map', lambda: {'synthetic': synthetic})
    monkeypatch.setattr(districts, 'random', random.Random(20260901))

    component_hits = [0, 0]
    for _ in range(3000):
        latitude, longitude = generate_district_point('synthetic')
        point = Point(longitude, latitude)
        component_hits[0 if square.covers(point) else 1] += 1

    square_share = component_hits[0] / sum(component_hits)
    assert 0.46 <= square_share <= 0.54


def test_unknown_district_cannot_be_sampled():
    with pytest.raises(ValueError, match='district id'):
        generate_district_point('unknown-district')
