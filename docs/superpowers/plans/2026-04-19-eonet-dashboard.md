# EONET Hazard-Aware Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add NASA EONET-backed hazard ranking to the existing dashboard so important natural hazards can appear above ordinary news without turning routine weather into alerts.

**Architecture:** Keep the native `dashboard` skill and host orchestration mostly unchanged. Add a focused EONET normalization/ranking helper under the dashboard container, pass a small hazard config through `DASHBOARD_CONFIG`, merge qualifying hazards into the dashboard data model in `containers/dashboard/app.py`, and render a compact `Priority hazards` block in the existing news panel.

**Tech Stack:** Python 3.11, Flask, Jinja2 templates, `requests`, `unittest`, existing MiniClaw dashboard container

---

## File Structure

**Create**

- `containers/dashboard/eonet.py` — EONET fetch, normalization, scoring, thresholding, and compact view-model helpers
- `tests/test_dashboard_eonet.py` — unit tests for normalization, ranking, threshold behavior, and fallback handling

**Modify**

- `containers/dashboard/app.py` — read hazard config, call the helper, merge hazards into the dashboard data model, and keep fallback behavior quiet
- `containers/dashboard/templates/dashboard.html` — render a `Priority hazards` block above regular news cards when hazards qualify
- `containers/dashboard/Dockerfile` — copy the new helper module into the container image
- `core/container_manager.py` — include default hazard-aware config in `DASHBOARD_CONFIG` when opening the dashboard

### Task 1: Add EONET ranking helper and focused unit tests

**Files:**
- Create: `containers/dashboard/eonet.py`
- Test: `tests/test_dashboard_eonet.py`

- [ ] **Step 1: Write the failing unit tests for normalization, ranking, and threshold behavior**

```python
import unittest
from unittest.mock import patch

from containers.dashboard.eonet import (
    build_priority_hazards,
    fetch_eonet_events,
    normalize_event,
)


OPEN_WILDFIRE = {
    "id": "EONET_1",
    "title": "Large wildfire near population center",
    "closed": None,
    "categories": [{"id": "wildfires", "title": "Wildfires"}],
    "sources": [{"id": "InciWeb", "url": "https://example.invalid/fire"}],
    "geometry": [{"date": "2026-04-19T10:00:00Z", "type": "Point", "coordinates": [-72.6, 44.3]}],
}

OPEN_DUST = {
    "id": "EONET_2",
    "title": "Regional dust event",
    "closed": None,
    "categories": [{"id": "dustHaze", "title": "Dust and Haze"}],
    "sources": [{"id": "NASA", "url": "https://example.invalid/dust"}],
    "geometry": [{"date": "2026-04-19T08:00:00Z", "type": "Point", "coordinates": [15.0, 22.0]}],
}


class DashboardEONETTests(unittest.TestCase):
    def test_normalize_event_extracts_dashboard_fields(self):
        item = normalize_event(OPEN_WILDFIRE, focus_location={"name": "Burlington", "lat": 44.47, "lon": -73.21})
        self.assertEqual(item["event_id"], "EONET_1")
        self.assertEqual(item["category"], "wildfires")
        self.assertEqual(item["category_label"], "Wildfires")
        self.assertEqual(item["source_label"], "InciWeb")
        self.assertTrue(item["is_open"])
        self.assertIn("score", item)

    def test_build_priority_hazards_prefers_major_hazard_over_lower_signal_item(self):
        ranked = build_priority_hazards(
            [OPEN_DUST, OPEN_WILDFIRE],
            hazard_cfg={"enabled": True, "limit": 3, "min_score": 40},
            focus_location={"name": "Burlington", "lat": 44.47, "lon": -73.21},
            now_ts=1776596400,
        )
        self.assertEqual(ranked[0]["category"], "wildfires")
        self.assertEqual(len(ranked), 1)

    def test_build_priority_hazards_returns_empty_when_all_items_are_below_threshold(self):
        ranked = build_priority_hazards(
            [OPEN_DUST],
            hazard_cfg={"enabled": True, "limit": 3, "min_score": 70},
            focus_location=None,
            now_ts=1776596400,
        )
        self.assertEqual(ranked, [])

    @patch("containers.dashboard.eonet.requests.get")
    def test_fetch_eonet_events_returns_empty_list_on_http_failure(self, mock_get):
        mock_get.side_effect = RuntimeError("boom")
        self.assertEqual(fetch_eonet_events({"enabled": True}), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail before implementation**

Run: `.venv/bin/python -m unittest tests.test_dashboard_eonet -v`
Expected: FAIL with `ModuleNotFoundError` for `containers.dashboard.eonet` or missing exported functions.

- [ ] **Step 3: Write the minimal EONET helper implementation**

```python
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

