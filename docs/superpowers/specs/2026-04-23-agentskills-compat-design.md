# agentskills.io Compatibility — Design Spec

**Date:** 2026-04-23
**Roadmap item:** Hermes-Inspired Enhancement #3 (`agentskills.io compat`)
**Status:** Design approved; implementation plan to follow.

## Goal

MiniClaw skills become agentskills.io-spec-compliant, so they drop into Claude Code, Cursor, OpenCode, Goose, and other spec-compatible agents without modification. MiniClaw also accepts community skills from the agentskills.io ecosystem and OpenClaw-style sources, running them sandboxed through a single install pipeline.

Bidirectional compatibility — publishing and consumption — is the goal. OpenClaw compatibility falls out for free because OpenClaw uses the same `metadata.<vendor>.*` extension pattern the agentskills.io spec defines.

## Non-goals (this iteration)

- Registry discovery API — agentskills.io has no public registry API at time of writing.
- Signing / verification of imported skills — no mature standard yet in the ecosystem.
- `update_skill_hints` native skill implementation — scaffolding only; full behavior is roadmap item #4.
- Mobile HTTP entry point for install — future direction.

## Per-skill directory layout

Single-directory skills. The top-level `containers/` tree is deleted.

```
<skill-name>/
  SKILL.md              # frontmatter + instructions (agentskills.io-spec)
  config.yaml           # MiniClaw execution config (optional; sibling file)
  scripts/
    app.py              # container entrypoint (was containers/<name>/app.py)
    Dockerfile          # was containers/<name>/Dockerfile
    ...                 # extra support scripts
  references/           # optional, agentskills.io convention
  assets/               # optional, agentskills.io convention
  .install.json         # sidecar: provenance only (source URL, SHA256,
                        # installed_at, user_confirmed_env_passthrough).
                        # Written by the install pipeline; authored and
                        # imported tiers only.
```

Bundled skills (shipped in the repo) do not write `.install.json` — their provenance is git history.

## Trust tiers

Trust tier equals the directory a skill was loaded from. Policy is applied by the loader as a function of path, not from any field inside the skill directory. `.install.json` carries provenance (source URL, SHA256, install timestamp, confirmed env_passthrough) but never the trust tier.

```
skills/                         # bundled  — full trust; native execution allowed
~/.miniclaw/authored/           # authored — voice-installed via install_skill;
                                #            Docker-only; Dockerfile allowlist
~/.miniclaw/imported/           # imported — community-sourced;
                                #            Docker-only; allowlist + config clamps
```

`SkillLoader.DEFAULT_SEARCH_PATHS` is exactly these three directories. Name collisions across tiers are rejected at install time — no silent shadowing. Dev mode (below) is detected by symlink check, not by any file content.

## SKILL.md frontmatter

### New format (all tiers)

```yaml
---
name: web-search                       # kebab-case; matches parent directory
description: Search the web using Brave Search. Use when the user asks for
  current information, news, or anything that needs a live lookup.
license: MIT                           # optional (spec field)
compatibility: Requires network access and BRAVE_API_KEY.   # optional (spec field)
metadata:
  miniclaw:
    requires:
      env: [BRAVE_API_KEY]
      bins: [curl]
    self_update:
      allow_body: false                # opt-in for roadmap #4 (default false)
---
```

### Validation rules (enforced by `core/skill_validator.py`)

- `name`: match `^[a-z0-9]+(-[a-z0-9]+)*$`, 1–64 chars, no leading/trailing/consecutive hyphens, must equal parent directory name.
- `description`: 1–1024 chars, non-empty.
- `license`, `compatibility`, `metadata` (spec fields): validated shape only; stored as-is.
- `allowed-tools`: **ignored outright**. The field is experimental in the spec and honoring it would permit frontmatter-based privilege escalation.
- `requires`: read from `metadata.miniclaw.requires` only. Old top-level `requires:` is not accepted — clean break per migration decision.
- Unknown frontmatter keys log a warning; they do not fail the load (forward-compat with spec evolution).

### Body

No structural change. `## Inputs`/`## Parameters`/`## Input Schema` extraction in `SkillValidator.extract_input_schema` is preserved. The spec does not define input schemas, so this remains a spec-legal MiniClaw extension.

## `config.yaml` — per-tier policy

Sibling file. Shape unchanged for bundled; new enforcement kicks in per tier via `SkillValidator.validate_execution_config(config, tier=...)`.

```yaml
type: docker                  # docker | native  (native only honored for bundled)
image: miniclaw/web-search:latest
env_passthrough: [BRAVE_API_KEY]
timeout_seconds: 15
memory: 256m
cpus: 1.0
read_only: true
devices: []
extra_tmpfs: []
volumes: []
```

### Clamps and allowlists

