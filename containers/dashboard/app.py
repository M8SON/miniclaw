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
import re
import time
import threading
import calendar
from datetime import datetime, timezone

import requests
from flask import Flask, render_template, request, jsonify

try:
    import feedparser
    FEEDPARSER_AVAILABLE = True
except ImportError:
    feedparser = None
    FEEDPARSER_AVAILABLE = False

try:
    from .eonet import build_priority_hazards, fetch_eonet_events
except ImportError:
    from eonet import build_priority_hazards, fetch_eonet_events

try:
    from .dashboard_defaults import (
        DEFAULT_HAZARD_CATEGORIES,
        DEFAULT_HAZARD_CONFIG,
        default_hazard_config,
    )
except ImportError:
    from dashboard_defaults import (
        DEFAULT_HAZARD_CATEGORIES,
        DEFAULT_HAZARD_CONFIG,
        default_hazard_config,
    )

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False


app = Flask(__name__)

DEFAULT_STOCK_TICKERS = ["AAPL", "TSLA", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "SPY"]

_state_lock = threading.Lock()
_state = {
    "panels": [],
    "needs_refresh": False,
    "rss_feeds": [],
    "gdelt_queries": [],
    "stock_tickers": list(DEFAULT_STOCK_TICKERS),
    "hazard_config": dict(DEFAULT_HAZARD_CONFIG),
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

NEWS_MAX_AGE_SECONDS = 60 * 60 * 72


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_rss_image(entry) -> str:
    """Extract the best available image URL from an RSS/Atom entry."""
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        return entry.media_thumbnail[0].get("url", "")
    if hasattr(entry, "media_content") and entry.media_content:
        url = entry.media_content[0].get("url", "")
        if url:
            return url
    if hasattr(entry, "enclosures") and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get("type", "").startswith("image/"):
                return enc.get("href", "")
    # Fall back to first <img> in summary/content
    for field in ("summary", "content"):
        body = ""
        if field == "content" and hasattr(entry, "content") and entry.content:
            body = entry.content[0].get("value", "")
        else:
            body = entry.get(field, "") or ""
        m = re.search(r'<img[^>]+src=["\']((https?://)[^"\']+)["\']', body)
        if m:
            return m.group(1)
    return ""


def _weathercode_icon(code: int) -> str:
    if code == 0:               return "☀️"
    if code in (1, 2):          return "🌤️"
    if code == 3:               return "☁️"
    if code in (45, 48):        return "🌫️"
    if code in (51, 53, 55):    return "🌦️"
    if code in (56, 57):        return "🌨️"
    if code in (61, 63, 65):    return "🌧️"
    if code in (66, 67):        return "🌨️"
    if code in (71, 73, 75, 77): return "❄️"
    if code in (80, 81, 82):    return "🌦️"
    if code in (85, 86):        return "❄️"
    if code in (95, 96, 99):    return "⛈️"
    return "🌡️"


def _rss_entry_timestamp(entry) -> float:
    """Return a best-effort UTC timestamp for an RSS/Atom entry."""
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = getattr(entry, field, None)
        if parsed:
            return float(calendar.timegm(parsed))
    return 0.0


def _gdelt_timestamp(article: dict) -> float:
    """Return a best-effort UTC timestamp for a GDELT article."""
    raw = (article.get("seendate") or article.get("date") or "").strip()
    if not raw:
        return 0.0

    formats = (
        "%Y%m%dT%H%M%SZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
    )
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return 0.0


def _focus_location() -> dict | None:
    location = os.environ.get("WEATHER_LOCATION", "").strip()
    if not location:
        return None

    city = location.split(",")[0].strip()
    if not city:
        return None

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
        return {
            "name": place.get("name", city),
            "lat": place["latitude"],
            "lon": place["longitude"],
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def fetch_rss(feeds: list) -> list:
    """Fetch headlines + images from RSS/Atom feeds."""
    if not FEEDPARSER_AVAILABLE:
        return []

    items = []
    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
            source = feed.feed.get("title", feed_url)
            for entry in feed.entries[:3]:
                title = entry.get("title", "").strip()
                if title:
                    items.append({
                        "source": source,
                        "text": title,
                        "image_url": _extract_rss_image(entry),
                        "timestamp": _rss_entry_timestamp(entry),
                    })
        except Exception:
            pass
    return items


def fetch_gdelt(queries: list) -> list:
    """Fetch headlines + social images from GDELT v2 Doc API."""
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
                image_url = article.get("socialimage", "")
                if title and title not in seen:
                    seen.add(title)
                    items.append({
                        "source": domain,
                        "text": title,
                        "image_url": image_url,
                        "timestamp": _gdelt_timestamp(article),
                    })
        except Exception:
            pass
    return items


def fetch_news(rss_feeds: list, gdelt_queries: list) -> list:
    """Combine RSS and GDELT headlines, deduplicated and sorted by recency."""
    seen = set()
    items = []
    now = time.time()

    for item in fetch_rss(rss_feeds) + fetch_gdelt(gdelt_queries):
        key = item["text"].lower()[:60]
        if key not in seen:
            seen.add(key)
            items.append(item)

    fresh_items = []
    stale_items = []
    for item in items:
        ts = float(item.get("timestamp") or 0)
        if ts and now - ts <= NEWS_MAX_AGE_SECONDS:
            fresh_items.append(item)
        else:
            stale_items.append(item)

    sorted_items = sorted(
        fresh_items,
        key=lambda item: (float(item.get("timestamp") or 0), item["text"].lower()),
        reverse=True,
    )

    # If every source is missing timestamps or returns only older entries,
    # allow a small fallback set so the board does not go empty.
    if not sorted_items:
        sorted_items = sorted(
            stale_items,
            key=lambda item: (float(item.get("timestamp") or 0), item["text"].lower()),
            reverse=True,
        )

    cleaned = []
    for item in sorted_items[:24]:
        cleaned.append(
            {
                "source": item["source"],
                "text": item["text"],
                "image_url": item.get("image_url", ""),
            }
        )
    return cleaned


def fetch_priority_hazards(hazard_cfg: dict) -> list:
    focus_location = _focus_location()
    raw_events = fetch_eonet_events(hazard_cfg)
    return build_priority_hazards(raw_events, hazard_cfg, focus_location)


def fetch_weather() -> dict:
    """Fetch current weather from open-meteo (free, no API key)."""
    location = os.environ.get("WEATHER_LOCATION", "New York,NY")
    try:
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
        code = int(current.get("weathercode", 0))

        return {
            "temp": f"{round(current.get('temperature_2m', 0))}",
            "icon": _weathercode_icon(code),
            "tonight_low": f"{round(mins[0])}" if mins else "N/A",
            "tomorrow_high": f"{round(maxs[1])}" if len(maxs) > 1 else "N/A",
            "location": place.get("name", location),
        }
    except Exception as exc:
        return {"error": str(exc)}


def fetch_stocks(tickers: list) -> list:
    """Fetch stock price and daily change via yfinance."""
    if not YFINANCE_AVAILABLE:
        return [{"ticker": t, "price": "N/A", "change": "N/A", "pct": 0, "positive": False} for t in tickers]
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
                "pct": round(pct, 1),
                "positive": pct >= 0,
            })
        except Exception:
            results.append({"ticker": ticker, "price": "N/A", "change": "N/A", "pct": 0, "positive": False})
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