import requests


CATEGORY_BASE_SCORES = {
    "wildfires": 60,
    "severeStorms": 58,
    "volcanoes": 57,
    "floods": 55,
    "earthquakes": 55,
    "landslides": 48,
    "extremeTemperatures": 46,
    "dustHaze": 30,
}


def fetch_eonet_events(hazard_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    if not hazard_cfg.get("enabled", True):
        return []
    params = {
        "status": "open",
        "limit": hazard_cfg.get("fetch_limit", 20),
        "days": hazard_cfg.get("days", 14),
    }
    category_ids = hazard_cfg.get("categories") or []
    if category_ids:
        params["category"] = ",".join(category_ids)
    try:
        response = requests.get(
            "https://eonet.gsfc.nasa.gov/api/v3/events",
            params=params,
            timeout=10,
        )
        response.raise_for_status()
        return response.json().get("events", [])
    except Exception:
        return []


def normalize_event(event: dict[str, Any], focus_location: dict[str, float] | None = None, now_ts: float | None = None) -> dict[str, Any]:
    category = (event.get("categories") or [{}])[0]
    geometry = (event.get("geometry") or [{}])[-1]
    source = (event.get("sources") or [{}])[0]
    event_ts = _parse_event_timestamp(geometry.get("date"))
    score = _score_event(
        category_id=category.get("id", ""),
        event_ts=event_ts,
        is_open=event.get("closed") in (None, "", "null"),
        focus_location=focus_location,
        coordinates=geometry.get("coordinates"),
        now_ts=now_ts,
    )
    return {
        "event_id": event.get("id", ""),
        "title": event.get("title", "").strip(),
        "category": category.get("id", ""),
        "category_label": category.get("title", "Hazard"),
        "source_label": source.get("id", "EONET"),
        "source_url": source.get("url") or source.get("source") or "",
        "date": geometry.get("date", ""),
        "is_open": event.get("closed") in (None, "", "null"),
        "score": score,
        "region_label": _region_label(geometry.get("coordinates"), focus_location),
        "magnitude_label": _magnitude_label(event),
    }


def build_priority_hazards(raw_events: list[dict[str, Any]], hazard_cfg: dict[str, Any], focus_location: dict[str, float] | None, now_ts: float | None = None) -> list[dict[str, Any]]:
    ranked = [
        normalize_event(event, focus_location=focus_location, now_ts=now_ts)
        for event in raw_events
    ]
    ranked = [item for item in ranked if item["score"] >= hazard_cfg.get("min_score", 40)]
    ranked.sort(key=lambda item: (item["score"], item["date"], item["title"].lower()), reverse=True)
    return ranked[: hazard_cfg.get("limit", 3)]


def _score_event(category_id: str, event_ts: float, is_open: bool, focus_location: dict[str, float] | None, coordinates: Any, now_ts: float | None) -> int:
    score = CATEGORY_BASE_SCORES.get(category_id, 35)
    now_ts = now_ts or datetime.now(timezone.utc).timestamp()
    age_hours = max(0.0, (now_ts - event_ts) / 3600.0) if event_ts else 999.0
    if age_hours <= 24:
        score += 12
    elif age_hours <= 72:
        score += 6
    if is_open:
        score += 8
    if _is_locally_relevant(focus_location, coordinates):
        score += 8
    return score
```

- [ ] **Step 4: Run the tests to verify the helper passes**

Run: `.venv/bin/python -m unittest tests.test_dashboard_eonet -v`
Expected: PASS for all four `DashboardEONETTests`.

- [ ] **Step 5: Commit the helper and tests**

```bash
git add containers/dashboard/eonet.py tests/test_dashboard_eonet.py
git commit -m "feat: add EONET hazard ranking helper"
```

### Task 2: Wire hazard-aware config and merge hazards into dashboard data

**Files:**
- Modify: `containers/dashboard/app.py`
- Modify: `containers/dashboard/Dockerfile`
- Modify: `core/container_manager.py`
- Test: `tests/test_dashboard_eonet.py`
- Test: `tests/test_container_manager.py`

- [ ] **Step 1: Extend tests for app-level merge behavior and host config defaults**

```python
from unittest.mock import patch

from core.container_manager import ContainerManager
from containers.dashboard import app as dashboard_app


class DashboardAppIntegrationTests(unittest.TestCase):
    @patch("containers.dashboard.app.fetch_news", return_value=[{"source": "rss", "text": "headline", "image_url": ""}])
    @patch("containers.dashboard.app.fetch_priority_hazards", return_value=[{"title": "Major wildfire", "category_label": "Wildfires", "source_label": "InciWeb"}])
    def test_dashboard_index_includes_priority_hazards_when_news_panel_is_active(self, mock_hazards, mock_news):
        with dashboard_app.app.test_request_context("/"):
            with dashboard_app._state_lock:
                dashboard_app._state["panels"] = ["news"]
                dashboard_app._state["rss_feeds"] = []
                dashboard_app._state["gdelt_queries"] = []
                dashboard_app._state["hazard_config"] = {"enabled": True, "limit": 3, "min_score": 40}
            html = dashboard_app.index()
        self.assertIn("Priority hazards", html)
        self.assertIn("Major wildfire", html)


class ContainerManagerHazardConfigTests(unittest.TestCase):
    def test_open_dashboard_includes_default_hazard_config(self):
        manager = ContainerManager()
        manager.docker_available = False
        result = manager._open_dashboard(["news"], 10, location="Burlington,VT")
        self.assertIn("Dashboard unavailable", result)
```

Add one more assertion-driven unit around the JSON payload in `tests/test_container_manager.py` by patching `subprocess.run` and capturing the `docker run` command:

```python
    def test_open_dashboard_passes_hazard_config_in_dashboard_config(self):
        manager = ContainerManager()
        manager.docker_available = True

        class Result:
            returncode = 0
            stdout = "container123\n"
            stderr = ""

        with patch("core.container_manager.subprocess.run", return_value=Result()) as mock_run, \
             patch("core.container_manager.urllib.request.urlopen"), \
             patch.object(ContainerManager, "_find_chromium", return_value="/usr/bin/chromium"), \
             patch("core.container_manager.subprocess.Popen") as mock_popen:
            mock_popen.return_value.pid = 4321
            manager._open_dashboard(["news"], 10, location="Burlington,VT")

        docker_cmd = mock_run.call_args_list[0].args[0]
        config_arg = next(part for part in docker_cmd if part.startswith("DASHBOARD_CONFIG="))
        self.assertIn('"hazards"', config_arg)
        self.assertIn('"enabled": true', config_arg.lower())
```

- [ ] **Step 2: Run the targeted tests to confirm they fail**

Run: `.venv/bin/python -m unittest tests.test_dashboard_eonet tests.test_container_manager -v`
Expected: FAIL because `hazard_config` support and `fetch_priority_hazards` integration do not exist yet.

- [ ] **Step 3: Implement the app/data-path changes**

In `containers/dashboard/app.py`, add the new import and state:

```python
from eonet import build_priority_hazards, fetch_eonet_events

_state = {
    "panels": [],
    "needs_refresh": False,
    "rss_feeds": [],
    "gdelt_queries": [],
    "stock_tickers": ["AAPL", "TSLA", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "SPY"],
    "hazard_config": {
        "enabled": True,
        "limit": 3,
        "min_score": 40,
        "days": 14,
        "fetch_limit": 20,
        "categories": ["wildfires", "severeStorms", "volcanoes", "floods", "earthquakes", "landslides", "extremeTemperatures", "dustHaze"],
    },
}
```

Add focused helpers instead of inline fetch logic:

```python
def _focus_location() -> dict | None:
    location = os.environ.get("WEATHER_LOCATION", "").strip()
    if not location:
        return None
    city = location.split(",")[0].strip()
    try:
        geo_resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1},
            timeout=10,
        )
        geo_resp.raise_for_status()
        results = geo_resp.json().get("results", [])
        if not results:
            return None
        place = results[0]
        return {"name": place.get("name", city), "lat": place["latitude"], "lon": place["longitude"]}
    except Exception:
        return None


