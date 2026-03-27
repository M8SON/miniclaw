---
name: tell_joke
description: Tell a random joke when the user asks for one.
version: "1.0.0"
metadata:
  openclaw:
    emoji: 😄
    requires: {}
---

# Joke Skill

## When to use
Use this skill when the user asks for a joke, something funny, or wants to be entertained.

## Inputs

```yaml
type: object
properties:
  topic:
    type: string
    description: Optional topic for the joke (e.g., 'programming', 'animals'). Leave blank for a random joke.
required: []
```

## How to respond
Tell the joke naturally. Deliver the punchline with good timing.
If a topic was given, try to keep the joke relevant to it.
