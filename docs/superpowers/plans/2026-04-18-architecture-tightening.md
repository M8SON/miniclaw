# Architecture Tightening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four architectural seams: Ollama escalation side-effect safety, deferred prompt construction, unified memory policy, and documented native handler model.

**Architecture:** `EscalateWithContext` lets `OllamaToolLoop` signal that tools already ran, so `Orchestrator` can commit that activity and ask Claude to finalize without re-executing. Prompt construction moves after routing. Memory policy is unified to proactive in both `BASE_PROMPT` and `SKILL.md`. CLAUDE.md documents native as a first-class execution path.

**Tech Stack:** Python 3.11, `anthropic` SDK, existing `ConversationState`/`ToolLoop` interfaces. No new dependencies.

> **Note:** This project has no test suite. TDD steps are replaced with manual verification via `./run.sh` (text mode). Each task includes a verification script or smoke-test command.

---

## File Map

| File | Change |
|---|---|
| `core/ollama_tool_loop.py` | Add `EscalateWithContext` dataclass; track executed tools; return `EscalateWithContext` on escalation after tools ran |
| `core/orchestrator.py` | Add `_claude_finalize_ollama_turn`; update Ollama branch; defer `_build_system_prompt` to after routing |
| `skills/save_memory/SKILL.md` | Replace trigger-phrase gate with proactive "what is worth saving" guidance |
| `core/prompt_builder.py` | Tighten `BASE_PROMPT` memory line to match `SKILL.md` |
| `CLAUDE.md` | Add Execution Paths section documenting Docker and native as first-class |

---

## Task 1: Add `EscalateWithContext` to `ollama_tool_loop.py`

**Files:**
- Modify: `core/ollama_tool_loop.py`

- [ ] **Step 1: Add the `EscalateWithContext` dataclass and update imports**

At the top of `core/ollama_tool_loop.py`, after the existing imports, add:

```python
from dataclasses import dataclass, field
```

After the `EscalateSignal` singleton definition (around line 36), add:

```python
@dataclass
class EscalateWithContext:
    """
    Returned by OllamaToolLoop when tools executed but the loop could not
    complete. Carries the tool activity so the orchestrator can commit it
    to ConversationState before asking Claude to finalize without re-running.
    """
    tool_activity: list[dict] = field(default_factory=list)
    # Each entry: {"name": str, "args": dict, "result": str}
```

- [ ] **Step 2: Track executed tools in `run()`**

At the top of the `run()` method body (after `local_messages = ...` and before the `while` loop), add:

```python
_executed_tools: list[dict] = []
```

- [ ] **Step 3: Append to `_executed_tools` after each successful skill execution**

Find the existing block in `run()` that calls `execute_skill` and checks for `None`:

```python
                    try:
                        result = self.container_manager.execute_skill(skill, args)
                    except Exception as exc:
                        logger.warning("OllamaToolLoop: tool %s raised %s → escalate", tool_name, exc)
                        return EscalateSignal

                    if result is None:
                        logger.warning("OllamaToolLoop: tool %s returned None → escalate", tool_name)
                        return EscalateSignal

                    result = self._extract_and_save_remember(result)
                    logger.info("OllamaToolLoop: tool %s → %s", tool_name, result[:100])
```

Replace with:

```python
                    try:
                        result = self.container_manager.execute_skill(skill, args)
                    except Exception as exc:
                        logger.warning("OllamaToolLoop: tool %s raised %s → escalate", tool_name, exc)
                        return EscalateWithContext(_executed_tools) if _executed_tools else EscalateSignal

                    if result is None:
                        logger.warning("OllamaToolLoop: tool %s returned None → escalate", tool_name)
                        return EscalateWithContext(_executed_tools) if _executed_tools else EscalateSignal

                    result = self._extract_and_save_remember(result)
                    _executed_tools.append({"name": tool_name, "args": args, "result": result})
                    logger.info("OllamaToolLoop: tool %s → %s", tool_name, result[:100])
```

- [ ] **Step 4: Replace all remaining `return EscalateSignal` with context-aware returns**