def fetch_priority_hazards(hazard_cfg: dict) -> list:
    focus_location = _focus_location()
    raw_events = fetch_eonet_events(hazard_cfg)
    return build_priority_hazards(raw_events, hazard_cfg, focus_location)
```

Then merge it into the `index()` route:

```python
    with _state_lock:
        panels = list(_state["panels"])
        rss_feeds = list(_state["rss_feeds"])
        gdelt_queries = list(_state["gdelt_queries"])
        stock_tickers = list(_state["stock_tickers"])
        hazard_config = dict(_state["hazard_config"])

    data = {}
    if "news" in panels:
        data["priority_hazards"] = fetch_priority_hazards(hazard_config)
        data["news"] = fetch_news(rss_feeds, gdelt_queries)
```

Update `main()` to hydrate the new config from `DASHBOARD_CONFIG`:

```python
    with _state_lock:
        _state["panels"] = inp.get("panels", ["news", "weather", "stocks", "music"])
        _state["rss_feeds"] = cfg.get("rss_feeds", DEFAULT_RSS_FEEDS)
        _state["gdelt_queries"] = cfg.get("gdelt_queries", DEFAULT_GDELT_QUERIES)
        _state["stock_tickers"] = cfg.get("stock_tickers", ["AAPL", "TSLA", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "SPY"])
        _state["hazard_config"] = cfg.get("hazards", dict(_state["hazard_config"]))
