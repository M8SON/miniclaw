# Dashboard Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a voice-triggered on-demand visual dashboard skill to MiniClaw that displays news/OSINT, weather, stocks, and music panels on a connected monitor.

**Architecture:** A native skill handler in `ContainerManager` starts a long-running Flask Docker container (serving the dashboard HTML) and launches Chromium on the host in kiosk mode pointing at it. The container is started detached (`docker run -d`), unlike other skills which run synchronously. A `threading.Timer` on the host handles auto-close. Closing kills the host Chromium process and stops the container.

**Tech Stack:** Flask (container), Playwright (news scraping inside container), yfinance (stocks), open-meteo API (weather, no key needed), Chromium (host kiosk display), threading.Timer (timeout), Python signal/subprocess (lifecycle management).

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `core/container_manager.py` | Modify | Add volume mount support + dashboard native handlers |
| `skills/soundcloud/config.yaml` | Modify | Add volume mount for `~/.miniclaw` |
| `containers/soundcloud/app.py` | Modify | Write `/miniclaw/now_playing.json` after successful play |
| `skills/dashboard/SKILL.md` | Create | Claude's routing instructions for the dashboard skill |
| `skills/dashboard/config.yaml` | Create | Skill type (native) + timeout |
| `containers/dashboard/Dockerfile` | Create | Build image with Flask, Playwright, yfinance |
| `containers/dashboard/app.py` | Create | Flask server: routes `/`, `/refresh`, `/poll`, `/health` + data fetchers |
| `containers/dashboard/templates/dashboard.html` | Create | B-layout HTML: news main panel + sidebar widgets |

---

## Task 1: Add Volume Mount Support to ContainerManager

Volume mounts allow skill containers to read/write the host's `~/.miniclaw/` directory. The soundcloud skill needs this to write `now_playing.json`.

**Files:**
- Modify: `core/container_manager.py`

- [ ] **Step 1: Add `volumes` parameter to `_build_docker_cmd`**

In `core/container_manager.py`, find the `_build_docker_cmd` signature and body. Add `volumes` parameter and the flag injection:

```python
def _build_docker_cmd(
    self,
    image: str,
    env_vars: dict[str, str] | None = None,
    devices: list[str] | None = None,
    input_data: str = "",
    memory: str | None = None,
    read_only: bool = True,
    extra_tmpfs: list[str] | None = None,
    volumes: list[str] | None = None,   # ← ADD THIS
) -> list[str]:
```

Then inside the body, after the `extra_tmpfs` loop and before the `env_vars` block, add:

```python
        for volume in (volumes or []):
            expanded = os.path.expanduser(volume)
            cmd.extend(["-v", expanded])
```

- [ ] **Step 2: Pass `volumes` through `execute_skill`**

In `execute_skill`, find the `_build_docker_cmd(...)` call and add the `volumes` keyword argument:

```python
        cmd = self._build_docker_cmd(
            image=image,
            env_vars=self._collect_env_vars(config.get("env_passthrough", [])),
            devices=config.get("devices", []),
            input_data=json.dumps(tool_input),
            memory=config.get("memory", self.memory_limit),
            read_only=config.get("read_only", True),
            extra_tmpfs=config.get("extra_tmpfs", []),
            volumes=config.get("volumes", []),   # ← ADD THIS
        )
```

- [ ] **Step 3: Verify the docker command is built correctly**

Run this quick check from the miniclaw directory:

```bash
cd /home/daedalus/linux/miniclaw && source .venv/bin/activate && python3 - <<'EOF'
from core.container_manager import ContainerManager
cm = ContainerManager()
cmd = cm._build_docker_cmd(
    image="miniclaw/test:latest",
    volumes=["~/.miniclaw:/miniclaw"],
)
assert "-v" in cmd, "Missing -v flag"
v_idx = cmd.index("-v")
assert "/miniclaw" in cmd[v_idx + 1], "Volume path not expanded"
print("PASS:", cmd[v_idx], cmd[v_idx + 1])
EOF
```

Expected output: `PASS: -v /home/daedalus/.miniclaw:/miniclaw`

- [ ] **Step 4: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add core/container_manager.py && git commit -m "feat: add volume mount support to ContainerManager"
```

---

## Task 2: SoundCloud Now-Playing State File

The dashboard music widget reads `~/.miniclaw/now_playing.json` written by the soundcloud container. The container needs a volume mount to reach the host path.

**Files:**
- Modify: `skills/soundcloud/config.yaml`
- Modify: `containers/soundcloud/app.py`

- [ ] **Step 1: Add volume mount to soundcloud config**

Replace the contents of `skills/soundcloud/config.yaml`:

```yaml
image: miniclaw/soundcloud:latest
timeout_seconds: 45
devices:
  - /dev/snd