@app.route("/music")
def music_status():
    return jsonify(fetch_music())


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
    gdelt_str = request.args.get("gdelt_queries", "")
    sources_str = request.args.get("news_sources", "")

    rss_source_map = {
        "osint":    ["https://bellingcat.com/feed/", "https://www.twz.com/rss"],
        "world":    ["https://www.aljazeera.com/xml/rss/all.xml"],
        "local_vt": ["https://vtdigger.org/feed/", "https://www.sevendaysvt.com/rss"],
    }

    with _state_lock:
        if panels_str:
            _state["panels"] = [p.strip() for p in panels_str.split(",") if p.strip()]
            enabled = "news" in _state["panels"]
            current_hazard_cfg = _state.get("hazard_config", DEFAULT_HAZARD_CONFIG)
            merged_hazard_cfg = dict(default_hazard_config(enabled=enabled))
            merged_hazard_cfg.update(current_hazard_cfg)
            merged_hazard_cfg["categories"] = list(merged_hazard_cfg.get("categories", DEFAULT_HAZARD_CATEGORIES))
            merged_hazard_cfg["enabled"] = enabled
            _state["hazard_config"] = merged_hazard_cfg
        if gdelt_str:
            _state["gdelt_queries"] = [q.strip() for q in gdelt_str.split("|") if q.strip()]
            # Topic-specific update: clear RSS so only on-topic GDELT results show.
            # RSS feeds are restored when news_sources is explicitly provided.
            if not sources_str:
                _state["rss_feeds"] = []
        if sources_str:
            sources = [s.strip() for s in sources_str.split(",") if s.strip()]
            feeds = []
            for src in sources:
                feeds.extend(rss_source_map.get(src, []))
            _state["rss_feeds"] = feeds
        _state["needs_refresh"] = True

    return jsonify({"status": "ok"})


@app.route("/")
def index():
    with _state_lock:
        panels = list(_state["panels"])
        rss_feeds = list(_state["rss_feeds"])
        gdelt_queries = list(_state["gdelt_queries"])
        stock_tickers = list(_state["stock_tickers"])
        hazard_config = dict(_state.get("hazard_config", default_hazard_config()))

    data = {}
    if "news" in panels:
        data["priority_hazards"] = fetch_priority_hazards(hazard_config)
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
        _state["stock_tickers"] = cfg.get("stock_tickers", list(DEFAULT_STOCK_TICKERS))
        hazard_cfg = dict(default_hazard_config(enabled="news" in _state["panels"]))
        hazard_cfg.update(cfg.get("hazards", {}))
        hazard_cfg["categories"] = list(hazard_cfg.get("categories", DEFAULT_HAZARD_CATEGORIES))
        _state["hazard_config"] = hazard_cfg

    app.run(host="0.0.0.0", port=7860, debug=False, threaded=True)


if __name__ == "__main__":
    main()
