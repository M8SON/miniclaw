"""
Homebridge skill — control smart home devices via Homebridge Config UI X.

Ported from: https://github.com/openclaw/skills/tree/main/skills/jiasenl/clawdbot-skill-homebridge
Adapted for MiniClaw: credentials via env vars, input via SKILL_INPUT JSON.
"""

import json
import os
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def make_request(url: str, method: str = "GET", data: dict = None, token: str = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        print(f"Homebridge API error {e.code}: {e.reason}. {error_body[:200]}")
        sys.exit(1)
    except URLError as e:
        print(f"Cannot reach Homebridge at {url}: {e.reason}")
        sys.exit(1)


def authenticate(base_url: str, username: str, password: str) -> str:
    resp = make_request(f"{base_url}/api/auth/login", method="POST",
                        data={"username": username, "password": password})
    token = resp.get("access_token")
    if not token:
        print("Authentication failed: no access token returned.")
        sys.exit(1)
    return token


def fuzzy_find(accessories: list, name: str) -> list:
    """Return accessories whose serviceName contains the search term (case-insensitive)."""
    needle = name.lower()
    return [a for a in accessories if needle in a.get("serviceName", "").lower()]


def format_values(values: dict) -> str:
    parts = []
    if "On" in values:
        parts.append("on" if values["On"] else "off")
    if "Brightness" in values:
        parts.append(f"brightness {values['Brightness']}%")
    if "TargetTemperature" in values:
        parts.append(f"target temp {values['TargetTemperature']}°C")
    if "CurrentTemperature" in values:
        parts.append(f"current temp {values['CurrentTemperature']}°C")
    if "RotationSpeed" in values:
        parts.append(f"speed {values['RotationSpeed']}%")
    if "Hue" in values:
        parts.append(f"hue {values['Hue']}°")
    if "Saturation" in values:
        parts.append(f"saturation {values['Saturation']}%")
    return ", ".join(parts) if parts else "state unknown"


def action_list(base_url: str, token: str, room: str = None, device_type: str = None) -> str:
    accessories = make_request(f"{base_url}/api/accessories", token=token)

    if device_type:
        accessories = [a for a in accessories if a.get("type") == device_type]

    if room:
        layout = make_request(f"{base_url}/api/accessories/layout", token=token)
        room_ids = set()
        for r in layout:
            if room.lower() in r.get("name", "").lower():
                for svc in r.get("services", []):
                    room_ids.add(svc.get("uniqueId"))
        accessories = [a for a in accessories if a.get("uniqueId") in room_ids]

    if not accessories:
        return "No devices found matching that filter."

    lines = [f"Found {len(accessories)} device(s):"]
    for a in accessories:
        state = format_values(a.get("values", {}))
        lines.append(f"  {a['serviceName']} ({a['type']}) — {state}")
    return "\n".join(lines)


def action_rooms(base_url: str, token: str) -> str:
    layout = make_request(f"{base_url}/api/accessories/layout", token=token)
    if not layout:
        return "No rooms found."
    lines = []
    for room in layout:
        names = [s.get("serviceName", "?") for s in room.get("services", [])]
        lines.append(f"{room['name']}: {', '.join(names) if names else 'no devices'}")
    return "\n".join(lines)


def action_get(base_url: str, token: str, device_name: str) -> str:
    if not device_name:
        return "Please specify a device name."
    accessories = make_request(f"{base_url}/api/accessories", token=token)
    matches = fuzzy_find(accessories, device_name)
    if not matches:
        names = [a["serviceName"] for a in accessories[:10]]
        return f"No device matching '{device_name}'. Available: {', '.join(names)}."
    a = matches[0]
    state = format_values(a.get("values", {}))
    return f"{a['serviceName']} ({a['type']}): {state}"


def action_set(base_url: str, token: str, device_name: str, characteristic: str, value: str) -> str:
    if not device_name or not characteristic or value is None:
        return "Please specify device name, characteristic, and value."

    accessories = make_request(f"{base_url}/api/accessories", token=token)
    matches = fuzzy_find(accessories, device_name)
    if not matches:
        names = [a["serviceName"] for a in accessories[:10]]
        return f"No device matching '{device_name}'. Available: {', '.join(names)}."

    # Convert value to correct type
    coerced = value
    if isinstance(value, str):
        if value.lower() == "true":
            coerced = True
        elif value.lower() == "false":
            coerced = False
        elif value.lstrip("-").isdigit():
            coerced = int(value)
        else:
            try:
                coerced = float(value)
            except ValueError:
                pass

    a = matches[0]
    make_request(
        f"{base_url}/api/accessories/{a['uniqueId']}",
        method="PUT",
        token=token,
        data={"characteristicType": characteristic, "value": coerced},
    )
    return f"Done. Set {characteristic} to {value} on {a['serviceName']}."


def main():
    raw = os.environ.get("SKILL_INPUT", "") or sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("Invalid input: expected JSON.")
        sys.exit(1)

    base_url = os.environ.get("HOMEBRIDGE_URL", "").rstrip("/")
    username = os.environ.get("HOMEBRIDGE_USERNAME", "")
    password = os.environ.get("HOMEBRIDGE_PASSWORD", "")

    if not base_url or not username or not password:
        print("Missing required env vars: HOMEBRIDGE_URL, HOMEBRIDGE_USERNAME, HOMEBRIDGE_PASSWORD")
        sys.exit(1)

    token = authenticate(base_url, username, password)

    action = data.get("action", "list")

    if action == "list":
        print(action_list(base_url, token, data.get("room"), data.get("device_type")))
    elif action == "rooms":
        print(action_rooms(base_url, token))
    elif action == "get":
        print(action_get(base_url, token, data.get("device_name", "")))
    elif action == "set":
        print(action_set(base_url, token,
                         data.get("device_name", ""),
                         data.get("characteristic", ""),
                         str(data.get("value", ""))))
    else:
        print(f"Unknown action '{action}'. Use: list, rooms, get, set.")


if __name__ == "__main__":
    main()