Every `return EscalateSignal` in `run()` must now carry `_executed_tools` if any tools ran. Replace every occurrence with:

```python
return EscalateWithContext(_executed_tools) if _executed_tools else EscalateSignal
```

There are 8 occurrences in `run()` (lines 103, 106, 115, 123, 141, 149, 178, 187, 190 — note lines 154/159 were already updated in Step 3). Apply the same replacement to all remaining ones.

After the replacement the file should have zero bare `return EscalateSignal` inside `run()`. The module-level `EscalateSignal` singleton itself stays — it is still the "no tools ran" signal.

- [ ] **Step 5: Verify the module imports cleanly**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python -c "from core.ollama_tool_loop import OllamaToolLoop, EscalateSignal, EscalateWithContext; print('OK')"
```

Expected output: `OK`

- [ ] **Step 6: Commit**

```bash
cd ~/linux/miniclaw && git add core/ollama_tool_loop.py && git commit -m "feat: add EscalateWithContext for safe Ollama escalation after tool execution"
```

---

## Task 2: Add `_claude_finalize_ollama_turn` and update Ollama branch in `orchestrator.py`

**Files:**
- Modify: `core/orchestrator.py`

- [ ] **Step 1: Add the `_claude_finalize_ollama_turn` method**

Add this method to `Orchestrator` after `_execute_direct` (around line 209):

```python
    def _claude_finalize_ollama_turn(
        self,
        user_message: str,
        tool_activity: list[dict],
        system_prompt: str,
    ) -> str:
        """
        Finalize a turn where Ollama ran tools but couldn't produce a response.

        Commits the user message and tool activity to ConversationState in
        Anthropic format, then asks Claude to summarize the results without
        re-executing any tools.
        """
        # Commit the user message
        self.conversation_state.append_user_text(user_message)

        # Commit the tool_use assistant turn (synthetic Anthropic format)
        tool_use_blocks = [
            {
                "type": "tool_use",
                "id": f"ollama_{i}",
                "name": activity["name"],
                "input": activity["args"],
            }
            for i, activity in enumerate(tool_activity)
        ]
        self.conversation_state.append_assistant_content(tool_use_blocks)

        # Commit the tool_result user turn
        tool_result_blocks = [
            {
                "type": "tool_result",
                "tool_use_id": f"ollama_{i}",
                "content": activity["result"],
            }
            for i, activity in enumerate(tool_activity)
        ]
        self.conversation_state.append_tool_results(tool_result_blocks)

        # Ask Claude to produce a final spoken response — no tools offered
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_prompt,
            messages=self.conversation_state.select_messages_for_prompt(),
        )

        response_text = " ".join(
            block.text for block in response.content if block.type == "text"
        )
        self.conversation_state.append_assistant_content(
            [{"type": "text", "text": t} for t in [response_text] if t]
        )
        self.conversation_state.prune()

        logger.info(
            "_claude_finalize_ollama_turn: finalized with %d tool(s)", len(tool_activity)
        )
        return response_text or "Done."
```

- [ ] **Step 2: Update the Ollama branch in `process_message`**

Find the Ollama tier block in `process_message` (currently lines 180-190):

```python
        # Ollama tier
        from core.ollama_tool_loop import EscalateSignal
        result = self._ollama_tool_loop.run(
            user_message=user_message, system_prompt=system_prompt
        )
        if result is EscalateSignal:
            logger.info("OllamaToolLoop escalated → Claude")
            return self.tool_loop.run(
                user_message=user_message, system_prompt=system_prompt
            )
        return result
```

Replace with:

```python
        # Ollama tier
        from core.ollama_tool_loop import EscalateSignal, EscalateWithContext
        result = self._ollama_tool_loop.run(
            user_message=user_message, system_prompt=system_prompt
        )
        if result is EscalateSignal:
            logger.info("OllamaToolLoop escalated → Claude (no tools ran)")
            return self.tool_loop.run(
                user_message=user_message, system_prompt=system_prompt
            )
        if isinstance(result, EscalateWithContext):
            logger.info(
                "OllamaToolLoop escalated with %d tool(s) → Claude finalize",
                len(result.tool_activity),
            )
            return self._claude_finalize_ollama_turn(
                user_message, result.tool_activity, system_prompt
            )
        return result