```

In `containers/dashboard/Dockerfile`, copy the helper into the image:

```dockerfile
COPY app.py /app/app.py
COPY eonet.py /app/eonet.py
COPY templates/ /app/templates/
```

In `core/container_manager.py`, add the default hazards config when composing `dashboard_config`:

```python
        dashboard_config = json.dumps({
            "rss_feeds": rss_feeds,
            "gdelt_queries": queries,
            "stock_tickers": ["AAPL", "TSLA", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "SPY"],
            "hazards": {
                "enabled": "news" in panels,
                "limit": 3,
                "min_score": 40,
                "days": 14,
                "fetch_limit": 20,
                "categories": [
                    "wildfires",
                    "severeStorms",
                    "volcanoes",
                    "floods",
                    "earthquakes",
                    "landslides",
                    "extremeTemperatures",
                    "dustHaze",
                ],
            },
        })
```

- [ ] **Step 4: Run the targeted tests to verify the integration passes**

Run: `.venv/bin/python -m unittest tests.test_dashboard_eonet tests.test_container_manager -v`
Expected: PASS, including the new app merge behavior and the Docker config assertion.

- [ ] **Step 5: Commit the integration changes**

```bash
git add containers/dashboard/app.py containers/dashboard/Dockerfile core/container_manager.py tests/test_dashboard_eonet.py tests/test_container_manager.py
git commit -m "feat: wire EONET hazards into dashboard data"
```

### Task 3: Render the Priority hazards block in the news panel

**Files:**
- Modify: `containers/dashboard/templates/dashboard.html`
- Test: `tests/test_dashboard_eonet.py`

- [ ] **Step 1: Add a failing rendering assertion for the hazard block**

```python
    @patch("containers.dashboard.app.fetch_news", return_value=[{"source": "rss", "text": "headline", "image_url": ""}])
    @patch(
        "containers.dashboard.app.fetch_priority_hazards",
        return_value=[
            {
                "title": "Major wildfire",
                "category_label": "Wildfires",
                "source_label": "InciWeb",
                "region_label": "Vermont",
                "magnitude_label": "High spread",
            }
        ],
    )
    def test_dashboard_template_renders_priority_hazard_block_before_news_cards(self, mock_hazards, mock_news):
        with dashboard_app.app.test_request_context("/"):
            with dashboard_app._state_lock:
                dashboard_app._state["panels"] = ["news"]
                dashboard_app._state["rss_feeds"] = []
                dashboard_app._state["gdelt_queries"] = []
                dashboard_app._state["hazard_config"] = {"enabled": True, "limit": 3, "min_score": 40}
            html = dashboard_app.index()
        self.assertIn("Priority hazards", html)
        self.assertIn("Major wildfire", html)
        self.assertLess(html.index("Priority hazards"), html.index("headline"))
