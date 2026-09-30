#!/usr/bin/env python3
"""Download, validate and normalize Saint Petersburg district boundaries.

The checked-in ``data/spb_districts.geojson`` is the application source of
truth.  This script is a maintenance tool, not a runtime dependency: it makes
one Nominatim lookup for the 18 explicitly allow-listed OSM relations, applies
coverage-aware simplification, validates the result and writes deterministic
GeoJSON.

Shapely 2.1 or newer is required for ``coverage_simplify``::

    python -m pip install "Shapely==2.1.1"
    python tools/update_spb_districts.py

For an offline/reviewable update, save the raw Nominatim FeatureCollection and
pass it with ``--input``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import shapely
    from shapely.geometry import MultiPolygon, Point, mapping, shape
    from shapely.geometry.polygon import orient
except ImportError:  # pragma: no cover - depends on the maintainer setup
    sys.exit(
        "update_spb_districts: нужен Shapely >= 2.1 "
        "(python -m pip install 'Shapely==2.1.1')"
    )

if not hasattr(shapely, "coverage_simplify"):  # pragma: no cover - setup guard
    sys.exit("update_spb_districts: нужен Shapely >= 2.1 с coverage_simplify")


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "spb_districts.geojson"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/lookup"
USER_AGENT = (
    "geoguessr-spb-district-updater/1.0 "
    "(+https://github.com/kidavspb/geoguessr-spb)"
)

# Tolerance is in RFC 7946 longitude/latitude degrees.  At Saint Petersburg's
# latitude 0.00002 degrees is at most about 2.2 m.  GEOS simplifies the whole
# coverage together, retaining shared edges and avoiding district gaps/overlap.
DEFAULT_TOLERANCE = 0.00002
DEFAULT_PRECISION = 6

DISTRICTS = (
    ("admiralteysky", "Адмиралтейский район", 1114193),
    ("vasileostrovsky", "Василеостровский район", 1114252),
    ("vyborgsky", "Выборгский район", 1114354),
    ("kalininsky", "Калининский район", 1114806),
    ("kirovsky", "Кировский район", 1114809),
    ("kolpinsky", "Колпинский район", 337424),
    ("krasnogvardeysky", "Красногвардейский район", 1114895),
    ("krasnoselsky", "Красносельский район", 363103),
    ("kronshtadtsky", "Кронштадтский район", 1115082),
    ("kurortny", "Курортный район", 1115366),
    ("moskovsky", "Московский район", 338636),
    ("nevsky", "Невский район", 368287),
    ("petrogradsky", "Петроградский район", 1114905),
    ("petrodvortsovy", "Петродворцовый район", 367375),
    ("primorsky", "Приморский район", 1115367),
    ("pushkinsky", "Пушкинский район", 338635),
    ("frunzensky", "Фрунзенский район", 369514),
    ("tsentralny", "Центральный район", 1114902),
)

# Safely interior landmark coordinates (latitude, longitude).  They guard
# against a swapped axis, wrong administrative level or badly assembled
# multipolygon during a future refresh.
CONTROL_POINTS = (
    ("Исаакиевский собор", 59.9343, 30.3061, "admiralteysky"),
    ("Стрелка Васильевского острова", 59.9443, 30.3065, "vasileostrovsky"),
    ("центр Колпино", 59.7480, 30.5880, "kolpinsky"),
    ("центр Кронштадта", 59.9911, 29.7770, "kronshtadtsky"),
    ("центр Сестрорецка", 60.0980, 29.9638, "kurortny"),
    ("Петропавловская крепость", 59.9500, 30.3167, "petrogradsky"),
    ("Большой Петергофский дворец", 59.8845, 29.9084, "petrodvortsovy"),
    ("Екатерининский дворец", 59.7154, 30.3957, "pushkinsky"),
    ("Эрмитаж", 59.9398, 30.3146, "tsentralny"),
)


def source_url() -> str:
    params = {
        "osm_ids": ",".join(f"R{relation_id}" for _, _, relation_id in DISTRICTS),
        "format": "geojson",
        "polygon_geojson": "1",
        # Zero asks Nominatim for the unsimplified indexed OSM geometry.  The
        # deterministic, topology-aware simplification happens locally.
        "polygon_threshold": "0",
    }
    return f"{NOMINATIM_URL}?{urlencode(params)}"


def fetch_source() -> dict[str, Any]:
    request = Request(
        source_url(),
        headers={
            "Accept": "application/geo+json, application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen(request, timeout=120) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Не удалось получить OSM geometry через Nominatim: {exc}") from exc


def load_source(path: Path | None) -> dict[str, Any]:
    if path is None:
        return fetch_source()
    try:
        with path.open(encoding="utf-8") as source_file:
            return json.load(source_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Не удалось прочитать {path}: {exc}") from exc


def polygon_count(geometry: Any) -> int:
    return len(geometry.geoms) if geometry.geom_type == "MultiPolygon" else 1


def hole_count(geometry: Any) -> int:
    polygons = geometry.geoms if geometry.geom_type == "MultiPolygon" else (geometry,)
    return sum(len(polygon.interiors) for polygon in polygons)


def orient_geometry(geometry: Any) -> Any:
    """Use RFC 7946's recommended counter-clockwise exterior ring order."""
    if geometry.geom_type == "Polygon":
        return orient(geometry, sign=1.0)
    return MultiPolygon([orient(polygon, sign=1.0) for polygon in geometry.geoms])


def round_coordinates(value: Any, precision: int) -> Any:
    if isinstance(value, (list, tuple)):
        return [round_coordinates(item, precision) for item in value]
    return round(float(value), precision)


