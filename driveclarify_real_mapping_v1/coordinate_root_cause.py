"""Pure-Python OpenDRIVE/GNSS reconstruction and coordinate root-cause logic."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Sequence


EARTH_RADIUS_EQUATOR_METRES = 6_378_137.0


class CoordinateRootCauseVerdict(str, Enum):
    ROOT_CAUSE_VALID_INVARIANT_CONVERSION_BUG = "ROOT_CAUSE_VALID_INVARIANT_CONVERSION_BUG"
    ROOT_CAUSE_INVALID_INVARIANT_WRONG_OBJECT_PAIR = "ROOT_CAUSE_INVALID_INVARIANT_WRONG_OBJECT_PAIR"
    ROOT_CAUSE_REFERENCE_POINT_OFFSET_MISSING = "ROOT_CAUSE_REFERENCE_POINT_OFFSET_MISSING"
    ROOT_CAUSE_TEMPORAL_FRAME_MISMATCH = "ROOT_CAUSE_TEMPORAL_FRAME_MISMATCH"
    ROOT_CAUSE_INSUFFICIENT_RECORDED_EVIDENCE = "ROOT_CAUSE_INSUFFICIENT_RECORDED_EVIDENCE"
    ROOT_CAUSE_MULTIPLE_WITH_PRIMARY_CLASSIFICATION = "ROOT_CAUSE_MULTIPLE_WITH_PRIMARY_CLASSIFICATION"


@dataclass(frozen=True)
class GeoReference:
    latitude_reference_degrees: float
    longitude_reference_degrees: float
    projection: str
    source_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_opendrive_georeference(opendrive_text: str) -> GeoReference:
    """Parse the exact +lat_0/+lon_0 used by CARLA's route conversion."""

    root = ET.fromstring(opendrive_text)
    node = root.find("./header/geoReference")
    text = "" if node is None or node.text is None else node.text.strip()
    latitude = re.search(r"(?:^|\s)\+lat_0=([^\s]+)", text)
    longitude = re.search(r"(?:^|\s)\+lon_0=([^\s]+)", text)
    projection = re.search(r"(?:^|\s)\+proj=([^\s]+)", text)
    if latitude is None or longitude is None:
        raise ValueError("OPENDRIVE_GEOREFERENCE_LAT_LON_MISSING")
    return GeoReference(
        float(latitude.group(1)),
        float(longitude.group(1)),
        "UNKNOWN" if projection is None else projection.group(1),
        text,
    )


def gps_to_carla(
    gps_lat_lon_alt: Sequence[float],
    georeference: GeoReference,
) -> tuple[float, float, float]:
    """Leaderboard Mercator inverse: east/longitude -> +x, north/latitude -> -y."""

    if len(gps_lat_lon_alt) != 3:
        raise ValueError("GNSS_REQUIRES_LAT_LON_ALT")
    lat, lon, alt = (float(item) for item in gps_lat_lon_alt)
    lat_ref = georeference.latitude_reference_degrees
    lon_ref = georeference.longitude_reference_degrees
    scale = math.cos(math.radians(lat_ref))
    if abs(scale) < 1e-12:
        raise ValueError("GNSS_REFERENCE_SCALE_DEGENERATE")
    my = math.log(math.tan(math.radians(lat + 90.0) / 2.0)) * EARTH_RADIUS_EQUATOR_METRES * scale
    mx = lon * math.pi * EARTH_RADIUS_EQUATOR_METRES * scale / 180.0
    reference_my = (
        scale
        * EARTH_RADIUS_EQUATOR_METRES
        * math.log(math.tan(math.radians(90.0 + lat_ref) / 2.0))
    )
    reference_mx = scale * lon_ref * math.pi * EARTH_RADIUS_EQUATOR_METRES / 180.0
    return mx - reference_mx, reference_my - my, alt


def carla_to_gps(
    location_xyz: Sequence[float],
    georeference: GeoReference,
) -> tuple[float, float, float]:
    if len(location_xyz) != 3:
        raise ValueError("CARLA_LOCATION_REQUIRES_XYZ")
    x, y, z = (float(item) for item in location_xyz)
    lat_ref = georeference.latitude_reference_degrees
    lon_ref = georeference.longitude_reference_degrees
    scale = math.cos(math.radians(lat_ref))
    mx = scale * lon_ref * math.pi * EARTH_RADIUS_EQUATOR_METRES / 180.0 + x
    my = (
        scale
        * EARTH_RADIUS_EQUATOR_METRES
        * math.log(math.tan(math.radians(90.0 + lat_ref) / 2.0))
        - y
    )
    lon = mx * 180.0 / (math.pi * EARTH_RADIUS_EQUATOR_METRES * scale)
    lat = 360.0 * math.atan(math.exp(my / (EARTH_RADIUS_EQUATOR_METRES * scale))) / math.pi - 90.0
    return lat, lon, z


