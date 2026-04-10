---
name: get_weather
description: Get current weather information for a specific location
---

# Weather Skill

## When to use
Use this skill when the user asks about weather, temperature, or 
conditions in a specific location.

## Inputs

```yaml
type: object
properties:
  query:
    type: string
    description: City name or location (e.g., 'London', 'New York', 'Burlington VT')
required:
  - query
```

## How to respond
Summarize the weather conversationally. Include temperature, conditions,
humidity, and wind speed. Keep it brief for spoken delivery.

Example: "It's currently 72 degrees and sunny in Burlington with light winds."