| Rule                                | bundled | authored                      | imported                      |
| ----------------------------------- | ------- | ----------------------------- | ----------------------------- |
| `type: native` allowed              | yes     | no                            | no                            |
| `memory` hard max                   | none    | 1g                            | 512m                          |
| `timeout_seconds` hard max          | none    | 120s                          | 60s                           |
| `cpus` hard max                     | none    | 2.0                           | 1.0                           |
| `devices`                           | any     | allowlist                     | allowlist                     |
| `volumes`                           | any     | skill-scoped only             | skill-scoped only             |
| `env_passthrough`                   | any     | first-run user confirmation   | first-run user confirmation   |
| `read_only: false` allowed          | yes     | yes                           | requires confirmation         |

**Device allowlist:** `/dev/snd`, `/dev/video0`, `/dev/video1`, `/dev/i2c-*`, `/dev/gpiomem`. Anything else rejected.

**Skill-scoped volumes:** host-side path must resolve under `~/.miniclaw/<skill-name>/`. Mounts like `~:/host` or `/:/rootfs` rejected.

**env_passthrough gate:** on first run of an authored or imported skill, MiniClaw speaks (or prints) the env var list and requires explicit confirmation (`confirm passthrough` by voice, `y/N` by text). The confirmed list is recorded in `.install.json`. Re-prompt only if the skill's config changes on disk.

**Credential-pattern warning:** `env_passthrough` values matching `*_SECRET`, `*_TOKEN`, `*_KEY`, or `ANTHROPIC_API_KEY` trigger an additional confirmation prompt even inside the normal passthrough gate. Not a hard block — users may legitimately want to grant `OPENWEATHER_API_KEY` — but an extra step.

Clamps + allowlists are applied at both install time (reject the install) and load time (move skill to `invalid_skills`). A skill cannot mutate its config on disk to escalate privileges without re-triggering validation.

## Dockerfile validator (generalized)

`core/dockerfile_validator.py` is generalized to a per-tier allowlist. It is invoked from every non-bundled skill load path: the existing `install_skill` voice flow, the new `miniclaw skill install` CLI, and `SkillLoader._load_skill` for any skill in `authored/` or `imported/` (defense in depth against manual file edits).

| Instruction                   | bundled | authored                   | imported                                         |
| ----------------------------- | ------- | -------------------------- | ------------------------------------------------ |
| `FROM miniclaw/base:latest`   | yes     | required                   | required                                         |
| `FROM` anything else          | yes     | no                         | no                                               |
| `RUN pip install <pkg>`       | yes     | yes                        | yes; no `--index-url` or `--extra-index-url`     |
| `RUN apt-get install <pkg>`   | yes     | yes                        | yes; apt allowlist (see below)                   |
| `RUN` arbitrary               | yes     | no                         | no                                               |
| `COPY <local>`                | yes     | yes; paths inside skill    | yes; paths inside skill                          |
| `COPY --from=...`             | yes     | no                         | no                                               |
| `ADD` (any)                   | yes     | no                         | no                                               |
| `WORKDIR`/`CMD`/`ENTRYPOINT`/`ENV` | yes | yes                        | yes                                              |
| `USER`                        | yes     | no                         | no                                               |
| `EXPOSE`                      | yes     | no-op                      | no-op                                            |
| `VOLUME`                      | yes     | no (use config.yaml)       | no (use config.yaml)                             |

**apt-get allowlist (imported tier):** `curl`, `ca-certificates`, `git`, `jq`, `ffmpeg`, `libsndfile1`, `espeak-ng`. Users can extend the allowlist by editing `~/.miniclaw/config/apt-allowlist.txt` — a deliberate, keyboard-only trust decision.

Bundled skills are exempt from Dockerfile validation. They live in git; their provenance is code review.

## Install pipeline

A single pipeline services voice, CLI, and (future) mobile entry points. Voice and CLI both feed the same core: validate, summarize, gate, install, build, reload.

```
1. Fetch       — git clone / tarball extract into a staging dir under /tmp
2. Locate      — find <staging>/SKILL.md; if multiple skills, prompt for choice
3. Validate    — SkillValidator on SKILL.md + config.yaml + Dockerfile,
                 tier=imported (or authored for install_skill voice path).
                 Failure terminates here with a printed/spoken reason.
4. Summarize   — render a permission summary: env_passthrough list, volumes,
                 devices, memory/timeout, apt packages, network access.
5. Confirm     — three gates: "confirm install" → "confirm build" → "confirm restart".
                 Gate style matches invocation mode (spoken for voice, y/N for CLI).
6. Install     — strip any .install.json the staging dir shipped (prevents a
                 malicious skill from claiming pre-confirmed passthrough or
                 trusted provenance). Move staging → ~/.miniclaw/<tier>/<name>/
                 then write a fresh .install.json with
                 {source, sha256, installed_at, user_confirmed_env_passthrough}.
                 The tier is never written into this file — it is inferred by
                 the loader from the install directory.
7. Build       — docker build -t miniclaw/<name>:latest <skill>/scripts/
                 invoked via scripts/build_new_skill.sh on the host.
                 Pipeline never touches the Docker socket directly from a
                 restricted subprocess context.
8. Reload      — orchestrator.reload_skills()
```

