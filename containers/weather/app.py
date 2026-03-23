"""
Weather skill container - receives a location query, returns weather data.

Contract:
  Input:  SKILL_INPUT env var or stdin (JSON with "query" field)
  Output: Weather summary printed to stdout
"""

import os
import sys
import json
import requests


def get_weather(location: str) -> str:
    api_key = os.environ.get("OPENWEATHER_API_KEY")
    if not api_key:
        return "Weather service not configured: missing API key"

    try:
        response = requests.get(
            "http://api.openweathermap.org/data/2.5/weather",
            params={"q": location, "appid": api_key, "units": "imperial"},
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            return json.dumps({
                "location": data["name"],
                "temperature": f"{data['main']['temp']}°F",
                "feels_like": f"{data['main']['feels_like']}°F",
                "conditions": data["weather"][0]["description"],
                "humidity": f"{data['main']['humidity']}%",
                "wind_speed": f"{data['wind']['speed']} mph",
            })

        return f"Weather lookup failed: HTTP {response.status_code}"

    except Exception as e:
        return f"Weather error: {str(e)}"


def main():
    # Read input from SKILL_INPUT env var or stdin
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

    result = get_weather(query)
    print(result)


if __name__ == "__main__":
    main()
