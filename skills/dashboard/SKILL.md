---
name: dashboard
description: Show a visual dashboard on the connected monitor, or close it. Displays news, weather, stocks, and music.
---

# Dashboard Skill

## When to use

Use this skill when the user asks to:
- See a dashboard, display, or screen ("show me the dashboard", "pull up the display")
- View specific feeds ("show me the news", "throw up stocks and weather")
- Update or change the current dashboard ("switch to conflict news", "update my news feed")
- Close or dismiss the dashboard ("close the display", "turn off the screen")

Do NOT use this skill to play audio — use the soundcloud/audio skill for that.

## Before opening — check memory first

Before asking the user anything, check remembered context for:
- **Location** — look for a saved city, location, or hometown. If found, use it silently.
- **News preferences** — look for saved dashboard preferences (e.g. "likes local + conflict news"). If found, use them silently.

Only ask if the information is not in memory.

## Gathering preferences

### Location
If location is not in memory and the user's request involves weather or local news, ask:
> "What city are you in? I'll remember it for next time."

After they answer, use `save_memory` to store it (topic: "location", e.g. "Mason is in Burlington, Vermont").

### News preferences
If news is in the requested panels and preferences are not in memory and the user's request doesn't already specify what they want (e.g. "show me conflict news" is already clear), ask:
> "What kind of news do you want on the dashboard? I can show local news for your city, world news, OSINT and conflict monitoring, or a mix."

After they answer, use `save_memory` to store it (topic: "dashboard news preferences").

If the user says "update my news feed" or "change my news preferences", ask the question above regardless of what's in memory, then update the saved memory.

## Building the news config

Based on location and preferences, set:

**`news_sources`** — which RSS feed groups to include:
- `"osint"` — Bellingcat, The War Zone (investigative + defense)
- `"world"` — Al Jazeera (international)
- `"local_vt"` — VTDigger, Seven Days (Vermont/Burlington only)

**`gdelt_queries`** — GDELT search queries (free, no API key, great for local + topic-based news):
- For local news: use the user's city + state, e.g. `"Burlington Vermont"`
- For conflict/geopolitics: `"conflict military geopolitics"`
- For climate/environment: `"climate environment"`
- For any topic the user requests: build a natural query string for it

Always include a location-based GDELT query when you know the user's city. Add topic queries based on preferences.

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
    description: which panels to display
  location:
    type: string
    description: city name for weather and local GDELT query (e.g. "Burlington")
  news_sources:
    type: array
    items:
      enum: [osint, world, local_vt]
    description: which RSS feed groups to include in the news panel
  gdelt_queries:
    type: array
    items:
      type: string
    description: GDELT search query strings — build from user's location + interests
  timeout_minutes:
    type: integer
    default: 10
required:
  - action
```

## Panel selection

Infer panels from the user's request:
- "news" / "what's happening" / "OSINT" / "headlines" → `["news"]`
- "weather" → `["weather"]`
- "stocks" / "market" → `["stocks"]`
- "music" / "what's playing" → `["music"]`
- "dashboard" / "everything" / no specific panel → `["news", "weather", "stocks", "music"]`
- Combinations: "news and weather" → `["news", "weather"]`

## How to respond

- After opening: confirm what's on screen. Example: "Dashboard is up with local Burlington news, conflict feeds, and weather."
- After closing: "Display closed."
- Keep responses short — the user is looking at the screen, not listening for detail.
