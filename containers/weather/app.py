"""
Weather skill container - receives a location query, returns weather data.

Uses open-meteo (free, no API key required).

Contract:
  Input:  SKILL_INPUT env var (JSON with "query" field)
  Output: Weather summary printed to stdout
"""

import json
import os
import sys

import requests


def get_weather(location: str) -> str:
    try:
        geo_resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1},
            timeout=10,
        )
        geo_resp.raise_for_status()
        results = geo_resp.json().get("results", [])
        if not results:
            return f"Location not found: {location}"

        place = results[0]
        lat, lon = place["latitude"], place["longitude"]
        place_name = place.get("name", location)

        w_resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,apparent_temperature,weathercode,windspeed_10m,relativehumidity_2m",
                "temperature_unit": "fahrenheit",
                "windspeed_unit": "mph",
                "timezone": "auto",
                "forecast_days": 1,
            },
            timeout=10,
        )
        w_resp.raise_for_status()
        data = w_resp.json()

        current = data.get("current", {})
        condition = _weathercode_description(current.get("weathercode", 0))

        return json.dumps({
            "location": place_name,
            "temperature": f"{round(current.get('temperature_2m', 0))}°F",
            "feels_like": f"{round(current.get('apparent_temperature', 0))}°F",
            "conditions": condition,
            "humidity": f"{round(current.get('relativehumidity_2m', 0))}%",
            "wind_speed": f"{round(current.get('windspeed_10m', 0))} mph",
        })

    except Exception as exc:
        return f"Weather error: {exc}"


def _weathercode_description(code: int) -> str:
    """Map WMO weather interpretation code to a human-readable description."""
    table = {
        0: "clear sky",
        1: "mainly clear", 2: "partly cloudy", 3: "overcast",
        45: "fog", 48: "icy fog",
        51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
        61: "light rain", 63: "rain", 65: "heavy rain",
        71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
        80: "light showers", 81: "showers", 82: "heavy showers",
        85: "snow showers", 86: "heavy snow showers",
        95: "thunderstorm", 96: "thunderstorm with hail", 99: "heavy thunderstorm with hail",
    }
    return table.get(code, "unknown conditions")


def main():
    raw_input = os.environ.get("SKILL_INPUT", "")
    if not raw_input:
        raw_input = sys.stdin.read()

    try:
        data = json.loads(raw_input)
        query = data.get("query", "")
    except json.JSONDecodeError:
        query = raw_input.strip()

    if not query:
        print("No location provided")
        sys.exit(1)

    print(get_weather(query))


if __name__ == "__main__":
    main()
