---
name: install-skill
description: Install a new skill by describing what it should do. Claude Code will
  write the skill files, validate them, and walk the user through a three-step voice
  confirmation before building and loading.
---
# Install Skill

## When to use
Use this skill when the user wants to add new capabilities to the assistant, such as:
- "add a new skill"
- "install a skill that can..."
- "teach yourself to..."
- "create a skill for..."
- "can you learn to..."

Do NOT use this for general questions or tasks the assistant can already do.

## Inputs

```yaml
type: object
properties:
  description:
    type: string
    description: >
      Plain English description of what the new skill should do, including
      any external services or API keys it might need.
required:
  - description
```

## How to respond
Tell the user you are going to write the skill files and that you will walk
them through confirmation steps before anything is built or installed.
Use short spoken sentences. Do not use markdown.
