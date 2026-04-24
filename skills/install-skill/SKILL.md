---
name: install-skill
description: Install a new skill — either author from scratch via Claude Code, or
  install an existing agentskills.io-compliant skill from a URL or filesystem path.
  All installs go through three voice confirmation gates before the skill is built
  and loaded.
---
# Install Skill

## When to use

**Author from scratch** — call with `description` when the user asks for a capability
that doesn't exist yet:
- "add a skill that does X"
- "install a skill to X"
- "teach yourself to X"
- "create a skill for X"

**Install existing** — call with `source` when the user references a URL, repo, or path
to an agentskills.io-format skill:
- "install the pdf-tools skill from github dot com slash foo slash bar"
- "install the skill at this URL: ..."
- "import the skill from ..."

Do NOT use this for general questions or tasks the assistant can already do.

## Inputs

```yaml
type: object
properties:
  description:
    type: string
    description: >
      Plain English description of what the new skill should do, including
      any external services or API keys it might need. Used only when
      authoring a new skill from scratch.
  source:
    type: string
    description: >
      URL (https://...) or filesystem path pointing at an existing
      agentskills.io-format skill directory. If provided, the skill is
      fetched, validated, and installed through the same three-gate
      confirmation flow — no authoring via Claude Code.
```

Exactly one of `description` or `source` should be provided. `source` takes precedence
when both are present.

## How to respond

For both flows: tell the user what will happen and that you will walk them through
confirmation steps before anything is built or installed. Use short spoken sentences.
Do not use markdown.