This pipeline reuses all of `meta_skill.py`'s existing scaffolding: three-gate pattern, host-side build script, cleanup-on-failure. The `install_skill` native skill gains a new dispatch branch for "install from URL" vs "author from scratch"; the two flows share steps 3–8.

### New CLI surface

```
miniclaw skill install <url|path> [--tier imported|authored]
miniclaw skill uninstall <name>
miniclaw skill list [--tier bundled|authored|imported]
miniclaw skill validate <path>      # dry-run validation, no install
miniclaw skill dev <path>           # dev-mode bind-mount / symlink
```

Implemented in a thin wrapper module invoked from `main.py` (subcommand dispatch) or a dedicated `scripts/miniclaw-skill.py`. Decide at implementation time.

## Voice ergonomics

Voice-in-scope — flows through the same install pipeline with spoken gates:

1. Install community skill by name (e.g. *"install the pdf-tools skill from github dot com slash foo slash bar"*). Pipeline speaks the permission summary, then the three gates.
2. First-run `env_passthrough` confirmation, per skill, once. Recorded in `.install.json`.
3. Update confirmation. If Dockerfile or config SHA256 changes vs `.install.json`, next load prompts: *"the web-search skill has changed on disk. confirm reinstall to accept the new version."* Same three gates.
4. Uninstall. *"uninstall the pdf-tools skill"* → one-gate voice confirmation → remove skill dir + `.install.json` + drop the Docker image.

Voice-out-of-scope — keyboard-only, because "mis-hearing" would widen attack surface materially:

- Extending `~/.miniclaw/config/apt-allowlist.txt`.
- Promoting a skill between trust tiers (requires `mv` on the filesystem).
- `MINICLAW_SKILL_STRICT=false` and other master switches. Logged loudly at startup when set.
- `miniclaw skill dev <path>` — dev-mode entry.
- Granting devices or volumes outside the default allowlist (edit `~/.miniclaw/config/device-allowlist.txt` / `volume-allowlist.txt`).

Text-mode parity: every voice gate has a `y/N` equivalent on the terminal. Nothing is voice-only.

## Dev mode

`miniclaw skill dev <path>`:

1. Validate path contains `SKILL.md` + `config.yaml`.
2. Create symlink `~/.miniclaw/imported/<name>` → `<path>` (or bind-mount on systems without symlink support).
3. Do not write `.install.json`. Dev mode is identified by the loader via symlink detection on the skill directory itself — unspoofable by skill content.
4. Loader detects the symlink and skips Dockerfile allowlist + config clamps.
5. On every load, log `SKILL <name> IN DEV MODE — security validations bypassed`.

Dev mode still runs **structural validation** (name format, frontmatter required fields, parent-dir match). The escape hatch is only for security clamps, not correctness checks. Exiting dev mode is `miniclaw skill install <path>`, which removes the symlink and runs the real pipeline to install the skill normally.

## Self-updating skills — scaffolding for roadmap #4

The frontmatter field `metadata.miniclaw.self_update.allow_body: true` is reserved, recognized by the validator, and otherwise inert in this iteration.

When roadmap item #4 lands, a new native skill (tentatively `update_skill_hints`) will accept `{skill_name, new_body_markdown}` and rewrite only the SKILL.md body, after validating:

- The target skill has `self_update.allow_body: true`.
- The new content is body-only (no injected frontmatter, no new input schema section).
- The rewritten file still parses cleanly through `SkillValidator.validate_markdown`.

Dockerfile, config.yaml, and `scripts/` are never touched by self-update. Any change there requires the full install pipeline — i.e. gated reinstall. This keeps self-improvement scoped to routing hints while preserving every execution-time security invariant.

## Migration of the 12 existing skills

Clean break, single PR. One deterministic migration script produces the rename + restructure as a mechanical diff.

### Renames (snake_case → kebab-case)

```
dashboard            → dashboard            (no change)
homebridge           → homebridge           (no change)
install_skill        → install-skill
playwright_scraper   → playwright-scraper
recall_session       → recall-session
save_memory          → save-memory
schedule             → schedule             (no change)
set_env_var          → set-env-var
skill_tells_random   → skill-tells-random
soundcloud           → soundcloud           (no change)
weather              → weather              (no change)
web_search           → web-search
```

