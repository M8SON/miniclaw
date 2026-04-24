---
name: save-memory
description: Save something to long-term memory so it can be recalled in future conversations.
---
# Save Memory

## What is worth saving
Save things that would be useful to recall in a future session:
- Stated preferences ("I prefer temperatures in Celsius")
- Ongoing projects or goals Mason has described
- Facts about Mason's life or work he has mentioned
- Anything Mason explicitly asks you to remember

You do not need to wait for an explicit "remember this" command if the information is clearly durable and useful across future conversations.

Do not save passing remarks, one-off requests, or things that will not matter next session.

## What to save
Extract the core fact or preference worth remembering. Keep it concise. For example:
- "My wife's name is Sarah"
- "User prefers temperatures in Celsius"
- "The garage door code is 1234"
- "User is working on MiniClaw routing reliability"

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
If you saved something proactively, keep the acknowledgement minimal and natural.
