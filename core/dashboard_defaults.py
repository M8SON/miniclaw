"""
Default hazard configuration for the dashboard skill.

Host-side copy: the same values live in
skills/dashboard/scripts/dashboard_defaults.py so the containerized app can
read them without reaching back into the core module. Keep the two files
in sync.
"""

DEFAULT_HAZARD_CATEGORIES = [
    "wildfires",
    "severeStorms",
    "volcanoes",
    "floods",
    "earthquakes",
    "landslides",
    "extremeTemperatures",
    "dustHaze",
]


def default_hazard_config(enabled: bool = True) -> dict:
    return {
        "enabled": enabled,
        "limit": 3,
        "min_score": 40,
        "days": 14,
        "fetch_limit": 20,
        "categories": list(DEFAULT_HAZARD_CATEGORIES),
    }


DEFAULT_HAZARD_CONFIG = default_hazard_config()
