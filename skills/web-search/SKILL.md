---
name: web-search
description: Search the web for current information, news, facts, or answers to questions
metadata:
  miniclaw:
    requires:
      env:
      - BRAVE_API_KEY
---
# Web Search Skill

## When to use
Use this skill when the user asks about current events, news, facts,
prices, scores, or anything that requires up-to-date information
beyond your training data.

## Inputs

```yaml
type: object
properties:
  query:
    type: string
    description: The search query (e.g., 'latest SpaceX launch', 'Bitcoin price today')
required:
  - query
```

## How to respond
Summarize the top results conversationally. Mention sources briefly
if the user might want to look them up. Keep it concise for spoken delivery.