def compensate_sensor_extrinsic(
    sensor_world_xyz: Sequence[float],
    sensor_extrinsic_vehicle_xyz: Sequence[float],
    vehicle_yaw_degrees: float,
) -> tuple[float, float, float]:
    """Recover the vehicle actor origin from the GNSS attachment point."""

    if len(sensor_world_xyz) != 3 or len(sensor_extrinsic_vehicle_xyz) != 3:
        raise ValueError("SENSOR_EXTRINSIC_REQUIRES_XYZ")
    sx, sy, sz = (float(item) for item in sensor_world_xyz)
    ex, ey, ez = (float(item) for item in sensor_extrinsic_vehicle_xyz)
    yaw = math.radians(float(vehicle_yaw_degrees))
    c, s = math.cos(yaw), math.sin(yaw)
    return sx - (c * ex - s * ey), sy - (s * ex + c * ey), sz - ez


def reconstruct_simlingo_reference_bug(
    first_route_world_xyz: Sequence[float],
    first_route_gps: Sequence[float],
) -> dict[str, Any]:
    """Reconstruct the Town03 fixed point selected by SimLingo's current equations.

    With Town03 lat_0=0 and fsolve initial guess [0, 0], equation 2 has the exact
    lat_ref=0 root.  The erroneous ``locx * lat_ref`` term then vanishes, so equation
    1 sets lon_ref to the first route GPS longitude instead of map lon_0.
    """

    if len(first_route_world_xyz) != 3 or len(first_route_gps) != 3:
        raise ValueError("REFERENCE_RECONSTRUCTION_REQUIRES_XYZ_AND_GNSS")
    locx = float(first_route_world_xyz[0])
    inferred = GeoReference(0.0, float(first_route_gps[1]), "simlingo_fsolve_bug", "SOURCE_RECONSTRUCTION")
    reconverted = gps_to_carla(first_route_gps, inferred)
    dx = reconverted[0] - locx
    dy = reconverted[1] - float(first_route_world_xyz[1])
    return {
        "inferred_lat_ref_degrees": inferred.latitude_reference_degrees,
        "inferred_lon_ref_degrees": inferred.longitude_reference_degrees,
        "first_route_gps_reconverted_xyz": list(reconverted),
        "bias_xy_metres": [dx, dy],
        "bias_distance_metres": math.hypot(dx, dy),
        "source_bug": "LONGITUDE_EQUATION_MULTIPLIES_WORLD_X_BY_LAT_REF",
    }


def planar_distance(left_xyz: Sequence[float], right_xyz: Sequence[float]) -> float:
    return math.hypot(float(left_xyz[0]) - float(right_xyz[0]), float(left_xyz[1]) - float(right_xyz[1]))


def classify_root_cause(
    *,
    same_object: bool,
    same_reference_point: bool,
    same_frame: bool,
    same_clock_frame: bool,
    conversion_fixture_failed: bool,
    recorded_operands_available: bool,
) -> CoordinateRootCauseVerdict:
    if not same_object:
        return CoordinateRootCauseVerdict.ROOT_CAUSE_INVALID_INVARIANT_WRONG_OBJECT_PAIR
    if not same_reference_point:
        return CoordinateRootCauseVerdict.ROOT_CAUSE_REFERENCE_POINT_OFFSET_MISSING
    if not same_frame or not same_clock_frame:
        return CoordinateRootCauseVerdict.ROOT_CAUSE_TEMPORAL_FRAME_MISMATCH
    if conversion_fixture_failed:
        return CoordinateRootCauseVerdict.ROOT_CAUSE_VALID_INVARIANT_CONVERSION_BUG
    if not recorded_operands_available:
        return CoordinateRootCauseVerdict.ROOT_CAUSE_INSUFFICIENT_RECORDED_EVIDENCE
    return CoordinateRootCauseVerdict.ROOT_CAUSE_INSUFFICIENT_RECORDED_EVIDENCE
