# Self-Improving Skills — Design Spec

**Date:** 2026-04-24
**Roadmap item:** Hermes-Inspired Enhancement #4 (`Self-improving skills`)
**Builds on:** `2026-04-23-agentskills-compat-design.md`, which scaffolded the frontmatter flag.
**Status:** Design approved; implementation plan to follow.

## Goal

Skills accumulate routing coverage over time without user intervention. When Claude observes a successful skill invocation on a phrasing the SKILL.md doesn't currently mention, or notices a routing miss it corrected mid-turn, it autonomously adds the new phrasing to that skill's SKILL.md as an additive routing hint. Each change is reversible via `git revert`.

The user's manual paths — editing skills via Claude Code in another session, or using `install-skill` to author new ones — remain unchanged. This system handles the steady-state "skill learns new phrasings" use case.

## Constraints already locked from roadmap #3

These are non-negotiable per the agentskills.io compat spec:

- Mechanism gated by frontmatter: `metadata.miniclaw.self_update.allow_body: true` (default false, opt-in).
- Rewrite is body-only — no frontmatter changes, no new input schema sections, no Dockerfile / config.yaml / `scripts/` modifications.
- Rewritten file must pass `SkillValidator.validate_markdown`.
- Native skill name: `update_skill_hints`. API shape: `{skill_name, addition, rationale}`.

## Non-goals

- **Auto skill creation.** `install-skill` (voice / CLI / URL import) already covers explicit creation. Auto-creation has different risk shape and is not in scope here.
- **Tier 2/3 changes.** Reword existing routing hints, replace whole sections, or remove existing examples. These are higher-risk and remain manual via Claude Code or a future explicit invocation.
- **Confirmation gates.** The product value of self-improvement is autonomy; gating each update would make it slower than the user typing the change in Claude Code. Safety shifts to *bound the surface area + make every change reversible*.
- **Batch / scheduled review.** A periodic pattern review pass over MiniClaw's `SessionArchive` is a reasonable v2 feature. v1 is in-process detection only.

## Per-tier eligibility