volumes:
  - ~/.miniclaw:/miniclaw
```

- [ ] **Step 2: Write now_playing.json in soundcloud app.py**

In `containers/soundcloud/app.py`, add a `write_now_playing` function and call it after a successful result. Replace the file with:

```python
"""
SoundCloud skill container - searches and plays music via yt-dlp and mpv.
"""

import os
import sys
import json
import time
import subprocess


def write_now_playing(title: str) -> None:
    try:
        with open("/miniclaw/now_playing.json", "w") as f:
            json.dump({"title": title, "timestamp": time.time()}, f)
    except OSError:
        pass


def search_and_play(query: str) -> str:
    search_result = subprocess.run(
        [
            "yt-dlp",
            "--get-title",
            "--get-url",
            "-f", "bestaudio",
            "--no-playlist",
            "--cache-dir", "/tmp/yt-dlp-cache",
            f"scsearch1:{query}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if search_result.returncode != 0 or not search_result.stdout.strip():
        return f"No results found for '{query}' on SoundCloud"

    lines = search_result.stdout.strip().splitlines()
    if len(lines) < 2:
        return f"Could not retrieve stream for '{query}'"

    title = lines[0]
    stream_url = lines[1]

    subprocess.Popen(
        ["mpv", "--no-video", "--really-quiet", stream_url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    write_now_playing(title)
    return f"Now playing: {title}"


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
        print("No query provided")
        sys.exit(1)

    print(search_and_play(query))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify the now_playing write path manually**

```bash
mkdir -p ~/.miniclaw && python3 - <<'EOF'
import json, time, os
os.makedirs(os.path.expanduser("~/.miniclaw"), exist_ok=True)
# Simulate what the container writes to /miniclaw (which maps to ~/.miniclaw on host)
path = os.path.expanduser("~/.miniclaw/now_playing.json")
with open(path, "w") as f:
    json.dump({"title": "Test Track", "timestamp": time.time()}, f)
data = json.loads(open(path).read())
assert data["title"] == "Test Track"
age = time.time() - data["timestamp"]
assert age < 60, "Should be fresh"
print("PASS: now_playing.json written and readable")
EOF
```

Expected: `PASS: now_playing.json written and readable`

- [ ] **Step 4: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add skills/soundcloud/config.yaml containers/soundcloud/app.py && git commit -m "feat: soundcloud writes now_playing.json for dashboard music widget"
```

---

## Task 3: Dashboard Flask Container (app.py)

The Flask server runs inside Docker, serves the dashboard HTML, fetches data from APIs, and exposes a `/refresh` endpoint so the native handler can update panels without restarting the container.

**Files:**
- Create: `containers/dashboard/app.py`

- [ ] **Step 1: Create the directory**

```bash
mkdir -p /home/daedalus/linux/miniclaw/containers/dashboard/templates
```

- [ ] **Step 2: Write `containers/dashboard/app.py`**

```python
"""
Dashboard skill container — serves a live dashboard page via Flask.

Routes:
  GET /              render the dashboard with current panels + fetched data
  GET /refresh       update active panels (panels=news,weather,...) then signal reload
  GET /poll          returns {"reload": true} once after /refresh, then {"reload": false}
  GET /health        liveness check
"""

import os
import sys
import json
import time
import threading

import requests
from flask import Flask, render_template, request, jsonify

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False


app = Flask(__name__)

_state_lock = threading.Lock()
_state = {
    "panels": [],
    "needs_refresh": False,
    "news_accounts": ["OSINTDefender"],
    "stock_tickers": ["AAPL", "TSLA", "NVDA"],
}


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def fetch_news(accounts: list) -> list:
    """Scrape Twitter/X timelines for the given accounts via Playwright."""
    if not PLAYWRIGHT_AVAILABLE:
        return []
    items = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            for account in accounts:
                try:
                    page = browser.new_page(
                        user_agent=(
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        )
                    )
                    page.goto(
                        f"https://twitter.com/{account}",
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                    page.wait_for_timeout(2000)
                    tweets = page.query_selector_all('[data-testid="tweetText"]')
                    for tweet in tweets[:6]:
                        text = tweet.inner_text().strip()
                        if text:
                            items.append({"source": f"@{account}", "text": text})
                    page.close()
                except Exception:
                    pass
            browser.close()
    except Exception:
        pass
    return items


def fetch_weather() -> dict:
    """Fetch current weather from open-meteo (free, no API key)."""
    location = os.environ.get("WEATHER_LOCATION", "New York,NY")
    try:
        geo_resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1},
            timeout=10,
        )
        geo_resp.raise_for_status()
        results = geo_resp.json().get("results", [])
        if not results:
            return {"error": f"Location not found: {location}"}

        place = results[0]
        lat, lon = place["latitude"], place["longitude"]

        w_resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,weathercode",
                "daily": "temperature_2m_min,temperature_2m_max",
                "temperature_unit": "fahrenheit",
                "timezone": "auto",
                "forecast_days": 2,
            },
            timeout=10,
        )
        w_resp.raise_for_status()
        data = w_resp.json()

        current = data.get("current", {})
        daily = data.get("daily", {})
        mins = daily.get("temperature_2m_min", [])
        maxs = daily.get("temperature_2m_max", [])

        return {
            "temp": f"{round(current.get('temperature_2m', 0))}°F",
            "tonight_low": f"{round(mins[0])}°F" if mins else "N/A",
            "tomorrow_high": f"{round(maxs[1])}°F" if len(maxs) > 1 else "N/A",
            "location": place.get("name", location),
        }
    except Exception as exc:
        return {"error": str(exc)}


def fetch_stocks(tickers: list) -> list:
    """Fetch stock price and daily change via yfinance."""
    if not YFINANCE_AVAILABLE:
        return [{"ticker": t, "price": "N/A", "change": "N/A", "positive": False} for t in tickers]
    results = []
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).fast_info
            price = info.last_price
            prev = info.previous_close
            pct = ((price - prev) / prev * 100) if prev else 0.0
            results.append({
                "ticker": ticker,
                "price": f"${price:.2f}",
                "change": f"{pct:+.1f}%",
                "positive": pct >= 0,
            })
        except Exception:
            results.append({"ticker": ticker, "price": "N/A", "change": "N/A", "positive": False})
    return results


