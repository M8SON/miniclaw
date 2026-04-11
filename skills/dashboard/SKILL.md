---
name: dashboard
description: Show a visual dashboard on the connected monitor, or close it. Displays news, weather, stocks, and music.
---

# Dashboard Skill

## When to use

- "show me the dashboard", "pull up the display", "open the screen"
- "show me the news / weather / stocks / what's playing"
- "switch to conflict news", "update my news feed", "show me Middle East news"
- "close the display", "turn off the screen"

Do NOT use this skill to play audio — use the soundcloud skill.

## Before calling

Check memory for location and news preferences. Use them silently if found. Ask only if missing.

- **Location missing + weather or local news requested:** ask what city they're in, then save with `save_memory` (topic: "location").
- **News preferences missing + not specified in request:** ask what kind of news (local, world, OSINT/conflict, or a mix), then save with `save_memory` (topic: "dashboard news preferences"). Skip if request already specifies topic.
- If user says "update my news feed", ask regardless of memory.

## Building the news config

**`news_sources`** — RSS feed groups:
- `"osint"` — Bellingcat, The War Zone
- `"world"` — Al Jazeera
- `"local_vt"` — VTDigger, Seven Days (Burlington/Vermont only)

**`gdelt_queries`** — free dynamic news queries. Always include a location query when city is known. Add topic queries from preferences or request.

- Burlington, VT → `"Burlington Vermont"`
- Conflict/geopolitics → `"conflict military geopolitics"`
- Climate → `"climate environment"`
- User specifies a topic → build a precise query string for it

## Inputs

```yaml
type: object
properties:
  action:
    type: string
    enum: [open, close]
  panels:
    type: array
    items:
      enum: [news, weather, stocks, music]
  location:
    type: string
    description: city name for weather and local GDELT query
  news_sources:
    type: array
    items:
      enum: [osint, world, local_vt]
  gdelt_queries:
    type: array
    items:
      type: string
  timeout_minutes:
    type: integer
    default: 10
required:
  - action
```

## Panel selection

- news / headlines / what's happening → `["news"]`
- weather → `["weather"]`
- stocks / market → `["stocks"]`
- music / what's playing → `["music"]`
- dashboard / everything / unspecified → `["news", "weather", "stocks", "music"]`
- Combinations: "news and weather" → `["news", "weather"]`

## Live topic updates

If the dashboard is open, `action: "open"` with new parameters updates content in place.

- "show me Middle East news" → `gdelt_queries: ["Middle East conflict news"]` (no `news_sources`)
- "switch to local news" → `gdelt_queries: ["Burlington Vermont"]`, `news_sources: ["local_vt"]`
- "show me Toyota news" → `gdelt_queries: ["Toyota new model 2026"]` (no `news_sources`)

**Rule:** Include `news_sources` only when the user wants a named feed category. For topic-specific updates, omit `news_sources` — this clears RSS so only on-topic results appear. If the user wants both named feeds AND a custom topic, include both. Be specific with queries: "Toyota Camry 2026 hybrid" beats "Toyota news".

## How to respond

- Opening: "Dashboard is up with [content summary]."
- Live update: "Switched to [topic]."
- Closing: "Display closed."
Keep responses short — the user is looking at the screen, not listening for detail.