Tier inference comes from the loader (already specced in roadmap #3). Eligibility for self-update:

| Tier | Eligible? |
|---|---|
| `bundled` | Yes, if frontmatter sets `allow_body: true` |
| `authored` | Yes, if frontmatter sets `allow_body: true` |
| `imported` | **No, regardless of frontmatter.** Treated as `false`. Rationale: imported tier is supply-chain untrusted; a community skill claiming `allow_body: true` would let it modify itself post-install, defeating the install-time review. |
| `dev` | Yes (you're iterating; mutation is fine) |

Bundled skills opt in selectively. Specifically these stay opt-out because their routing is security-relevant: `install-skill`, `set-env-var`, `save-memory`. Routing changes there could redirect security-gated flows to the wrong handler.

## The `update_skill_hints` native skill

A new bundled native skill, registered in `container_manager._native_handlers`. Implementation lives in a new module `core/skill_self_update.py` to keep the handler thin.

### Tool definition (input schema)

```yaml
type: object
properties:
  skill_name:
    type: string
    description: Kebab-case name of the skill whose SKILL.md should grow a routing hint.
  addition:
    type: string
    description: |
      The new routing hint to append. A short markdown bullet, typically a
      phrasing example or a clarifying note. Must be additive — do not
      include section restructuring, frontmatter, or input schema changes.
  rationale:
    type: string
    description: One sentence explaining why this addition is warranted (logged for audit).
required: [skill_name, addition, rationale]
```

### Behavior pipeline

```
1. Look up skill_name in skill_loader.skills.
   Reject if missing, if tier == imported, or if frontmatter
   metadata.miniclaw.self_update.allow_body is not exactly True.

2. Sanity-check the addition:
   - len(addition.strip()) between 1 and 500 chars.
   - No frontmatter delimiter ("---" at line start).
   - No "## Inputs" / "## Parameters" / "## Input Schema" headers.
   - No top-level (#) headings.
   - No HTML / script tags.

3. Read current SKILL.md. If addition.strip() already appears verbatim
   in the body, return {"status": "no-op", "reason": "already covered"}.

4. Locate or create the "## Auto-learned routing hints" section near the
   end of the body. Append the addition as a markdown bullet. If the
   section already has 30 bullets, drop the oldest before appending (FIFO).

5. Build the proposed file content (frontmatter unchanged, body modified).

6. Run SkillValidator.validate_markdown(new_content, skill_dir).
   Any ValueError → return {"status": "rejected", "reason": str(e)}.
   No partial write.

7. Atomic write: write to a tempfile in the same dir, fsync, os.rename.

8. Git commit (path-restricted):
   git -C <repo_root> commit -- skills/<name>/SKILL.md \
       -m "self-update(<name>): <rationale>" \
       -m "added: <first 80 chars of addition>"
   On non-git or commit failure: log warning, continue.

9. orchestrator.reload_skills() so the next routing round uses the new hint.

10. Return {"status": "ok", "skill": "<name>", "added": "<truncated addition>"}.
```

### Why a dedicated `## Auto-learned routing hints` section

Keeps human-authored content separate from machine-authored content. Two operational benefits:

- Reviewing `git log --grep "self-update"` on the repo shows exactly what auto-update has done.
- Rolling back ALL auto-changes for one skill = delete the section, commit. Hand-written intent stays intact.

The orchestrator's prompt builder concatenates the whole body anyway, so Claude still sees auto-learned hints when routing.

## Trigger model

Two paths, both calling the same `update_skill_hints` tool:

### Path 1 — in-the-moment Claude judgment

System-prompt guidance instructs Claude to call the tool when it observes:

1. **Novel successful phrasing.** A user said something the skill's SKILL.md doesn't explicitly mention as a trigger phrase, and the skill ran cleanly with a useful result. Add the phrasing.
2. **Routing miss corrected.** Claude initially routed to skill X, the user clarified or the result didn't fit, and Claude re-routed to skill Y. After the request completes successfully via Y, add a hint to Y about the original phrasing.

### Path 2 — 15-tool-call checkpoint

The orchestrator's `ToolLoop` already counts tool calls per turn. When a tool call brings the count to a multiple of 15 within a turn, the orchestrator prepends a checkpoint nudge to the next system message:

```
[CHECKPOINT — N tool calls in this turn]
Step back briefly: in the calls so far, did any skill route on a phrasing
that isn't in its SKILL.md? Did you correct a misroute? If so, call
update_skill_hints now before continuing the user's request.
```

The nudge is suppressed entirely when no loaded skill has `allow_body: true` in its frontmatter — no eligible skill means the checkpoint has no purpose.

### System prompt guidance (added to PromptBuilder when at least one loaded skill has `allow_body: true`)

```
Self-improving skills are enabled. Use update_skill_hints when:

  1. NOVEL SUCCESSFUL PHRASING: a user said something the skill's
     SKILL.md doesn't mention as a trigger phrase, and the skill
     ran cleanly. Add the phrasing as an example.

  2. ROUTING MISS YOU CORRECTED: you initially called skill X, the
     user clarified or the result didn't fit, and you re-routed to
     skill Y. After the user's request is satisfied via skill Y,
     add a hint to Y about the original phrasing.

Constraints:

  - Additions are short markdown bullets (one line).
  - Only call update_skill_hints once per skill per turn.
  - If the phrasing is already covered by existing SKILL.md content,
    don't call — it would be a no-op.
  - Never call on bundled skills whose routing is security-relevant
    (install-skill, set-env-var, save-memory).
  - Provide a rationale field naming the user phrasing or pattern
    that motivated the addition, in 15 words or fewer.

When in doubt, don't call. Auto-learned hints accumulate; bad ones
take effort to clean up.
```

## Bounds and safety

Code-enforced (not just prompt guidance):

| Bound | Where | Effect |
|---|---|---|
| Tier eligibility | `update_skill_hints` step 1 | imported / non-`allow_body: true` rejected unconditionally |
| Addition length | step 2 | over 500 chars rejected |
| Structural patterns | step 2 | frontmatter delimiter, input-schema headers, top-level headings, html tags rejected |
| Phrasing already covered | step 3 | silent no-op, no write |
| FIFO cap on auto-section | step 4 | 30 bullets max; 31st pushes oldest out |
| Rate limit per turn | handler-level cache `(skill_name, turn_id)` | second call for same skill same turn → "rate-limited" return; the orchestrator already increments a turn counter that gets passed through |
| Validator on rewrite | step 6 | failure → no write, returns "rejected" |
| Atomic write | step 7 | tempfile + rename; partial-write impossible |

## Audit and rollback

**Audit = git history.** Every successful update ends with a path-restricted git commit. Subjects follow `self-update(<skill>): <rationale>`. Find them with `git log --grep "self-update"`.

**Rollback paths, increasing weight:**

1. **One specific change** — `git revert <commit>`.
2. **All auto-learned hints on one skill** — delete the `## Auto-learned routing hints` section in that SKILL.md, commit cleanup. Manual via editor or Claude Code.
3. **Disable self-update on one skill** — flip `metadata.miniclaw.self_update.allow_body: false` in frontmatter, commit. Stops auto-updates on next reload.

No new rollback CLI command. The existing tools are sufficient.

**Edge case: not a git repo.** If MiniClaw is running outside a git checkout, `git commit` fails. Handler logs a warning and returns success — the file change still applies; only the audit trail is missing. Self-update never blocks on git availability.

**Edge case: unstaged unrelated changes.** Path-restricted commit (`git commit -- skills/<name>/SKILL.md`) ensures user's in-progress edits aren't swallowed by the auto-commit.

## Comparison with Hermes Agent

Hermes is the inspiration for this roadmap. Key differences in our design vs. theirs:

| Aspect | Ours | Hermes |
|---|---|---|
| File format | agentskills.io | agentskills.io (same) |
| Auto-apply | yes | yes (same) |
| Trigger | Claude's judgment + 15-tool-call checkpoint | every-15-tool-call checkpoint + opportunistic |
| Scope | Tier 1 additive only | Updates AND new skill creation |
| Eligibility | Frontmatter opt-in | Always-on |
| Per-tier policy | Imported tier blocked | N/A |
| Validation | Pre-write structural checks + validator | None mentioned |
| Audit | Git commit per change | None mentioned |

We're more conservative than Hermes deliberately:
- Frontmatter opt-in protects security-relevant skills (`set-env-var`, etc.)
- Imported tier block protects against supply-chain abuse
- Tier 1 additive bound prevents skills from silently restructuring
- Git audit gives `git revert` as a one-command rollback

Auto-creation is not in scope for our v1 because `install-skill` already covers explicit creation through three voice gates — different trigger, same capability.

## Testing

Unit + one integration test. No real-LLM tests (whether Claude makes good calls is product-validated, not unit-testable).

### `tests/test_skill_self_update.py` — handler logic

- Skill not found → error, no write.
- Tier `imported` → rejected even with `allow_body: true`.
- Skill missing `allow_body: true` → rejected.
- Eligible tiers (`bundled`, `authored`, `dev`) with `allow_body: true` → accepted.
- Addition >500 chars → rejected.
- Addition with `---` → rejected.
- Addition with `## Inputs` / `## Parameters` / `## Input Schema` → rejected.
- Addition with `# heading` → rejected.
- Addition already present in body → silent no-op.
- Append creates `## Auto-learned routing hints` section if missing.
- Append adds bullet to existing section if present.
- 31st bullet → oldest dropped (FIFO at 30).
- Rate limit: second call same skill same turn → "rate-limited".
- Resulting file failing validator → no write, "rejected" return.
- Successful update writes atomically (tempfile + rename).

### `tests/test_skill_self_update_git.py` — git side-effects

Separate file; uses a tempdir-as-repo fixture.

- Successful update produces a commit with `self-update(<name>): <rationale>` subject.
- Commit touches only `skills/<name>/SKILL.md` path (verified via `git show --stat`).
- Non-git directory: handler returns success, file written, warning logged.
- Repo with staged unrelated changes elsewhere: those changes are NOT in the self-update commit.

### `tests/test_orchestrator_checkpoint.py` — 15-tool-call nudge

- Tool count crosses 15 → next system message contains the checkpoint nudge.
- Crosses 30 → nudge fires again.
- Counts between multiples (e.g. 7, 22) → no nudge.
- Counter resets at turn boundary.
- No skills with `allow_body: true` loaded → nudge suppressed entirely.

### Integration test (one)

Fixture skill with `allow_body: true`. Mock orchestrator runs a scripted conversation: skill called, `update_skill_hints` called with novel phrasing. Assert: SKILL.md gains the expected bullet, `git log` shows one new commit, `skill_loader.reload_skills()` was invoked, the new SKILL.md still loads cleanly through the validator.

## Future direction (deferred)

- **Batch / scheduled pattern review** — a periodic pass over MiniClaw's `SessionArchive` (FTS5) to find recurring misroute patterns Claude didn't catch in-the-moment. Reasonable v2 if real-world usage shows in-the-moment is missing patterns.
- **Tier 2 (rewording) auto-apply** — promote rewording from manual-only to auto-apply once Tier 1 has demonstrated stability over months of real use. Higher risk; not justified yet.
- **Cross-skill consolidation** — when multiple skills accumulate similar phrasings, suggest one canonical home. Pure speculation; revisit if it ever feels needed.
- **A `claude-recall`-style stats command for self-update activity** — `miniclaw skill self-update-stats` showing per-skill counts, last-updated timestamps, FIFO-evictions. Cheap to add but not needed in v1.