def fetch_music() -> dict:
    """Read now_playing.json written by the soundcloud skill via shared volume."""
    try:
        raw = open("/miniclaw/now_playing.json").read()
        data = json.loads(raw)
        age = time.time() - data.get("timestamp", 0)
        if age > 60:
            return {"status": "idle"}
        return {"status": "playing", "title": data.get("title", "Unknown")}
    except Exception:
        return {"status": "idle"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/poll")
def poll():
    with _state_lock:
        needs = _state["needs_refresh"]
        if needs:
            _state["needs_refresh"] = False
    return jsonify({"reload": needs})


@app.route("/refresh")
def refresh():
    panels_str = request.args.get("panels", "")
    if panels_str:
        panels = [p.strip() for p in panels_str.split(",") if p.strip()]
        with _state_lock:
            _state["panels"] = panels
            _state["needs_refresh"] = True
    return jsonify({"status": "ok"})


@app.route("/")
def index():
    with _state_lock:
        panels = list(_state["panels"])
        news_accounts = list(_state["news_accounts"])
        stock_tickers = list(_state["stock_tickers"])

    data = {}
    if "news" in panels:
        data["news"] = fetch_news(news_accounts)
    if "weather" in panels:
        data["weather"] = fetch_weather()
    if "stocks" in panels:
        data["stocks"] = fetch_stocks(stock_tickers)
    if "music" in panels:
        data["music"] = fetch_music()

    return render_template("dashboard.html", panels=panels, data=data)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    raw = os.environ.get("SKILL_INPUT", "{}")
    try:
        inp = json.loads(raw)
    except json.JSONDecodeError:
        inp = {}

    cfg_raw = os.environ.get("DASHBOARD_CONFIG", "{}")
    try:
        cfg = json.loads(cfg_raw)
    except json.JSONDecodeError:
        cfg = {}

    with _state_lock:
        _state["panels"] = inp.get("panels", ["news", "weather", "stocks", "music"])
        _state["news_accounts"] = cfg.get("news_accounts", ["OSINTDefender"])
        _state["stock_tickers"] = cfg.get("stock_tickers", ["AAPL", "TSLA", "NVDA"])

    app.run(host="0.0.0.0", port=7860, debug=False, threaded=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Smoke-test the fetchers in isolation (no Docker yet)**

Activate the venv and install flask, yfinance, requests if not present, then test weather:

```bash
cd /home/daedalus/linux/miniclaw && source .venv/bin/activate
pip install flask yfinance requests --quiet
python3 - <<'EOF'
import os
os.environ["WEATHER_LOCATION"] = "Burlington,VT"
import sys
sys.path.insert(0, "containers/dashboard")
from app import fetch_weather
result = fetch_weather()
print(result)
assert "temp" in result, f"Missing temp key: {result}"
print("PASS: weather fetcher works")
EOF
```

Expected: dict with `temp`, `tonight_low`, `tomorrow_high`, `location` keys printed.

- [ ] **Step 4: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add containers/dashboard/app.py && git commit -m "feat: add dashboard Flask container app"
```

---

## Task 4: Dashboard HTML Template

B-layout: news feed occupies the left two-thirds; weather, stocks, and music widgets stack in the right sidebar. A JS poller calls `/poll` every 2 seconds and reloads when `/refresh` has been called.

**Files:**
- Create: `containers/dashboard/templates/dashboard.html`

- [ ] **Step 1: Write the template**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>MiniClaw</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  background: #0d1117;
  color: #cdd9e5;
  font-family: 'Segoe UI', system-ui, sans-serif;
  height: 100vh;
  overflow: hidden;
}
.dashboard {
  display: grid;
  grid-template-columns: 2fr 1fr;
  height: 100vh;
  gap: 12px;
  padding: 16px;
}
.main-panel {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 20px;
  overflow-y: auto;
}
.sidebar {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.widget {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 16px;
  flex: 1;
  overflow: hidden;
}
.panel-header {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #30363d;
}
.news-header  { color: #58a6ff; }
.weather-header { color: #e3b341; }
.stocks-header { color: #3fb950; }
.music-header  { color: #f78166; }
.news-item {
  padding: 10px 0;
  border-bottom: 1px solid #21262d;
  font-size: 14px;
  line-height: 1.5;
}
.news-item:last-child { border-bottom: none; }
.news-source { font-size: 11px; color: #8b949e; margin-bottom: 3px; }
.weather-temp { font-size: 36px; font-weight: 300; margin-bottom: 6px; }
.weather-detail { font-size: 12px; color: #8b949e; line-height: 2; }
.stock-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 7px 0;
  font-size: 13px;
  border-bottom: 1px solid #21262d;
}
.stock-row:last-child { border-bottom: none; }
.stock-ticker { font-weight: 600; }
.pos { color: #3fb950; }
.neg { color: #f85149; }
.music-title { font-size: 14px; font-weight: 500; margin-bottom: 4px; }
.muted { color: #8b949e; font-size: 13px; }
.unavailable { color: #8b949e; font-size: 12px; font-style: italic; }
.clock {
  position: fixed;
  bottom: 16px;
  right: 16px;
  font-size: 12px;
  color: #484f58;
}
</style>
</head>
<body>

<div class="clock" id="clock"></div>

<div class="dashboard">

  {# ── Main panel: news takes focus, or first panel if no news ── #}
  {% if 'news' in panels %}
  <div class="main-panel">
    <div class="panel-header news-header">News &amp; OSINT</div>
    {% if data.get('news') %}
      {% for item in data['news'] %}
      <div class="news-item">
        <div class="news-source">{{ item.source }}</div>
        {{ item.text }}
      </div>
      {% endfor %}
    {% else %}
      <div class="unavailable">Feed unavailable</div>
    {% endif %}
  </div>
  {% elif panels %}
  <div class="main-panel">
    <div class="panel-header">{{ panels[0]|upper }}</div>
    <div class="unavailable">No content</div>
  </div>
  {% else %}
  <div class="main-panel">
    <div class="panel-header">Dashboard</div>
    <div class="unavailable">No panels selected</div>
  </div>
  {% endif %}

  {# ── Sidebar widgets ── #}
  <div class="sidebar">

    {% if 'weather' in panels %}
    <div class="widget">
      <div class="panel-header weather-header">Weather</div>
      {% set w = data.get('weather', {}) %}
      {% if w and not w.get('error') %}
        <div class="weather-temp">{{ w.temp }}</div>
        <div class="weather-detail">
          Tonight low: {{ w.tonight_low }}<br>
          Tomorrow high: {{ w.tomorrow_high }}<br>
          {{ w.location }}
        </div>
      {% else %}
        <div class="unavailable">{{ w.get('error', 'Unavailable') }}</div>
      {% endif %}
    </div>
    {% endif %}

    {% if 'stocks' in panels %}
    <div class="widget">
      <div class="panel-header stocks-header">Stocks</div>
      {% if data.get('stocks') %}
        {% for s in data['stocks'] %}
        <div class="stock-row">
          <span class="stock-ticker">{{ s.ticker }}</span>
          <span>{{ s.price }}</span>
          <span class="{{ 'pos' if s.positive else 'neg' }}">{{ s.change }}</span>
        </div>
        {% endfor %}
      {% else %}
        <div class="unavailable">Unavailable</div>
      {% endif %}
    </div>
    {% endif %}

    {% if 'music' in panels %}
    <div class="widget">
      <div class="panel-header music-header">Now Playing</div>
      {% set m = data.get('music', {}) %}
      {% if m.get('status') == 'playing' %}
        <div class="music-title">{{ m.title }}</div>
      {% else %}
        <div class="muted">Nothing playing</div>
      {% endif %}
    </div>
    {% endif %}

  </div>
</div>

<script>
// Live clock
function tick() {
  document.getElementById('clock').textContent =
    new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}
tick();
setInterval(tick, 1000);

// Poll for panel-change reload signal
setInterval(() => {
  fetch('/poll')
    .then(r => r.json())
    .then(d => { if (d.reload) window.location.reload(); })
    .catch(() => {});
}, 2000);
</script>

</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add containers/dashboard/templates/dashboard.html && git commit -m "feat: add dashboard B-layout HTML template"
```

---

## Task 5: Dashboard Dockerfile and Image Build

**Files:**
- Create: `containers/dashboard/Dockerfile`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
FROM miniclaw/base:latest

RUN pip install --no-cache-dir flask yfinance playwright requests

# Install Chromium and its system dependencies for news scraping
RUN playwright install chromium && playwright install-deps chromium

COPY app.py /app/app.py
COPY templates/ /app/templates/

WORKDIR /app
CMD ["python", "app.py"]
```

- [ ] **Step 2: Build the image**

```bash
cd /home/daedalus/linux/miniclaw && docker build -t miniclaw/dashboard:latest containers/dashboard/
```

Expected: image builds successfully. This will take several minutes the first time (Chromium download ~200MB).

- [ ] **Step 3: Verify the container starts and /health responds**

```bash
docker run -d --network=host --memory=512m --tmpfs=/tmp:size=64m --tmpfs=/dev/shm:size=256m \
  -e SKILL_INPUT='{"panels":["weather"]}' \
  -e DASHBOARD_CONFIG='{"stock_tickers":["AAPL"]}' \
  -e WEATHER_LOCATION="Burlington,VT" \
  --name dashboard-test \
  miniclaw/dashboard:latest

# Wait for Flask to start
sleep 3

# Check health
curl -s http://localhost:7860/health
# Expected: {"status":"ok"}

# Check weather renders
curl -s http://localhost:7860/ | grep -i "weather"
# Expected: HTML containing weather panel markup

# Clean up
docker stop dashboard-test && docker rm dashboard-test
```

- [ ] **Step 4: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add containers/dashboard/Dockerfile && git commit -m "feat: add dashboard container Dockerfile"
```

---

## Task 6: Dashboard Skill Files

**Files:**
- Create: `skills/dashboard/SKILL.md`
- Create: `skills/dashboard/config.yaml`

- [ ] **Step 1: Write `skills/dashboard/SKILL.md`**

```markdown
---
name: dashboard
description: Show a visual dashboard on the connected monitor, or close it. Displays news/OSINT, weather, stocks, and music.
---

# Dashboard Skill

## When to use

Use this skill when the user asks to:
- See a dashboard, display, or screen ("show me the dashboard", "pull up the display")
- View specific feeds ("show me the news", "throw up stocks and weather")
- Turn on audio/news visually ("show me what's happening in the world")
- Close or dismiss the dashboard ("close the display", "turn off the screen", "get rid of that")

Do NOT use this skill to play audio — use the soundcloud/audio skill for that.

## Inputs

```yaml
type: object
properties:
  action:
    type: string
    enum: [open, close]
    description: open to show the dashboard, close to dismiss it
  panels:
    type: array
    items:
      type: string
      enum: [news, weather, stocks, music]
    description: which panels to display — only required when action is open
  timeout_minutes:
    type: integer
    default: 10
    description: auto-close after this many minutes — only for action open
required:
  - action
```

## Panel selection

Infer panels from the user's request:
- "news" / "what's happening" / "OSINT" → `["news"]`
- "weather" → `["weather"]`
- "stocks" / "market" → `["stocks"]`
- "music" / "what's playing" → `["music"]`
- "dashboard" / "everything" / no specific panel mentioned → `["news", "weather", "stocks", "music"]`
- Combinations: "news and weather" → `["news", "weather"]`

## How to respond

- After opening: confirm what's on screen. Example: "Dashboard is up with news and weather."
- After closing: "Display closed."
- If no monitor is connected: relay the error naturally. Example: "I don't see a display connected."
- Keep responses short — the user is looking at the screen, not listening for detail.
```

- [ ] **Step 2: Write `skills/dashboard/config.yaml`**

```yaml
type: native
timeout_seconds: 60
```

- [ ] **Step 3: Verify skill loads**

```bash
cd /home/daedalus/linux/miniclaw && source .venv/bin/activate && python3 - <<'EOF'
from core.skill_loader import SkillLoader
loader = SkillLoader("skills")
skill = loader.skills.get("dashboard")
assert skill is not None, "dashboard skill not found"
assert skill.execution_config.get("type") == "native", "should be native"
print(f"PASS: dashboard skill loaded — {skill.name}: {skill.description}")
EOF
```

Expected: `PASS: dashboard skill loaded — dashboard: Show a visual dashboard...`

- [ ] **Step 4: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add skills/dashboard/ && git commit -m "feat: add dashboard skill definition (SKILL.md + config.yaml)"
```

---

## Task 7: Dashboard Native Handler in ContainerManager

This is the host-side lifecycle manager: starts the detached Flask container, launches Chromium in kiosk mode, manages the lock file, and runs the auto-close timer.

**Files:**
- Modify: `core/container_manager.py`

- [ ] **Step 1: Add imports and module-level constants**

At the top of `core/container_manager.py`, add to the existing imports:

```python
import signal
import threading
import urllib.request
```

After the `logger = ...` and `REPO_ROOT = ...` lines, add:

```python
DASHBOARD_PORT = 7860
DASHBOARD_LOCK = Path.home() / ".miniclaw" / "dashboard.lock"
```

- [ ] **Step 2: Add `_dashboard_timer` instance variable and register handler**

In `__init__`, add `self._dashboard_timer: threading.Timer | None = None` after the existing instance variables, and register the handler:

```python
    def __init__(self, memory_limit: str = DEFAULT_MEMORY_LIMIT):
        self.memory_limit = memory_limit
        self._meta_skill_executor = None
        self._orchestrator = None
        self.docker_available = False
        self.docker_error = None
        self._dashboard_timer: threading.Timer | None = None   # ← ADD
        self._native_handlers = {
            "install_skill": self._execute_install_skill,
            "set_env_var": self._execute_set_env_var,
            "save_memory": self._execute_save_memory,
            "dashboard": self._execute_dashboard,             # ← ADD
        }
        self._verify_docker()
```

- [ ] **Step 3: Add `_find_chromium` helper**

Add this method to `ContainerManager` (before `_collect_env_vars`):

```python
    def _find_chromium(self) -> str | None:
        """Return the path to the first Chromium binary found on PATH, or None."""
        for name in ["chromium-browser", "chromium", "google-chrome-stable", "google-chrome"]:
            result = subprocess.run(["which", name], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
        return None
```

- [ ] **Step 4: Add `_cleanup_dashboard_lock` helper**

```python
    def _cleanup_dashboard_lock(self, lock: dict) -> None:
        """Kill host Chromium and stop the Docker container from a lock dict."""
        chromium_pid = lock.get("chromium_pid")
        container_id = lock.get("container_id")
        if chromium_pid:
            try:
                os.kill(chromium_pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        if container_id:
            try:
                subprocess.run(
                    ["docker", "stop", container_id],
                    capture_output=True,
                    timeout=15,
                )
            except Exception:
                pass
```

- [ ] **Step 5: Add `_close_dashboard_internal`**

```python
    def _close_dashboard_internal(self) -> None:
        """Called by the auto-timeout timer. Closes dashboard without returning a value."""
        if DASHBOARD_LOCK.exists():
            try:
                lock = json.loads(DASHBOARD_LOCK.read_text())
                self._cleanup_dashboard_lock(lock)
            except Exception:
                logger.exception("Error during dashboard auto-close")
            DASHBOARD_LOCK.unlink(missing_ok=True)
        self._dashboard_timer = None
        logger.info("Dashboard auto-closed by timeout")
```

- [ ] **Step 6: Add `_close_dashboard`**

```python
    def _close_dashboard(self) -> str:
        """Close the dashboard: kill Chromium, stop container, cancel timer."""
        if not DASHBOARD_LOCK.exists():
            return "No dashboard is currently open."
        try:
            lock = json.loads(DASHBOARD_LOCK.read_text())
            self._cleanup_dashboard_lock(lock)
            DASHBOARD_LOCK.unlink(missing_ok=True)
        except Exception as exc:
            logger.exception("Error closing dashboard")
            return f"Error closing dashboard: {exc}"
        if self._dashboard_timer:
            self._dashboard_timer.cancel()
            self._dashboard_timer = None
        return "Display closed."
```

- [ ] **Step 7: Add `_open_dashboard`**

```python
    def _open_dashboard(self, panels: list, timeout_minutes: int) -> str:
        """Start the dashboard container + host Chromium, write lock, start timer."""
        # --- Handle already-running dashboard ---
        if DASHBOARD_LOCK.exists():
            try:
                lock = json.loads(DASHBOARD_LOCK.read_text())
                os.kill(lock["chromium_pid"], 0)  # signal 0 = existence check
                # Still running — update panels
                panels_str = ",".join(panels)
                url = f"http://localhost:{lock['port']}/refresh?panels={panels_str}"
                urllib.request.urlopen(url, timeout=5)
                return f"Dashboard updated with {', '.join(panels)}."
            except ProcessLookupError:
                # Chromium died — stale lock, clean up and relaunch
                try:
                    self._cleanup_dashboard_lock(json.loads(DASHBOARD_LOCK.read_text()))
                except Exception:
                    pass
                DASHBOARD_LOCK.unlink(missing_ok=True)
            except Exception:
                DASHBOARD_LOCK.unlink(missing_ok=True)

        if not self.docker_available:
            return f"Dashboard unavailable: {self.docker_error or 'Docker is not running'}."

        # Ensure ~/.miniclaw exists (volume mount target)
        miniclaw_dir = Path.home() / ".miniclaw"
        miniclaw_dir.mkdir(parents=True, exist_ok=True)

        dashboard_config = json.dumps({
            "news_accounts": ["OSINTDefender"],
            "stock_tickers": ["AAPL", "TSLA", "NVDA"],
        })
        weather_loc = os.environ.get("WEATHER_LOCATION", "New York,NY")

        # --- Start Flask container (detached) ---
        docker_cmd = [
            "docker", "run", "-d",
            "--network=host",
            "--memory=512m",
            "--cpus=1.5",
            "--security-opt=no-new-privileges",
            "--tmpfs=/tmp:size=64m",
            "--tmpfs=/dev/shm:size=256m",
            "-v", f"{miniclaw_dir}:/miniclaw",
            "-e", f"SKILL_INPUT={json.dumps({'panels': panels, 'timeout_minutes': timeout_minutes})}",
            "-e", f"DASHBOARD_CONFIG={dashboard_config}",
            "-e", f"WEATHER_LOCATION={weather_loc}",
            "miniclaw/dashboard:latest",
        ]
        result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return f"Failed to start dashboard: {result.stderr.strip()[:300]}"

        container_id = result.stdout.strip()

        # --- Wait for Flask to be ready (up to 10s) ---
        for _ in range(20):
            try:
                urllib.request.urlopen(
                    f"http://localhost:{DASHBOARD_PORT}/health", timeout=1
                )
                break
            except Exception:
                time.sleep(0.5)
        else:
            subprocess.run(["docker", "stop", container_id], capture_output=True, timeout=10)
            return "Dashboard container started but server did not respond."

        # --- Launch Chromium on host in kiosk mode ---
        chromium = self._find_chromium()
        if not chromium:
            subprocess.run(["docker", "stop", container_id], capture_output=True, timeout=10)
            return "I don't see a display connected or Chromium is not installed."

        try:
            proc = subprocess.Popen(
                [chromium, "--kiosk", "--noerrdialogs",
                 "--disable-infobars", f"http://localhost:{DASHBOARD_PORT}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            subprocess.run(["docker", "stop", container_id], capture_output=True, timeout=10)
            return f"Failed to launch display: {exc}"

        # --- Write lock file ---
        DASHBOARD_LOCK.write_text(json.dumps({
            "chromium_pid": proc.pid,
            "container_id": container_id,
            "port": DASHBOARD_PORT,
        }))

        # --- Start auto-close timer ---
        if self._dashboard_timer:
            self._dashboard_timer.cancel()
        self._dashboard_timer = threading.Timer(
            timeout_minutes * 60, self._close_dashboard_internal
        )
        self._dashboard_timer.daemon = True
        self._dashboard_timer.start()

        panel_list = ", ".join(panels) if panels else "all panels"
        return f"Dashboard is up with {panel_list}."

```

- [ ] **Step 8: Add `_execute_dashboard` router**

```python
    def _execute_dashboard(self, tool_input: dict) -> str:
        """Route open/close dashboard actions."""
        action = str(tool_input.get("action", "")).strip().lower()
        if action == "open":
            panels = tool_input.get("panels", ["news", "weather", "stocks", "music"])
            timeout_minutes = int(tool_input.get("timeout_minutes", 10))
            return self._open_dashboard(panels, timeout_minutes)
        if action == "close":
            return self._close_dashboard()
        return f"Unknown dashboard action '{action}'. Use 'open' or 'close'."
```

- [ ] **Step 9: Verify handler is wired up (no Chromium/Docker needed)**

```bash
cd /home/daedalus/linux/miniclaw && source .venv/bin/activate && python3 - <<'EOF'
from core.container_manager import ContainerManager
cm = ContainerManager()
assert "dashboard" in cm._native_handlers, "dashboard handler not registered"
# Test close with no lock file returns gracefully
from pathlib import Path
lock = Path.home() / ".miniclaw" / "dashboard.lock"
lock.unlink(missing_ok=True)
result = cm._execute_dashboard({"action": "close"})
assert "No dashboard" in result, f"Unexpected: {result}"
# Test unknown action
result = cm._execute_dashboard({"action": "bogus"})
assert "Unknown" in result, f"Unexpected: {result}"
print("PASS: dashboard handler wired and routes correctly")
EOF
```

Expected: `PASS: dashboard handler wired and routes correctly`

- [ ] **Step 10: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add core/container_manager.py && git commit -m "feat: add dashboard native handler (open/close, lock file, auto-timeout)"
```

---

## Task 8: End-to-End Manual Test

Verify the full flow with a connected monitor.

**Prerequisites:** Chromium installed on the Pi host (`sudo apt install chromium-browser`), a monitor connected via HDMI, MiniClaw running in text or voice mode.

- [ ] **Step 1: Ensure the dashboard image is built**

```bash
cd /home/daedalus/linux/miniclaw && ./run.sh --list
```

Expected: `dashboard` skill appears in the loaded skills list (it's native so no Docker image build needed for the skill itself, but verify no errors).

- [ ] **Step 2: Test open via text mode**

```bash
cd /home/daedalus/linux/miniclaw && ./run.sh
```

Then type: `show me the news and weather`

Expected:
- A Chromium window opens in kiosk mode on the connected monitor
- The dashboard shows the news feed (left panel) and weather widget (right sidebar)
- Claude responds: "Dashboard is up with news, weather." (or similar)

- [ ] **Step 3: Test panel update**

While the dashboard is open, type: `also show me stocks`

Expected:
- Claude calls `dashboard` with `action=open, panels=["news","weather","stocks"]`
- The browser reloads within 2 seconds showing the stocks widget added to the sidebar
- Claude responds: "Dashboard updated with news, weather, stocks."

- [ ] **Step 4: Test close**

Type: `close the display`

Expected:
- Chromium closes
- The container stops
- `~/.miniclaw/dashboard.lock` is removed
- Claude responds: "Display closed."

- [ ] **Step 5: Test auto-timeout**

Type: `show me the dashboard for 1 minute`

Expected: Chromium opens, then closes automatically after ~60 seconds with no user action.

- [ ] **Step 6: Final commit — update memory**

If MiniClaw memory is running, ask the assistant to save a note that the dashboard skill is implemented. Otherwise just confirm you're done.

---

## Notes for Future Work

- **Twitter/X scraping fragility:** If Twitter changes its DOM, the `[data-testid="tweetText"]` selector will break. The fallback is RSS — many OSINT accounts (including OSINTDefender) post to Nitter instances or RSS aggregators. Add an RSS fallback to `fetch_news` when the Playwright approach yields 0 results.
- **Stock watchlist configuration:** Currently hardcoded to `["AAPL", "TSLA", "NVDA"]`. Add a `DASHBOARD_STOCKS` env var (comma-separated tickers) and read it in `_open_dashboard`.
- **Display environment:** On Pi with no `$DISPLAY` set, Chromium will fail silently. The `_open_dashboard` handler catches the `Popen` exception, but if Chromium crashes immediately, the lock file will be written with a dead PID. A 1-second `proc.poll()` check after launch would catch this case.
