"""Canonical Polygon/MultiPolygon conversion, validation, union, and membership."""
from __future__ import annotations

from shapely.geometry import MultiPolygon, Point, Polygon, mapping, shape
from shapely.prepared import prep
from shapely.validation import explain_validity


MIN_COMPONENT_AREA_M2 = 1e-6


def polygon_from_points(points: list[dict]) -> Polygon:
    if len(points) < 3:
        raise ValueError("polygon needs at least 3 points")
    try:
        polygon = Polygon([(float(point["x"]), float(point["y"])) for point in points])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("polygon points must contain numeric x and y") from exc
    if polygon.is_empty or polygon.area < MIN_COMPONENT_AREA_M2:
        raise ValueError("polygon area is too small")
    if not polygon.is_valid:
        raise ValueError(f"invalid polygon: {explain_validity(polygon)}")
    return polygon


def from_geojson(geometry: dict):
    if not isinstance(geometry, dict) or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        raise ValueError("geometry must be a GeoJSON Polygon or MultiPolygon")
    candidate = shape(geometry)
    if candidate.is_empty or not candidate.is_valid:
        raise ValueError(f"invalid canonical geometry: {explain_validity(candidate)}")
    return candidate


def normalize(candidate) -> Polygon | MultiPolygon:
    polygons = ([candidate] if isinstance(candidate, Polygon)
                else [part for part in candidate.geoms if isinstance(part, Polygon)])
    polygons = [part for part in polygons if part.area >= MIN_COMPONENT_AREA_M2]
    if not polygons:
        raise ValueError("canonical zone geometry has no usable polygon components")
    polygons.sort(key=lambda part: (round(part.bounds[0], 9), round(part.bounds[1], 9), -part.area))
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)


def union(existing: dict, contribution_points: list[dict]):
    current = from_geojson(existing)
    contribution = polygon_from_points(contribution_points)
    result = current.union(contribution)
    if result.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError(f"zone union produced unsupported {result.geom_type} geometry")
    return normalize(result)


def as_geojson(candidate) -> dict:
    result = mapping(normalize(candidate))
    # Shapely returns tuples; JSON-facing code and tests use plain lists.
    def lists(value):
        return [lists(item) for item in value] if isinstance(value, (tuple, list)) else value
    return {"type": result["type"], "coordinates": lists(result["coordinates"])}


def legacy_exterior(geometry: dict) -> list[dict]:
    candidate = from_geojson(geometry)
    polygon = candidate if isinstance(candidate, Polygon) else max(candidate.geoms, key=lambda part: part.area)
    coordinates = list(polygon.exterior.coords)
    if coordinates and coordinates[0] == coordinates[-1]:
        coordinates.pop()
    return [{"x": float(x), "y": float(y)} for x, y in coordinates]


def contains(geometry: dict, x: float, y: float) -> bool:
    # ``covers`` includes a point exactly on the physical zone boundary.
    return from_geojson(geometry).covers(Point(float(x), float(y)))


class PreparedZone:
    """One zone's containment test, built once instead of per detection.

    ``contains(geometry, x, y)`` above re-parses the GeoJSON, revalidates it, and
    constructs a fresh Shapely geometry on every call — at 240 frames/second with
    several detections each that dominated enrichment.  This holds the same
    validated geometry with a Shapely prepared predicate, and answers the same
    question with the same boundary semantics: ``covers`` includes a point exactly
    on the physical boundary.  The bounding-box pre-check is an exact short circuit
    (a point outside the envelope can never be covered), not an approximation.
    """

    __slots__ = ("geometry", "_prepared", "_min_x", "_min_y", "_max_x", "_max_y")

    def __init__(self, geometry: dict) -> None:
        shapely_geometry = from_geojson(geometry)
        self.geometry = geometry
        self._prepared = prep(shapely_geometry)
        self._min_x, self._min_y, self._max_x, self._max_y = shapely_geometry.bounds

    def covers(self, x: float, y: float) -> bool:
        x = float(x)
        y = float(y)
        if x < self._min_x or x > self._max_x or y < self._min_y or y > self._max_y:
            return False
        return self._prepared.covers(Point(x, y))


def prepare(geometry: dict) -> PreparedZone:
    return PreparedZone(geometry)


def component_count(geometry: dict) -> int:
    candidate = from_geojson(geometry)
    return 1 if isinstance(candidate, Polygon) else len(candidate.geoms)