```

- [ ] **Step 3: Verify the orchestrator imports cleanly**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python -c "from core.orchestrator import Orchestrator; print('OK')"
```

Expected output: `OK`

- [ ] **Step 4: Smoke test with OLLAMA_ENABLED=false (Claude-only path untouched)**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && echo "what is 2 plus 2" | python main.py --text
```

Expected: normal Claude response. No errors or tracebacks.

- [ ] **Step 5: Commit**

```bash
cd ~/linux/miniclaw && git add core/orchestrator.py && git commit -m "feat: handle EscalateWithContext — commit Ollama tool activity before Claude finalization"
```

---

## Task 3: Defer prompt construction to after routing

**Files:**
- Modify: `core/orchestrator.py`

- [ ] **Step 1: Update `process_message` to build the prompt after routing**

Find `process_message` (currently starting at line 163). Replace the entire method body with:

```python
    def process_message(self, user_message: str) -> str:
        """Process a user message through the tiered intelligence stack."""
        if self._tier_router is None:
            # OLLAMA_ENABLED=false — Claude-only path, unchanged.
            system_prompt = self._build_system_prompt(user_message=user_message)
            return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)

        route = self._tier_router.route(user_message)
        logger.debug("TierRouter: %s → tier=%s", user_message[:60], route.tier)

        if route.tier == "direct":
            return self._execute_direct(route, user_message)

        system_prompt = self._build_system_prompt(user_message=user_message)

        if route.tier == "claude":
            return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)

        # Ollama tier
        from core.ollama_tool_loop import EscalateSignal, EscalateWithContext
        result = self._ollama_tool_loop.run(
            user_message=user_message, system_prompt=system_prompt
        )
        if result is EscalateSignal:
            logger.info("OllamaToolLoop escalated → Claude (no tools ran)")
            return self.tool_loop.run(
                user_message=user_message, system_prompt=system_prompt
            )
        if isinstance(result, EscalateWithContext):
            logger.info(
                "OllamaToolLoop escalated with %d tool(s) → Claude finalize",
                len(result.tool_activity),
            )
            return self._claude_finalize_ollama_turn(
                user_message, result.tool_activity, system_prompt
            )
        return result
```

- [ ] **Step 2: Update `_execute_direct` to build the prompt lazily**

Replace the existing `_execute_direct` signature and fallback:

```python
    def _execute_direct(self, route, system_prompt: str, user_message: str) -> str:
        """Execute a dispatch-pattern route without any LLM involvement."""
        if route.action == "close_session":
            return self.close_session()

        if route.skill:
            skill = self.skills.get(route.skill)
            if skill:
                result = self.container_manager.execute_skill(skill, route.args)
                return result or "Done."

        # Dispatch resolution failed — fall back to Claude
        logger.warning(
            "_execute_direct: could not resolve skill=%r, falling back to Claude",
            route.skill,
        )
        return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)
```

With:

```python
    def _execute_direct(self, route, user_message: str) -> str:
        """Execute a dispatch-pattern route without any LLM involvement."""
        if route.action == "close_session":
            return self.close_session()

        if route.skill:
            skill = self.skills.get(route.skill)
            if skill:
                result = self.container_manager.execute_skill(skill, route.args)
                return result or "Done."

        # Dispatch resolution failed — build prompt lazily and fall back to Claude
        logger.warning(
            "_execute_direct: could not resolve skill=%r, falling back to Claude",
            route.skill,
        )
        system_prompt = self._build_system_prompt(user_message=user_message)
        return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)
```

- [ ] **Step 3: Verify clean import and smoke test**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python -c "from core.orchestrator import Orchestrator; print('OK')" && echo "what time is it" | python main.py --text
```

Expected: `OK` then a normal response. No errors.

- [ ] **Step 4: Commit**

```bash
cd ~/linux/miniclaw && git add core/orchestrator.py && git commit -m "refactor: defer system prompt construction to after route resolution"
```

---