### Structural moves

For each Docker skill, fold `containers/<old-name>/` into `skills/<new-name>/scripts/`. Delete the top-level `containers/` tree entirely.

```
Before:                          After:
skills/web_search/               skills/web-search/
  SKILL.md                         SKILL.md
  config.yaml                      config.yaml
containers/web_search/            scripts/
  Dockerfile                         Dockerfile
  app.py                             app.py
```

### Frontmatter migration

Per skill: move top-level `requires:` into `metadata.miniclaw.requires:`. Descriptions already fit the 1024-char limit.

### Code-side touch points

- `core/container_manager._execute_native_skill` — dispatch dict keys updated: `install_skill` → `install-skill`, `save_memory` → `save-memory`, `set_env_var` → `set-env-var`, `recall_session` → `recall-session`. `dashboard` unchanged.
- `core/skill_loader.DEFAULT_SEARCH_PATHS` — replace `~/.miniclaw/skills` with `~/.miniclaw/authored` + `~/.miniclaw/imported`. Bundled `./skills` path unchanged.
- `run.sh` auto-discovery — `containers/*/Dockerfile` → `skills/*/scripts/Dockerfile`.
- `scripts/port-skill.py` — emits new layout; renamed to `scripts/port-openclaw-skill.py` for clarity.
- Docker image tags (`miniclaw/web-search:latest` etc.) already kebab-case; no change.
- `CLAUDE.md` — rewrite the skill list and "Skill Structure" section to the new layout.
- `WORKING_MEMORY.md` — append migration note under the Hermes roadmap item.

### Unaffected

- `~/.miniclaw/memory/` — content-addressed, no skill-name dependency.
- `~/.miniclaw/sessions.db` — FTS5 search is over user/assistant/tool bodies, not the tool name column; BM25 ranking is unaffected. No reindex needed. Pre-migration rows keep the old snake_case names in the `tool_name` column; this is a historical record, not a live index.
- `.env` — no skill names referenced.

### Migration script

`scripts/migrate-to-agentskills.py` performs the full rename + restructure deterministically. Committed alongside the migration so the before/after is reproducible by running the script against the pre-migration tree. Deleted in the final commit of the migration PR.

## Testing

### Unit tests

- `test_skill_validator.py` — frontmatter name regex, description length, tier-aware `validate_execution_config`, `metadata.miniclaw.requires` parsing, unknown-frontmatter warning, parent-dir name match.
- `test_dockerfile_validator.py` — per-tier allowlist cases: reject arbitrary `FROM`/`RUN`/`COPY --from`/`ADD`; accept only allowlisted apt packages on imported tier.
- `test_skill_loader.py` — three-search-path precedence, per-tier policy application, `.install.json` sidecar read, dev-mode symlink handling, cross-tier name collision rejection.
- `test_install_pipeline.py` — fetch → validate → confirm → install → build → reload happy path, plus one case per rejection branch (bad name, bad Dockerfile, volume escape, env_passthrough credential-pattern block, name collision, memory clamp, timeout clamp).
- `test_migration_script.py` — runs `scripts/migrate-to-agentskills.py` against a fixture copy of the pre-migration tree and asserts the resulting layout is byte-identical to a reference.

### Integration tests

- Install a known-good agentskills.io-format fixture end-to-end against a real Docker daemon; assert it loads, responds, tears down cleanly.
- Install a known-bad fixture (one per security rule); assert rejection with the correct reason.
- Dev-mode round-trip: `miniclaw skill dev` → edit SKILL.md → reload picks up changes without re-validation.

## Rollout

Single PR, merged after tests green. Recommended commit ordering:

1. Run `scripts/migrate-to-agentskills.py` locally; commit the mechanical rename as one commit (pure file movement + frontmatter updates).
2. Loader + validator changes.
3. Install pipeline + CLI.
4. `CLAUDE.md` and `WORKING_MEMORY.md` updates.
5. Delete `scripts/migrate-to-agentskills.py`.

## Future direction

- **Voice-only install** — the end goal is an Alexa-like voice-only experience. CLI stays in scope this iteration for dev ergonomics; the pipeline is entry-point-agnostic so voice-only install can harden later by removing CLI paths if desired.
- **Mobile install** — an HTTP entry point can dispatch into the same pipeline. No architectural change needed.
- **Registry discovery** — if agentskills.io publishes a searchable registry API, a new `miniclaw skill search <query>` subcommand plugs in ahead of step 1 of the install pipeline.
- **Skill signing** — when a mature standard exists in the ecosystem, verify signatures in step 3 before validation.
- **Self-improving skills (roadmap #4)** — the `metadata.miniclaw.self_update.allow_body` scaffolding is already in place.