def validate_geometries(geometries: list[Any], stage: str) -> None:
    if len(geometries) != len(DISTRICTS):
        raise SystemExit(f"{stage}: ожидалось 18 geometries, получено {len(geometries)}")

    for (district_id, _, _), geometry in zip(DISTRICTS, geometries, strict=True):
        if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise SystemExit(f"{stage}: {district_id} имеет тип {geometry.geom_type}")
        if geometry.is_empty or not geometry.is_valid:
            raise SystemExit(f"{stage}: невалидная geometry района {district_id}")

    if not bool(shapely.coverage_is_valid(geometries)):
        raise SystemExit(f"{stage}: районы не образуют topologically valid coverage")

    # `coverage_is_valid` checks matching edges/overlaps; keep this explicit
    # area check as a readable invariant for future Shapely upgrades.
    for index, first in enumerate(geometries):
        for second in geometries[index + 1 :]:
            if first.intersection(second).area > 1e-14:
                raise SystemExit(f"{stage}: районные polygons перекрываются по площади")


def validate_control_points(geometries_by_id: dict[str, Any]) -> None:
    for label, latitude, longitude, expected_id in CONTROL_POINTS:
        point = Point(longitude, latitude)
        matches = [
            district_id
            for district_id, geometry in geometries_by_id.items()
            if geometry.covers(point)
        ]
        if matches != [expected_id]:
            raise SystemExit(
                f"Контрольная точка «{label}» ожидалась в {expected_id}, получено {matches}"
            )


def normalize(
    source: dict[str, Any], tolerance: float, precision: int
) -> tuple[dict[str, Any], list[Any], list[Any]]:
    if source.get("type") != "FeatureCollection" or not isinstance(
        source.get("features"), list
    ):
        raise SystemExit("Источник должен быть GeoJSON FeatureCollection")

    source_by_relation: dict[int, dict[str, Any]] = {}
    for feature in source["features"]:
        properties = feature.get("properties") or {}
        if properties.get("osm_type") != "relation":
            continue
        relation_id = properties.get("osm_id")
        if isinstance(relation_id, int):
            if relation_id in source_by_relation:
                raise SystemExit(f"Источник содержит duplicate relation {relation_id}")
            source_by_relation[relation_id] = feature

    expected_relations = {relation_id for _, _, relation_id in DISTRICTS}
    actual_relations = set(source_by_relation)
    if actual_relations != expected_relations:
        missing = sorted(expected_relations - actual_relations)
        extra = sorted(actual_relations - expected_relations)
        raise SystemExit(f"Неверный набор OSM relations: missing={missing}, extra={extra}")

    raw_geometries: list[Any] = []
    for district_id, expected_name, relation_id in DISTRICTS:
        feature = source_by_relation[relation_id]
        source_name = (feature.get("properties") or {}).get("name")
        if source_name != expected_name:
            raise SystemExit(
                f"Relation {relation_id} ({district_id}): ожидалось имя "
                f"{expected_name!r}, получено {source_name!r}"
            )
        try:
            raw_geometries.append(shape(feature["geometry"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"Relation {relation_id}: повреждённая GeoJSON geometry") from exc

    validate_geometries(raw_geometries, "source")
    simplified = list(
        shapely.coverage_simplify(raw_geometries, tolerance, simplify_boundary=True)
    )
    simplified = [orient_geometry(geometry) for geometry in simplified]
    validate_geometries(simplified, "simplified")

    features: list[dict[str, Any]] = []
    for (district_id, name, relation_id), geometry in zip(
        DISTRICTS, simplified, strict=True
    ):
        geometry_mapping = mapping(geometry)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": district_id,
                    "name": name,
                    "osm_relation_id": relation_id,
                },
                "geometry": {
                    "type": geometry_mapping["type"],
                    "coordinates": round_coordinates(
                        geometry_mapping["coordinates"], precision
                    ),
                },
            }
        )

    result = {"type": "FeatureCollection", "features": features}

    # Rounding can theoretically collapse a tiny segment or disturb a shared
    # edge, so validate the exact geometries that will be written, not only the
    # pre-rounded Shapely result.
    final_geometries = [shape(feature["geometry"]) for feature in features]
    validate_geometries(final_geometries, "rounded output")
    validate_control_points(
        {
            district_id: geometry
            for (district_id, _, _), geometry in zip(
                DISTRICTS, final_geometries, strict=True
            )
        }
    )
    return result, raw_geometries, final_geometries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        help="raw Nominatim GeoJSON вместо сетевого запроса",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument("--precision", type=int, default=DEFAULT_PRECISION)
    args = parser.parse_args()
    if args.tolerance < 0:
        parser.error("--tolerance не может быть отрицательным")
    if not 5 <= args.precision <= 10:
        parser.error("--precision должен быть от 5 до 10")
    return args


def main() -> None:
    args = parse_args()
    source = load_source(args.input)
    result, raw_geometries, final_geometries = normalize(
        source, args.tolerance, args.precision
    )
    serialized = json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding="utf-8")

    raw_vertices = sum(int(shapely.get_num_coordinates(g)) for g in raw_geometries)
    final_vertices = sum(int(shapely.get_num_coordinates(g)) for g in final_geometries)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    print(f"Записано: {args.output}")
    print(f"Районов: {len(final_geometries)}")
    print(f"Вершин: {raw_vertices} -> {final_vertices}")
    print(f"Размер: {len(serialized.encode('utf-8'))} bytes")
    print(f"SHA-256: {digest}")
    for (district_id, _, _), geometry in zip(DISTRICTS, final_geometries, strict=True):
        print(
            f"  {district_id:20} {geometry.geom_type:12} "
            f"parts={polygon_count(geometry):2} holes={hole_count(geometry):2} "
            f"vertices={int(shapely.get_num_coordinates(geometry)):4}"
        )


if __name__ == "__main__":
    main()