## Task 4: Unify memory saving policy

**Files:**
- Modify: `skills/save_memory/SKILL.md`
- Modify: `core/prompt_builder.py`

- [ ] **Step 1: Update `save_memory/SKILL.md`**

Replace lines 8-14 (the `## When to use` section):

```markdown
## When to use
Use this skill when the user explicitly asks you to remember something for next time. Trigger phrases include:
- "remember this", "remember that", "don't forget"
- "make a note", "note that", "keep that in mind"
- "remember for next time", "save that", "hold onto that"

Do NOT use this for things the user says in passing. Only save when they clearly intend for it to persist across conversations.
```

With:

```markdown
## What is worth saving
Save things that would be useful to recall in a future session:
- Stated preferences ("I prefer temperatures in Celsius")
- Ongoing projects or goals Mason has described
- Facts about Mason's life or work he has mentioned
- Anything Mason explicitly asks you to remember

Do not save passing remarks, one-off requests, or things that will not matter next session.
```

- [ ] **Step 2: Tighten the `BASE_PROMPT` memory line in `prompt_builder.py`**

Find lines 37-40 in `core/prompt_builder.py`:

```python
        "- If you learn something genuinely worth remembering about Mason — a preference, "
        "an ongoing project, something he asked you to keep in mind, or a useful fact about "
        "his life or work — save it using the save_memory skill without waiting to be asked. "
        "Do not save trivial exchanges. Only save what would be useful to recall in a future session.\n"
```

Replace with:

```python
        "- If you learn something genuinely worth remembering about Mason — a preference, "
        "an ongoing project, something he asked you to keep in mind, or a useful fact about "
        "his life or work — save it using the save_memory skill without waiting to be asked. "
        "Do not save passing remarks or one-off requests. Only save what would be useful "
        "to recall in a future session.\n"
```

- [ ] **Step 3: Verify clean import**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python -c "from core.prompt_builder import PromptBuilder; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
cd ~/linux/miniclaw && git add skills/save_memory/SKILL.md core/prompt_builder.py && git commit -m "docs: unify memory saving policy — proactive in both BASE_PROMPT and SKILL.md"
```

---

## Task 5: Document native handlers as first-class in `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the Execution Paths section**

In `CLAUDE.md`, find the `### Skill Structure` heading. Immediately before it, add the following new section:

```markdown
### Execution Paths

MiniClaw has two first-class execution paths:

**Docker** — default for stateless, sandboxed skills. Network/text transforms, web queries, API integrations. Isolated, memory-limited, torn down after each call.

**Native** — for skills that need host integration: hardware access, process control, reloading the orchestrator itself, or anything that cannot run in a container. Registered in `container_manager._execute_native_skill`.
Current native skills: `install_skill`, `set_env_var`, `save_memory`, `dashboard`.

When adding a new skill, choose Docker unless host access is genuinely required.

```

- [ ] **Step 2: Commit**

```bash
cd ~/linux/miniclaw && git add CLAUDE.md && git commit -m "docs: document native handlers as a first-class execution path alongside Docker"
```

---

## Self-Review

**Spec coverage:**
- Issue 1 (EscalateWithContext): Tasks 1 and 2 ✓
- Issue 2 (deferred prompt): Task 3 ✓
- Issue 3 (memory policy): Task 4 ✓
- Issue 4 (native docs): Task 5 ✓
- Out-of-scope item (tests for escalate-with-context path): correctly absent ✓

**Placeholder scan:** No TBDs or TODOs. All code blocks are complete and self-contained.

**Type consistency:**
- `EscalateWithContext.tool_activity` defined in Task 1, referenced as `result.tool_activity` in Task 2 ✓
- `_executed_tools` list of `{"name", "args", "result"}` dicts defined in Task 1, consumed in `_claude_finalize_ollama_turn` in Task 2 ✓
- `_execute_direct` signature changed from `(route, system_prompt, user_message)` to `(route, user_message)` — call site updated in Task 3 Step 1 ✓
- `append_assistant_content`, `append_tool_results`, `select_messages_for_prompt`, `prune` all verified against `conversation_state.py` ✓
