---
name: save_memory
description: Save something to long-term memory so it can be recalled in future conversations.
---

# Save Memory

## When to use
Use this skill when the user explicitly asks you to remember something for next time. Trigger phrases include:
- "remember this", "remember that", "don't forget"
- "make a note", "note that", "keep that in mind"
- "remember for next time", "save that", "hold onto that"

Do NOT use this for things the user says in passing. Only save when they clearly intend for it to persist across conversations.

## What to save
Extract the core fact or preference the user wants remembered. Keep it concise. For example:
- "My wife's name is Sarah"
- "User prefers temperatures in Celsius"
- "The garage door code is 1234"

## Inputs

```yaml
type: object
properties:
  topic:
    type: string
    description: A short label for this memory, 3-5 words (e.g. "wife name", "temperature preference"). Used as the filename.
  content:
    type: string
    description: The information to remember, written as a clear factual statement.
required:
  - topic
  - content
```

## How to respond
After saving, confirm naturally. For example: "Got it, I'll remember that." Keep it short.
Do not read the content back unless the user asks.
