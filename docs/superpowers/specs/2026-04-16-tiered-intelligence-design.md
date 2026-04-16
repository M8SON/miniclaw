# Tiered Intelligence Architecture

**Date:** 2026-04-16
**Status:** Approved
**Author:** Mason Misch

## Goal

Replace Claude as the default reasoning layer with a three-tier gate that uses Claude only when genuinely needed. The system should feel as fast and natural as human conversation while minimising API token cost.

Tiers:
1. **Deterministic** — regex/keyword dispatch, no LLM
2. **Ollama** — local LLM, full tool loop, for routine commands
3. **Claude** — remote API, for complex/ambiguous/meta requests

## Architecture

```
STT transcript
      │
      ▼
┌──────────────────────┐
│      TierRouter      │  deterministic, <5ms
│  1. dispatch pattern?│ → direct skill call (no LLM)
│  2. escalate pattern?│ → Claude immediately
│  3. predict skill    │
│     (SkillSelector)  │
│     claude_only set? │ → Claude immediately
│  4. else             │ → Ollama
└──────────────────────┘
         │
    ┌────┴────┐
    │         │
 Ollama     Claude
 ToolLoop   ToolLoop
    │    (existing,
    │     unchanged)
    │ EscalateSignal
    └──────→ Claude
             ToolLoop
```

Each tier either resolves (returns a spoken response) or passes down. `TierRouter` can route directly to Claude when escalate patterns match, skipping Ollama entirely to avoid double latency. Ollama only runs for transcripts the router is confident it can handle. When Ollama escalates, Claude receives the same `ConversationState` so context is continuous.

## New Components

### `core/tier_router.py`

Single responsibility: given a transcript, return `direct | ollama | claude` plus pre-extracted args for the direct case.

Three ordered checks:

**1. Dispatch patterns** — regex table loaded from `config/intent_patterns.yaml`:

```yaml
dispatch:
  - pattern: "^(stop|pause|halt)(\\s+music|\\s+playing)?$"
    skill: soundcloud_play
    args: {action: stop}
  - pattern: "^(volume up|louder)"
    skill: soundcloud_play
    args: {action: volume_up}
  - pattern: "^(volume down|quieter)"
    skill: soundcloud_play
    args: {action: volume_down}
  - pattern: "^(goodbye|bye|stop listening)"
    action: close_session
```

**2. Escalate patterns** — transcripts that Ollama would handle poorly, routed straight to Claude:

```yaml
escalate:
  - "\\b(install|add|make|create|build)\\s+(a\\s+)?(skill|tool|plugin)"
  - "\\b(remember|forget|don't forget)\\b"
  - "\\bexplain\\b.{20,}"
```

**3. Skill prediction** — reuses the existing `SkillSelector.select()`. If the top predicted skill is in `CLAUDE_ONLY_SKILLS`, route to Claude. Otherwise route to Ollama.

~80 lines of Python, no ML, no network calls.

### `core/ollama_tool_loop.py`

Mirrors `ToolLoop` but calls Ollama's OpenAI-compatible API (`/v1/chat/completions` with `tools` parameter). Interface is identical:

```python
OllamaToolLoop.run(user_message, system_prompt) -> str | EscalateSignal
```

`EscalateSignal` is a singleton sentinel class instance (not an exception) defined in `ollama_tool_loop.py` — `if result is EscalateSignal` in the orchestrator catches it and hands off to Claude cleanly. Using identity comparison (`is`) avoids accidental string matches.

**Escalation triggers (checked in order):**

1. Invalid tool call — Ollama names a skill not in the registry
2. Malformed JSON args — tool call args can't be parsed
3. Explicit signal — system prompt instructs Ollama to respond with the single word `ESCALATE` if the request is too complex; orchestrator detects this and escalates
4. Timeout — `OLLAMA_TIMEOUT_SECONDS` exceeded
5. Loop limit — same 10-round cap as Claude's ToolLoop

**Tool result format:** Ollama doesn't use Anthropic's `tool_result` block format. Results are injected as a `user` message: `[TOOL RESULT: skill_name]\n{result}`. Small instruction-tuned models handle this reliably.

**Shared state:** `ConversationState`, `ContainerManager`, and `MemoryProvider` are shared with `ToolLoop`. A turn handled by Ollama and a follow-up handled by Claude share the same conversation history.

### `config/intent_patterns.yaml`

Dispatch and escalate pattern tables. Loaded at startup by `TierRouter`. Adding a new deterministic command requires only a new entry here — no code changes.

## Modified Components

### `core/orchestrator.py`

`__init__` gains one new branch: if `OLLAMA_ENABLED`, construct `OllamaToolLoop` and `TierRouter`. Otherwise the existing path is completely unchanged.

`process_message()` becomes:

```python
def process_message(self, user_message):
    if self._tier_router:
        route = self._tier_router.route(user_message)
        if route.tier == "direct":
            return self._execute_direct(route)
        if route.tier == "claude":
            return self._run_claude(user_message)
        # ollama
        result = self._ollama_tool_loop.run(user_message, system_prompt)
        if result is EscalateSignal:
            return self._run_claude(user_message)
        return result
    return self._run_claude(user_message)  # existing path, OLLAMA_ENABLED=false
```

~30 lines added total. All existing behaviour behind `OLLAMA_ENABLED=false`.

## Configuration

All new env vars are optional. Existing installs are unaffected when `OLLAMA_ENABLED` is unset or false.

| Variable | Default | Notes |
|---|---|---|
| `OLLAMA_ENABLED` | `false` | Feature flag — off until Pi hardware is ready |
| `OLLAMA_HOST` | `http://localhost:11434` | Local Ollama instance URL |
| `OLLAMA_MODEL` | `phi4-mini` | Swap to larger model as hardware allows |
| `OLLAMA_TIMEOUT_SECONDS` | `8` | Escalate to Claude on timeout |
| `CLAUDE_ONLY_SKILLS` | `install_skill` | Comma-separated skill names always routed to Claude |

## What Does Not Change

- `ToolLoop` — untouched
- `SkillSelector` — reused by `TierRouter`, not modified
- `ContainerManager` — called identically by both tool loops
- `ConversationState` — shared across all tiers
- `PromptBuilder` — used by both tool loops
- All existing skills — no changes required
- `run.sh`, Docker setup, CI — unaffected

## New File Summary

```
core/tier_router.py            ← new
core/ollama_tool_loop.py       ← new
config/intent_patterns.yaml    ← new
```

## Hardware Note

`OLLAMA_ENABLED=false` by default. The Ollama tier is designed to activate when the Raspberry Pi 5 + AI HAT+ arrives. The NPU (Hailo-8L, 13 or 26 TOPS) offloads Whisper STT and Kokoro TTS, freeing Pi CPU/RAM for Ollama inference. Target Ollama model size can be increased as RAM tier is confirmed (8GB vs 16GB).

The deterministic tier (`TierRouter` dispatch patterns) can be built and validated immediately on any hardware — it requires no LLM.

## Routing Examples

| Transcript | Tier | Reason |
|---|---|---|
| "stop" | direct | dispatch pattern |
| "volume up" | direct | dispatch pattern |
| "play some jazz" | ollama | SkillSelector → soundcloud_play (Ollama-capable) |
| "what's the weather" | ollama | SkillSelector → weather (Ollama-capable) |
| "make me a tool that tracks my sleep" | claude | escalate pattern match |
| "remember that I prefer dark mode" | claude | escalate pattern match |
| "search for recent news about X and summarise it" | claude | SkillSelector → claude_only or Ollama escalates |
