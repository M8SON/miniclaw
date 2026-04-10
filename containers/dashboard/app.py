"""
Dashboard skill container — serves a live dashboard page via Flask.

Routes:
  GET /              render the dashboard with current panels + fetched data
  GET /refresh       update active panels (panels=news,weather,...) then signal reload
  GET /poll          returns {"reload": true} once after /refresh, then {"reload": false}
  GET /health        liveness check
"""

import os
import json
import time
import threading

import feedparser
import requests
from flask import Flask, render_template, request, jsonify

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
    "rss_feeds": [],
    "gdelt_queries": [],
    "stock_tickers": ["AAPL", "TSLA", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "SPY"],
}

DEFAULT_RSS_FEEDS = [
    "https://vtdigger.org/feed/",
    "https://www.sevendaysvt.com/rss",
    "https://bellingcat.com/feed/",
    "https://www.twz.com/rss",
    "https://www.aljazeera.com/xml/rss/all.xml",
]

DEFAULT_GDELT_QUERIES = [
    "Burlington Vermont",
    "conflict military geopolitics",
]


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def fetch_rss(feeds: list) -> list:
    """Fetch headlines from RSS/Atom feeds."""
    items = []
    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
            source = feed.feed.get("title", feed_url)
            for entry in feed.entries[:3]:
                title = entry.get("title", "").strip()
                if title:
                    items.append({"source": source, "text": title})
        except Exception:
            pass
    return items


def fetch_gdelt(queries: list) -> list:
    """Fetch headlines from GDELT v2 Doc API."""
    items = []
    seen = set()
    for query in queries:
        try:
            resp = requests.get(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params={"query": query, "mode": "artlist", "maxrecords": 5, "format": "json"},
                timeout=10,
            )
            resp.raise_for_status()
            for article in resp.json().get("articles", []):
                title = article.get("title", "").strip()
                domain = article.get("domain", "")
                if title and title not in seen:
                    seen.add(title)
                    items.append({"source": domain, "text": title})
        except Exception:
            pass
    return items


def fetch_news(rss_feeds: list, gdelt_queries: list) -> list:
    """Combine RSS and GDELT headlines, deduplicated."""
    seen = set()
    items = []
    for item in fetch_rss(rss_feeds) + fetch_gdelt(gdelt_queries):
        key = item["text"].lower()[:60]
        if key not in seen:
            seen.add(key)
            items.append(item)
    return items[:20]


def fetch_weather() -> dict:
    """Fetch current weather from open-meteo (free, no API key)."""
    location = os.environ.get("WEATHER_LOCATION", "New York,NY")
    try:
        # open-meteo geocoding doesn't understand "City,State" — use city name only
        city = location.split(",")[0].strip()
        geo_resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1},
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
        with open("/miniclaw/now_playing.json") as f:
            raw = f.read()
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
        rss_feeds = list(_state["rss_feeds"])
        gdelt_queries = list(_state["gdelt_queries"])
        stock_tickers = list(_state["stock_tickers"])

    data = {}
    if "news" in panels:
        data["news"] = fetch_news(rss_feeds, gdelt_queries)
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
        _state["rss_feeds"] = cfg.get("rss_feeds", DEFAULT_RSS_FEEDS)
        _state["gdelt_queries"] = cfg.get("gdelt_queries", DEFAULT_GDELT_QUERIES)
        _state["stock_tickers"] = cfg.get("stock_tickers", ["AAPL", "TSLA", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "SPY"])

    app.run(host="0.0.0.0", port=7860, debug=False, threaded=True)


if __name__ == "__main__":
    main()