```

- [ ] **Step 2: Run the dashboard EONET test module to verify the new assertion fails**

Run: `.venv/bin/python -m unittest tests.test_dashboard_eonet -v`
Expected: FAIL because the template does not render `priority_hazards` yet.

- [ ] **Step 3: Implement the compact hazard block in the template**

Add styles near the existing news panel CSS:

```html
.hazard-strip {
  display: grid;
  gap: 10px;
  margin-bottom: 14px;
}

.hazard-card {
  position: relative;
  padding: 14px 16px;
  border: 1px solid rgba(255, 95, 89, 0.28);
  background: linear-gradient(135deg, rgba(72, 16, 16, 0.92), rgba(29, 10, 14, 0.96));
}

.hazard-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.hazard-badge {
  padding: 4px 8px;
  font-size: 10px;
  font-weight: 900;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #fff2dc;
  background: rgba(255, 95, 89, 0.18);
  border: 1px solid rgba(255, 95, 89, 0.34);
}
```

Then render the block at the top of the existing news body:

```html
    <div class="news-body">
      {% if data.get('priority_hazards') %}
      <section class="hazard-strip">
        <div class="frame-subtitle">Priority hazards</div>
        {% for item in data['priority_hazards'] %}
        <article class="hazard-card">
          <div class="hazard-meta">
            <span class="hazard-badge">{{ item.category_label }}</span>
            {% if item.region_label %}<span class="source-tag">{{ item.region_label }}</span>{% endif %}
            <span class="source-tag">{{ item.source_label }}</span>
          </div>
          <div class="card-headline">{{ item.title }}</div>
          {% if item.magnitude_label %}
          <div class="frame-subtitle">{{ item.magnitude_label }}</div>
          {% endif %}
        </article>
        {% endfor %}
      </section>
      {% endif %}

      {% if data.get('news') %}
      <div class="news-cards">
```

- [ ] **Step 4: Run the rendering tests to verify the hazard block now appears**

Run: `.venv/bin/python -m unittest tests.test_dashboard_eonet -v`
Expected: PASS, including the order assertion showing hazards render ahead of standard news cards.

- [ ] **Step 5: Commit the template work**

```bash
git add containers/dashboard/templates/dashboard.html tests/test_dashboard_eonet.py
git commit -m "feat: render priority hazard block in dashboard"
```

### Task 4: Full verification and developer preview

**Files:**
- Modify: none
- Test: `tests/test_dashboard_eonet.py`
- Test: `tests/test_container_manager.py`

- [ ] **Step 1: Run the focused automated suite**

Run: `.venv/bin/python -m unittest tests.test_dashboard_eonet tests.test_container_manager -v`
Expected: PASS with no failures or errors.

- [ ] **Step 2: Run the broader smoke suite to catch regressions in existing core behavior**

Run: `.venv/bin/python -m unittest discover -s tests -v`
Expected: PASS, or at minimum no new failures attributable to dashboard hazard changes.

- [ ] **Step 3: Build the dashboard container image with the new helper copied in**

Run: `docker build -t miniclaw/dashboard:latest containers/dashboard`
Expected: successful build ending with `Successfully tagged miniclaw/dashboard:latest` or the local Docker equivalent.

- [ ] **Step 4: Preview the dashboard locally with the news panel enabled**

Run: `./scripts/preview_dashboard.sh --panels news,weather --location "Burlington,VT"`
Expected: the dashboard opens, the news panel still renders normally, and `Priority hazards` appears only when EONET returns qualifying events.

- [ ] **Step 5: Commit any final test-driven cleanup**

```bash
git add tests/test_dashboard_eonet.py tests/test_container_manager.py containers/dashboard/app.py containers/dashboard/eonet.py containers/dashboard/templates/dashboard.html containers/dashboard/Dockerfile core/container_manager.py
git commit -m "test: verify EONET dashboard hazard integration"
```
