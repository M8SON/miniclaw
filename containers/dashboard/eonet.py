from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import requests


CATEGORY_BASE_SCORES = {
    "wildfires": 70,
    "severeStorms": 68,
    "volcanoes": 66,
    "floods": 64,
    "earthquakes": 63,
    "landslides": 55,
    "extremeTemperatures": 52,
    "dustHaze": 10,
}


def fetch_eonet_events(hazard_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    if not hazard_cfg.get("enabled", True):
        return []

    params = {
        "status": "open",
        "limit": hazard_cfg.get("fetch_limit", 20),
        "days": hazard_cfg.get("days", 14),
    }
    categories = hazard_cfg.get("categories") or []
    if categories:
        params["category"] = ",".join(categories)

    try:
        response = requests.get(
            "https://eonet.gsfc.nasa.gov/api/v3/events",
            params=params,
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json() if callable(getattr(response, "json", None)) else {}
        events = payload.get("events", [])
        return events if isinstance(events, list) else []
    except Exception:
        return []


def normalize_event(
    event: dict[str, Any],
    focus_location: dict[str, Any] | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    category = _first_or_default(event.get("categories"), {})
    source = _first_or_default(event.get("sources"), {})
    geometry = _latest_geometry(event.get("geometry"))
    coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
    event_ts = _parse_timestamp(geometry.get("date") if isinstance(geometry, dict) else None)
    is_open = _is_open_event(event)
    category_id = str(category.get("id", "") or "")
    category_label = str(category.get("title", "") or _humanize_category(category_id) or "Hazard")
    score = _score_event(
        category_id=category_id,
        event_ts=event_ts,
        is_open=is_open,
        focus_location=focus_location,
        coordinates=coordinates,
        magnitude_bonus=_magnitude_bonus(event),
        now_ts=now_ts,
    )

    return {
        "event_id": str(event.get("id", "") or ""),
        "title": str(event.get("title", "") or "").strip(),
        "category": category_id,
        "category_label": category_label,
        "source_label": str(source.get("id", "") or "EONET"),
        "source_url": str(source.get("url", "") or source.get("source", "") or ""),
        "date": str(geometry.get("date", "") if isinstance(geometry, dict) else ""),
        "is_open": is_open,
        "score": score,
        "region_label": _region_label(coordinates, focus_location),
        "magnitude_label": _magnitude_label(category_label, event),
    }


def build_priority_hazards(
    raw_events: list[dict[str, Any]],
    hazard_cfg: dict[str, Any],
    focus_location: dict[str, Any] | None,
    now_ts: float | None = None,
) -> list[dict[str, Any]]:
    if not hazard_cfg.get("enabled", True):
        return []

    min_score = int(hazard_cfg.get("min_score", 40))
    limit = int(hazard_cfg.get("limit", 3))

    ranked: list[dict[str, Any]] = []
    for event in raw_events or []:
        try:
            item = normalize_event(event, focus_location=focus_location, now_ts=now_ts)
        except Exception:
            continue
        if item["score"] >= min_score:
            ranked.append(item)

    ranked.sort(
        key=lambda item: (
            item["score"],
            item["date"],
            item["title"].lower(),
        ),
        reverse=True,
    )
    return ranked[:limit]


def _score_event(
    category_id: str,
    event_ts: float,
    is_open: bool,
    focus_location: dict[str, Any] | None,
    coordinates: Any,
    magnitude_bonus: int,
    now_ts: float | None,
) -> int:
    score = CATEGORY_BASE_SCORES.get(category_id, 40)
    now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()

    if event_ts:
        age_hours = max(0.0, (now - event_ts) / 3600.0)
        if age_hours <= 24:
            score += 12
        elif age_hours <= 72:
            score += 6

    if is_open:
        score += 8

    if _is_locally_relevant(focus_location, coordinates):
        score += 8

    score += magnitude_bonus

    return int(score)


def _is_open_event(event: dict[str, Any]) -> bool:
    closed = event.get("closed")
    return closed in (None, "", "null")


def _latest_geometry(geometry: Any) -> dict[str, Any]:
    if isinstance(geometry, list) and geometry:
        last = geometry[-1]
        return last if isinstance(last, dict) else {}
    if isinstance(geometry, dict):
        return geometry
    return {}


def _region_label(
    coordinates: Any,
    focus_location: dict[str, Any] | None,
) -> str:
    point = _representative_point(coordinates)
    if point is None:
        return "Global"
    if focus_location and _valid_focus_location(focus_location):
        distance = _distance_km(
            float(focus_location["lat"]),
            float(focus_location["lon"]),
            point[1],
            point[0],
        )
        if distance <= 250:
            name = str(focus_location.get("name", "") or "").strip()
            return f"Near {name}" if name else "Local area"
        if distance <= 1000:
            return "Regional"
    return "Global"


def _magnitude_label(category_label: str, event: dict[str, Any]) -> str:
    value = _magnitude_value(event)
    unit = _magnitude_unit(event)
    description = _magnitude_description(event)

    numeric_value = _magnitude_numeric_value(event)
    if numeric_value is not None and numeric_value > 0:
        parts: list[str] = []
        if unit:
            parts.append(f"{unit} {value}")
        elif value:
            parts.append(value)
        if description:
            parts.append(description)
        if parts:
            return " - ".join(parts)

    title = str(event.get("title", "") or "").strip()
    if title:
        return title
    return f"Active {category_label.lower()}"


def _magnitude_bonus(event: dict[str, Any]) -> int:
    value = _magnitude_numeric_value(event)
    if value is None or value <= 0:
        return 0
    return min(20, max(2, int(round(value * 3))))


def _humanize_category(category_id: str) -> str:
    if not category_id:
        return ""
    pieces: list[str] = []
    current = category_id[0]
    for char in category_id[1:]:
        if char.isupper() and (not current.endswith(" ")):
            pieces.append(current)
            current = char
        else:
            current += char
    pieces.append(current)
    return " ".join(part.capitalize() for part in pieces)


def _parse_timestamp(raw: Any) -> float:
    if not raw:
        return 0.0
    text = str(raw).strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _first_or_default(values: Any, default: dict[str, Any]) -> dict[str, Any]:
    if isinstance(values, list) and values:
        first = values[0]
        if isinstance(first, dict):
            return first
    return default


def _magnitude_value(event: dict[str, Any]) -> str:
    value = event.get("magnitudeValue")
    if value is None:
        magnitude = event.get("magnitude")
        if isinstance(magnitude, dict):
            value = magnitude.get("value")
    if value is None:
        return ""
    return str(value).strip()


def _magnitude_unit(event: dict[str, Any]) -> str:
    unit = event.get("magnitudeUnit")
    if unit is None:
        magnitude = event.get("magnitude")
        if isinstance(magnitude, dict):
            unit = magnitude.get("unit")
    if unit is None:
        return ""
    return str(unit).strip()


def _magnitude_description(event: dict[str, Any]) -> str:
    description = event.get("magnitudeDescription")
    if description is None:
        description = event.get("description")
    if description is None:
        magnitude = event.get("magnitude")
        if isinstance(magnitude, dict):
            description = magnitude.get("description")
    if description is None:
        return ""
    return str(description).strip()


def _magnitude_numeric_value(event: dict[str, Any]) -> float | None:
    raw = event.get("magnitudeValue")
    if raw is None:
        magnitude = event.get("magnitude")
        if isinstance(magnitude, dict):
            raw = magnitude.get("value")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _valid_coordinates(coordinates: Any) -> bool:
    return (
        isinstance(coordinates, (list, tuple))
        and len(coordinates) >= 2
        and all(isinstance(value, (int, float)) for value in coordinates[:2])
    )


def _valid_focus_location(focus_location: dict[str, Any]) -> bool:
    return all(
        key in focus_location and isinstance(focus_location[key], (int, float))
        for key in ("lat", "lon")
    )


def _is_locally_relevant(
    focus_location: dict[str, Any] | None,
    coordinates: Any,
) -> bool:
    if not focus_location or not _valid_focus_location(focus_location):
        return False
    point = _representative_point(coordinates)
    if point is None:
        return False
    distance = _distance_km(
        float(focus_location["lat"]),
        float(focus_location["lon"]),
        point[1],
        point[0],
    )
    return distance <= 500


def _representative_point(coordinates: Any) -> tuple[float, float] | None:
    points = list(_iter_points(coordinates))
    if not points:
        return None
    lon = sum(point[0] for point in points) / len(points)
    lat = sum(point[1] for point in points) / len(points)
    return (lon, lat)


def _iter_points(value: Any):
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            yield (float(value[0]), float(value[1]))
            return
        for item in value:
            yield from _iter_points(item)


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * radius_km * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
