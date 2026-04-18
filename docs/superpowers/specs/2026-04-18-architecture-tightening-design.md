# Architecture Tightening Design

**Date:** 2026-04-18
**Status:** Approved

## Summary

Four targeted fixes to address architectural seams identified in a code review.
No new features. No refactoring beyond what each fix requires.

---

## Issue 1 (High): Ollama escalation side-effect safety

### Problem

`OllamaToolLoop.run()` executes tools immediately but only commits to
`ConversationState` on success. Six escalation paths can fire after one or more
tools have already run. The orchestrator then reruns the full turn through
Claude, risking duplicate side effects and inconsistent state.

### Design: EscalateWithContext

Add a second return type to `ollama_tool_loop.py`:

```python
@dataclass
class EscalateWithContext:
    tool_activity: list[dict]  # [{name, args, result}, ...]
```

`OllamaToolLoop.run()` gains an internal `_executed_tools` list. After every
successful `container_manager.execute_skill()` call, append
`{name, args, result}`. On any escalation that fires when `_executed_tools` is
non-empty, return `EscalateWithContext(_executed_tools)` instead of
`EscalateSignal`.

The orchestrator Ollama branch becomes:

```python
result = self._ollama_tool_loop.run(...)
if result is EscalateSignal:
    # No tools ran — safe to rerun through Claude
    return self.tool_loop.run(user_message, system_prompt)
if isinstance(result, EscalateWithContext):
    # Tools ran — finalize without re-executing
    return self._claude_finalize_ollama_turn(
        user_message, result.tool_activity, system_prompt
    )
return result
```

`_claude_finalize_ollama_turn()` is a new private method on `Orchestrator`:

1. Commits `user_message` to `ConversationState`
2. Translates `tool_activity` into Anthropic-format `tool_use` + `tool_result`
   blocks and appends them to `ConversationState`
3. Makes a direct Claude API call with **no tools offered** (prevents
   re-execution)
4. Commits the final assistant response to `ConversationState`

This bypasses `ToolLoop` entirely for finalization — Claude only needs to
produce a spoken summary of what already happened.

### Invariant

After this change: tools executed by OllamaToolLoop are always recorded in
`ConversationState` before Claude is involved, and Claude is never offered tools
for a turn where Ollama already executed them.

---

## Issue 2 (Medium): Defer prompt construction until after routing

### Problem

`process_message()` builds the full system prompt (memory load + skill
expansion) before checking the route tier. Direct-action paths discard it
entirely. The "cheap fast path" architecture is correct in concept but not in
execution.

### Design: Lazy prompt

Move `system_prompt = self._build_system_prompt(user_message=user_message)`
from before routing to inside each branch that needs it:

```python
def process_message(self, user_message: str) -> str:
    if self._tier_router is None:
        system_prompt = self._build_system_prompt(user_message=user_message)
        return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)

    route = self._tier_router.route(user_message)

    if route.tier == "direct":
        return self._execute_direct(route, None, user_message)

    system_prompt = self._build_system_prompt(user_message=user_message)

    if route.tier == "claude":
        return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)

    # Ollama tier ...
```

`_execute_direct` receives a `prompt_builder` callable (a zero-arg lambda
wrapping `self._build_system_prompt`) instead of a pre-built string. It only
invokes the callable if dispatch resolution fails and falls back to Claude
(the existing path at `orchestrator.py:203`). Successful direct actions never
call it.

No behaviour change for any path. Direct actions that succeed skip prompt
construction entirely.

---

## Issue 3 (Medium): Unified memory saving policy

### Problem

`prompt_builder.py:BASE_PROMPT` tells Claude to save memories proactively
without waiting. `save_memory/SKILL.md` tells Claude to save only when the user
explicitly asks. The contradiction produces behaviour drift.

### Design: Align to proactive policy (Option A)

**`save_memory/SKILL.md`** — replace the `## When to use` section (trigger-phrase gate) with:

```markdown
## What is worth saving
Save things that would be useful to recall in a future session:
- Stated preferences ("I prefer temperatures in Celsius")
- Ongoing projects or goals Mason has described
- Facts about Mason's life or work he's mentioned
- Anything Mason explicitly asks you to remember

Do not save passing remarks, one-off requests, or things that won't matter
next session.
```

**`prompt_builder.py:BASE_PROMPT`** — tighten the existing line to explicitly
name "passing remarks" as the counter-example, mirroring SKILL.md:

```python
"- If you learn something genuinely worth remembering about Mason — a preference, "
"an ongoing project, something he asked you to keep in mind, or a useful fact about "
"his life or work — save it using the save_memory skill without waiting to be asked. "
"Do not save passing remarks or one-off requests. Only save what would be useful "
"to recall in a future session.\n"
```

Result: one policy, stated consistently in both places.

---

## Issue 4: Native handlers are first-class

### Problem

`CLAUDE.md` implies Docker is the canonical execution path and native is a
special case. This is misleading — native handlers are a permanent, intentional
architectural choice for host-integrated skills.

### Design: Document the two-path model

Add an **Execution Paths** section to `CLAUDE.md` under "Skill Structure":

```markdown
### Execution Paths

MiniClaw has two first-class execution paths:

**Docker** — default for stateless, sandboxed skills. Network/text transforms,
web queries, API integrations. Isolated, memory-limited, torn down after each
call.

**Native** — for skills that need host integration: hardware access, process
control, reloading the orchestrator itself, or anything that can't run in a
container. Registered in `container_manager._execute_native_skill`.
Current native skills: `install_skill`, `set_env_var`, `save_memory`, `dashboard`.

When adding a new skill, choose Docker unless host access is genuinely required.
```

No code changes. Documentation only.

---

## Out of scope

- Tests for the "Ollama executed a tool, then escalated" path are not in this
  spec. They are the highest-priority residual risk and should be added after
  Issue 1 is implemented, in a separate task.
- Prompt stratification beyond Issue 2's minimal change (e.g. a lightweight
  Ollama-specific prompt) is deferred.
- Native handler interface formalization is deferred.
