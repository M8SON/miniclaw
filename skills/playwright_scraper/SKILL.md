---
name: scrape_webpage
description: Scrape and extract text content from a webpage, including sites with anti-bot protection that block simple HTTP requests
---

# Playwright Scraper Skill

## When to use
Use this skill when the user asks you to read, summarise, or extract information
from a specific URL — especially when the site uses JavaScript rendering or
anti-bot protection that would block a plain HTTP request.

Do NOT use this for general web searches. Use search_web for that.

## Inputs

```yaml
type: object
properties:
  url:
    type: string
    description: The full URL to scrape (must start with http:// or https://)
  instruction:
    type: string
    description: Optional — what to extract or focus on (e.g. "get the price", "summarise the article")
required:
  - url
```

## How to respond
Read the returned text and answer the user's question based on it.
Keep the response concise and spoken-friendly — no markdown, no bullet lists.
