# Token Reduction Design

> **For agentic workers:** Use superpowers:writing-plans to implement this spec.

**Goal:** Reduce per-request input tokens for simple commands (e.g. "play a song") from ~6,266 to ~2,500–3,500 without degrading skill invocation accuracy.

**Approach:** Two independent improvements — semantic skill selection (architectural) and dashboard SKILL.md editorial trim (content).

---

## Component A: Semantic Skill Selection

### Problem

Every request, regardless of complexity, injects all 10 skills in full (~3,700 tokens). A "play a song" command only needs soundcloud, but it gets the dashboard live-update instructions, homebridge device schema, and 8 other skills it will never use.

### Design

**New file: `core/skill_selector.py`**

A `SkillSelector` class that:
- Loads `sentence-transformers` (`all-MiniLM-L6-v2`) once at startup — already installed as a chromadb dependency, no new packages required
- Embeds each skill as `"{name}: {description}"` when `index(skills)` is called (on startup and after skill reload)
- Exposes `select(user_message, top_k=2) -> set[str]` — embeds the user message, computes cosine similarity against indexed skills, returns the top `top_k` skill names

**Threshold rule:** If no skill scores above 0.25 cosine similarity, return the top 2 anyway — avoids starving Claude on ambiguous queries.

**Fallback:** If the model fails to load (import error, cold start), log a warning and set `selector = None`. PromptBuilder treats `None` selector as "all skills full" — zero behavior change, no crash.

**Changes to `PromptBuilder`**

`build()` gains an optional `user_message: str | None = None` parameter.

- When `user_message` is provided: call `skill_selector.select(user_message)` to get the set of full-detail skill names. Skills in that set get full instructions. All others get a compact one-liner: `"- {name}: {description}"`.
- When `user_message` is absent (e.g. initial session): fall back to current all-full behavior.

`PromptBuilder.__init__()` gains an optional `skill_selector: SkillSelector | None = None` parameter.

**Always-full skills** (never compacted regardless of query):
- `set_env_var` — safety-critical, already in `ALWAYS_FULL_SKILLS`
- `save_memory` — Claude is instructed to proactively invoke it; needs full context to know when
- `install_skill` — multi-step confirmation flow requires full instructions

**Changes to `Orchestrator`**

Pass `user_message` to `prompt_builder.build()` on each API call. The system prompt becomes per-request rather than pre-built. Overhead: ~10ms for the embedding lookup — negligible vs. API latency.

After skill reload (`reload_skills()`), call `skill_selector.index(skills)` to re-embed the updated skill set.

**Estimated savings for "play a song":**

| | Tokens |
|---|---|
| Soundcloud full | ~180 |
| 9 skill one-liners | ~90 |
| Base prompt + memory + message | ~2,500 |
| **Total** | **~2,770** |
| Today's total | ~6,266 |

### Configuration

`SKILL_SELECT_TOP_K` env var (default: `2`) — number of skills to expand in full per request.

---

## Component B: Dashboard SKILL.md Trim

### Problem

The dashboard SKILL.md is 1,316 tokens — the largest skill file by far. Much of the content is redundant: scripted dialogue strings, prose that duplicates the YAML schema, and 6 examples where 3 would suffice.

### What stays

- "When to use" trigger list
- "Before opening — check memory first" rule (condensed to 2 lines)
- Input YAML schema (tool definition — cannot cut)
- Panel selection table
- "How to respond" (already 4 lines)

### What gets condensed

- **"Gathering preferences"** — remove verbatim quoted strings; keep bare instructions (what to ask, what to save). The exact wording is Claude's to choose.
- **"Building the news config"** — cut prose descriptions of each source type (duplicates enum values in schema). Keep 2–3 GDELT query examples, remove the rest.
- **"Live topic updates"** — cut from 6 examples to 3. Keep the rule about when NOT to include `news_sources`.

### Target

~650 tokens (down from ~1,316). All behavioral rules intact — only redundant prose and excess examples removed.

---

## Testing

### Dashboard SKILL.md trim

Verify Claude produces identical tool inputs before and after the trim for these 4 queries (text mode, `python3 main.py --text`):

1. "Open the dashboard" → expect: `action: open`, all 4 panels, memory check for location
2. "Show me Middle East news" → expect: `gdelt_queries: ["Middle East conflict news"]`, no `news_sources`
3. "Switch to local news" → expect: `gdelt_queries: ["Burlington Vermont"]`, `news_sources: ["local_vt"]`
4. "Close the dashboard" → expect: `action: close`

### Semantic skill selection

Add a `--skill-select "<query>"` flag to `main.py` (or a standalone test script) that prints which skills would be selected for a given query without making an API call. Run before and after the implementation to validate selection accuracy.

Test queries and expected top selections:

| Query | Expected top skills |
|---|---|
| "play a song" | soundcloud |
| "what's the weather" | weather |
| "show me the dashboard" | dashboard |
| "search for X" | web_search |
| "show me what's playing" | dashboard or soundcloud (ambiguous — both acceptable) |
| "add a skill that does X" | install_skill |
| "remember that I prefer dark mode" | save_memory |

If any selection is clearly wrong (e.g. "play a song" selects homebridge), increase `SKILL_SELECT_TOP_K` to 3 or review the skill descriptions used for embedding.

---

## Rollout Order

1. Trim dashboard SKILL.md and verify with the 4 test queries (no code change, easy to validate)
2. Implement `core/skill_selector.py` and wire into `PromptBuilder` and `Orchestrator`
3. Run `--skill-select` tests
4. Smoke test full conversation in text mode
