# MiniClaw 2.0 — Dashboard Display Skill Design

**Date:** 2026-04-10
**Author:** Mason Misch
**Status:** Approved

---

## Overview

A new optional skill that renders an on-demand visual dashboard on a connected monitor. Triggered entirely by voice, dismissed by voice or auto-timeout. Claude remains the sole AI brain (no on-device LLM). Designed for Pi 5 resource efficiency — nothing runs in the background, all data is fetched at open time.

---

## Architecture

```
Voice request
  → Whisper STT
  → Claude (extracts intent + panel list)
  → container_manager starts dashboard Docker container
      → Flask server (localhost:7860)
      → data fetchers (news, weather, stocks, music)
      → renders HTML dashboard
  → container_manager launches Chromium on HOST (kiosk mode) → http://localhost:7860
  → user sees dashboard

Close trigger (voice or auto-timeout)
  → close_dashboard() skill call
  → container_manager kills host Chromium PID (from lock file)
  → stops Docker container
  → removes lock file
```

**Important separation:** Chromium runs on the Pi host (so it can access the display). The Flask server runs inside Docker. The lock file stores both the host Chromium PID and the container ID so both can be cleaned up.

**New files:**
- `skills/dashboard/config.yaml` — skill definition (actions, parameters)
- `skills/dashboard/SKILL.md` — Claude's instructions for when/how to use the skill
- `containers/dashboard/app.py` — Flask server + data fetchers
- `containers/dashboard/templates/dashboard.html` — B-layout HTML template
- `containers/dashboard/Dockerfile`

---

## Layout

**B — Focus + Sidebar:** News feed occupies the left two-thirds of the screen. Weather, stocks, and music stack vertically in a right sidebar. If news is not in the requested panels, the first requested panel takes the main area. Panel presence is dynamic — only requested panels render.

---

## Skill Actions

Two actions registered with Claude:

### `open_dashboard(panels, timeout_minutes=10)`

- `panels`: list from `["news", "weather", "stocks", "music"]`
- `timeout_minutes`: auto-close after this many minutes since opening (default 10); the container runs an internal countdown and self-exits when it expires
- Checks `~/.miniclaw/dashboard.lock` — if dashboard already running, sends a `GET /refresh?panels=...` request to the running Flask server to update the display without relaunching Chromium
- Starts Docker container, Flask server, launches Chromium on host in kiosk mode
- Writes `~/.miniclaw/dashboard.lock` (host Chromium PID + container ID + port)

### `close_dashboard()`

- Reads lock file, kills Chromium PID, stops container
- Removes `~/.miniclaw/dashboard.lock`
- Claude calls this on explicit close requests or when auto-timeout fires

**Claude uses natural language understanding** — no verbatim commands required. "Throw up the news", "show me what's happening", "get rid of that" all map correctly.

---

## Data Sources & Panels

### News / OSINT
- **Source:** Playwright scrapes Twitter/X timelines for configured accounts (default: `@OSINTDefender`)
- **Fallback:** RSS feeds from the same accounts if scraping fails
- **Output:** Latest N posts rendered as headline cards in the main panel
- **Additional sources:** Configurable list in `config.yaml` — add any Twitter account or RSS URL

### Weather
- **Source:** `open-meteo.com` free API — no API key required
- **Output:** Current temp + condition, tonight's low, tomorrow's forecast
- **Config:** Reads `WEATHER_LOCATION` env var (already used in MiniClaw startup context)

### Stocks
- **Source:** `yfinance` Python library (Yahoo Finance unofficial API, free, no key)
- **Output:** Price + daily % change for each ticker
- **Config:** Watchlist defined in `config.yaml`

### Music / Now Playing
- **Source:** Shared state file `~/.miniclaw/now_playing.json` — the SoundCloud skill container will need to be updated to write this file on play/pause/stop events (new requirement on that skill)
- **Output:** Track name, artist, playback position
- **Fallback:** "Nothing playing" if file absent or stale (>60s old)

### Audio Control
- "Turn on audio for the news" → Claude calls `open_dashboard(panels=["news"])` and separately triggers the audio skill to play a news stream (NPR, BBC, etc.)
- The dashboard music widget reflects playback state only — audio control stays in the existing audio/SoundCloud skill

---

## Error Handling

| Scenario | Behavior |
|---|---|
| No monitor connected | Chromium fails to launch → container exits cleanly → Claude responds via voice: "I don't see a display connected." |
| Data fetch failure (any panel) | That panel shows "Feed unavailable" — other panels render normally |
| Dashboard already open | Lock file detected → update panels in-place, no second Chromium |
| Twitter scraping breaks | Falls back to RSS; news panel shows "Feed unavailable" if both fail |
| Container crash | Lock file left behind → next `open_dashboard` call detects stale lock (PID not running), cleans up and relaunches |

---

## Auto-Timeout

Managed host-side by `container_manager`, not inside the Docker container (a container cannot signal host processes). When `open_dashboard` is called, `container_manager` starts a background thread that sleeps for `timeout_minutes`. If `close_dashboard()` is not called before the thread wakes, it kills the host Chromium PID and stops the container, then removes the lock file. Default: 10 minutes. Overridable per voice request ("show me the news for 30 minutes").

Timeout is time-based from open — the container is purely a Flask server with no lifecycle awareness.

---

## Resource Profile

- Idle (dashboard closed): zero CPU, zero memory overhead
- Active: one Docker container + Chromium in kiosk mode
- Data fetch happens once at open time — no polling, no background refresh
- Consistent with Pi 5 efficiency goals

---

## Out of Scope

- Always-on display mode
- Background data refresh / live-updating panels
- On-device LLM (Claude remains the sole AI brain, API-based)
- Push notifications or alerts while dashboard is closed
