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
