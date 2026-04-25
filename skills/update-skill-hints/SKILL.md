---
name: update-skill-hints
description: Add an additive routing hint to a skill's SKILL.md so it learns
  from observed user phrasings. Use when you notice a skill was routed on a
  novel successful phrasing or when you corrected a misroute. Updates only
  skills with metadata.miniclaw.self_update.allow_body set to true.
metadata:
  miniclaw:
    self_update:
      allow_body: false
---

# Update Skill Hints

## When to use

Call this skill when you observe one of these patterns:

- NOVEL SUCCESSFUL PHRASING: a user said something the target skill's
  SKILL.md doesn't explicitly mention, the skill ran cleanly, and the
  result satisfied the user. Add the phrasing as an example.
- ROUTING MISS YOU CORRECTED: you initially routed to skill X, the user
  clarified or the result didn't fit, and re-routing to skill Y satisfied
  the request. After the request completes via Y, add a hint to Y about
  the original phrasing.

Do NOT use this for security-relevant skills (install-skill, set-env-var,
save-memory). Their routing must stay manually authored.

When in doubt, do not call. Auto-learned hints accumulate; bad ones are
work to clean up.

## Inputs

```yaml
type: object
properties:
  skill_name:
    type: string
    description: Kebab-case name of the skill to update (e.g. weather, web-search).
  addition:
    type: string
    description: |
      The new routing hint to append. A short markdown bullet (one line),
      typically a phrasing example. Must be additive — no section headers,
      no frontmatter, no input-schema modifications.
  rationale:
    type: string
    description: One sentence (15 words or fewer) explaining why this addition is warranted.
required:
  - skill_name
  - addition
  - rationale
```

## How to respond

After a successful call, the orchestrator will reload the skill and the new
hint takes effect on the next routing decision. Tell the user briefly that
you've updated the skill — short spoken sentence. After a rejection or
no-op, do not retry; continue the user's original request.
