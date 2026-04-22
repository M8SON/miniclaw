# FTS5 Session Archive — Design Spec

**Status:** approved design, pending implementation plan
**Date:** 2026-04-22
**Author:** Mason Misch
**Context:** Second of four Hermes-inspired enhancements. The `schedule` skill (#1) shipped 2026-04-19; this spec covers item #2. Items #3 (agentskills.io compat) and #4 (self-improving skills) get their own specs.

## Motivation

MiniClaw remembers curated facts (markdown vault + chromadb) but forgets every conversation as soon as it ends. Users who ask *"what did we decide about the dashboard last week?"* or *"when did we last talk about the AI HAT+ 2?"* get nothing — the assistant has no record of what was actually said in prior sessions.

The vault is the wrong place to fix this. Vault notes are hand-curated facts ("user prefers af_heart voice"), not raw conversation history. Bloating the vault with thousands of voice turns would degrade the curated-memory recall it does well today.

A separate transcript archive, indexed by FTS5, fills the gap.

## Goals

- Persist every conversation turn (voice and text mode) to a local sqlite + FTS5 store.
- Let Claude search prior sessions by content via a new native skill `recall_session`.
- Zero added latency on the voice loop — writes must be microsecond-cheap on Pi.
- Survive process crashes without losing the in-progress session.
- Structurally ready for an optional chromadb rerank layer once Hailo-8L NPU offload makes embeddings cheap. No throwaway work.

## Non-goals

- Not a replacement for the curated memory vault. The two stores answer different questions: vault = *"what do we know about X?"*, archive = *"did we ever literally talk about X?"*
- Not always-on background recall. Tool-invoked only — Claude calls `recall_session` when the user references prior sessions.
- Not a chromadb-backed semantic store on day one. Pi 5 CPU embedding (~50–200ms per turn through `all-MiniLM-L6-v2`) is too expensive for the write path. Semantic rerank is deferred until Hailo offload makes it near-free.
- Not user-facing storage management. No pruning UI, no per-session deletion in v1. Add only if disk usage becomes a real problem (transcripts are tiny).
- Not encrypted at rest. Local-only Pi deployment, same trust boundary as the vault.

## Engine choice — why FTS5 over chromadb

| | FTS5 | chromadb (default `all-MiniLM-L6-v2`) |
|---|---|---|
| Per-turn write cost on Pi 5 CPU | ~1 ms (sqlite insert) | ~50–200 ms (transformer forward pass) |
| Resident RAM | a few MB | ~150–300 MB (model loaded) |
| Disk per turn | ~content size + ~30% FTS index | content + 384-dim float vector + chromadb overhead |
| Recall on LongMemEval R@5 | ~86% (BM25) | ~96% (semantic) |
| Strengths | exact phrases, proper nouns, dates | paraphrase, conceptual queries |

The ~10pp recall gap on paraphrased queries is real but acceptable in exchange for keeping the voice loop fast. Voice transcripts are dominated by concrete anchors (project names, commands, dates) where keyword search shines. Curated semantic recall is already covered by the existing chromadb vault.

When Hailo-8L lands, we add chromadb as a **lazy rerank** layer over FTS5 results — embedding only the query plus top-N candidates at query time, never every turn at write time. See §6.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ main.py                                                 │
│ └─ instantiates SessionArchive, injects into            │
│    orchestrator and container_manager                   │
│                                                         │
│ Orchestrator (existing) gains three hooks:              │
│ ├─ start_session(mode) → session_id  on first user turn │
│ ├─ append_turn(...)                  after each turn    │
│ └─ end_session(session_id)           on session close   │
│                                                         │
│ core/session_archive.py (new)                           │
│ ├─ SessionArchive                                       │
│ │   ├─ start_session(mode) -> int                       │
│ │   ├─ append_turn(session_id, role, content,           │
│ │   │              tool_name=None) -> None              │
│ │   ├─ end_session(session_id) -> None                  │
│ │   └─ search(query, since=None, limit=5,               │
│ │             oversample=None) -> list[dict]            │
│ └─ all sqlite work wrapped in try/except                │
│                                                         │
│ skills/recall_session/ (new native skill)               │
│ ├─ SKILL.md                                             │
│ └─ config.yaml (type: native)                           │
│                                                         │
│ container_manager._execute_native_skill                 │
│ └─ new branch: "recall_session" → archive.search()      │
└─────────────────────────────────────────────────────────┘
```

### New files

- `core/session_archive.py` — sqlite + FTS5 store, write/read API.
- `skills/recall_session/SKILL.md` — routing instructions.
- `skills/recall_session/config.yaml` — `type: native`, no Docker.

### Touched files

- `core/orchestrator.py` — three small hook calls (start / append / end).
- `core/container_manager.py` — register the `recall_session` native handler, accept injected `_archive` reference.
- `main.py` — instantiate `SessionArchive`, pass to orchestrator and container_manager.

### No new dependencies

`sqlite3` is in the Python stdlib and standard CPython builds ship with FTS5 enabled.

## Data model

```sql
CREATE TABLE sessions (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,           -- ISO 8601
    ended_at    TEXT,                    -- NULL until session closes
    mode        TEXT NOT NULL,           -- 'voice' | 'text'
    turn_count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE turns (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES sessions(id),
    turn_index  INTEGER NOT NULL,        -- 0-based within session
    ts          TEXT NOT NULL,           -- ISO 8601
    role        TEXT NOT NULL,           -- 'user' | 'assistant' | 'tool'
    content     TEXT NOT NULL,           -- text body OR short tool summary
    tool_name   TEXT                     -- NULL unless role='tool'
);

CREATE INDEX idx_turns_session ON turns(session_id, turn_index);
CREATE INDEX idx_turns_ts      ON turns(ts);

CREATE VIRTUAL TABLE turns_fts USING fts5(
    content,
    content='turns', content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER turns_ad AFTER DELETE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
```

**Notes:**
- `content='turns'` external-content mode: FTS5 stores only the index, not a duplicate of the text. ~30% disk overhead instead of 130%.
- `tokenize='porter unicode61'` — Porter stemming + Unicode-aware. Catches "scheduled"/"scheduling"/"schedules" with one query.
- Tool turns use `role='tool'`, `tool_name='weather'`, `content='Paris: 14°C, light rain'` — a one-line summary, not the JSON blob. The summary is composed inline in the orchestrator from the existing `tool_use` and `tool_result` blocks: `f"{tool_name}({short_input}) → {short_result}"`, truncated to ~200 chars.
- No `schema_version` table in v1. `CREATE TABLE IF NOT EXISTS` on init. Add migration scaffolding only when the first migration is needed.

## Write path

**Session boundaries mirror runtime:**

| Mode | Session start | Session end |
|---|---|---|
| Voice | First user turn after wake word | Idle timeout, "goodbye" / "stop", or process exit |
| Text | Process start | Process exit |

**Per-turn writes:** every user message, every assistant final response, and every tool call gets its own row, written immediately after the turn completes. This is per-turn for crash safety — losing one in-progress turn beats losing the whole session.

**Failure posture:** every public method on `SessionArchive` wraps its sqlite work in `try/except`, logs warnings, and returns gracefully. A broken sqlite never raises into the voice loop. Same posture as the existing `MemPalaceBridge` chromadb mirror.

## Read path — `recall_session` skill

### `SKILL.md`

```markdown
---
name: recall_session
description: Search past conversation transcripts. Use when the user references something said in a prior session.
---

Search the local archive of past conversations for content matching a query.

When to use:
- User references a prior conversation ("what did we decide about X last week?")
- User asks "did we ever talk about X?" or "when did we last discuss X?"
- You need to verify what was actually said in a past session

When NOT to use:
- For general facts or preferences — those live in saved memory and are already in your prompt
- For the current ongoing conversation — that's in your context window

Input (JSON via SKILL_INPUT):
- query (required): keywords or short phrase to search for
- since (optional): ISO date "2026-04-15" or relative "yesterday" / "last week"
- limit (optional): max results, default 5

Output: dated snippets ordered by relevance, each with ±1 surrounding turn for context.
Tell the user when matches were from and quote the relevant lines. If nothing matches, say so plainly.
```

### `config.yaml`

```yaml
type: native
timeout_seconds: 5
```

### Native handler shape

```python
def _execute_recall_session(self, skill_input: dict) -> str:
    query = skill_input.get("query", "").strip()
    if not query:
        return "No query provided."
    since = self._parse_since(skill_input.get("since"))
    limit = int(skill_input.get("limit", 5))
    hits = self._archive.search(query, since=since, limit=limit)
    if not hits:
        return f"No prior sessions mention '{query}'."
    return self._format_hits(hits)
```

### Result format (what Claude reads back)

```
[2026-04-19 14:30] user: how's the schedule skill coming along?
[2026-04-19 14:30] assistant: shipped it — yaml-backed recurring tasks…
[2026-04-19 14:31] tool: schedule(action=list) → 3 active tasks

[2026-04-15 09:12] user: remind me what we said about ollama tier
[2026-04-15 09:12] assistant: gated behind OLLAMA_ENABLED=false until Pi hardware…
```

Blank line between hits. For each match, fetch ±1 surrounding turn from the same session for context. Claude composes a natural-language response from this output.

### Recall SQL

```sql
SELECT t.ts, t.role, t.tool_name, t.content, s.mode, t.session_id, t.turn_index
FROM turns_fts
JOIN turns t ON t.id = turns_fts.rowid
JOIN sessions s ON s.id = t.session_id
WHERE turns_fts MATCH ?
  AND (? IS NULL OR t.ts >= ?)
ORDER BY rank
LIMIT ?;
```

Then a second query per hit fetches `turn_index ± 1` from the same `session_id` for context.

### Date parsing

A small helper accepts: ISO ("2026-04-15"), "today", "yesterday", "last week", "N days ago". Anything unrecognized falls through to no date filter rather than failing. The aim is robustness — recall should always return something useful when the user clearly wanted recall.

## Forward-compat — chromadb rerank when Hailo lands

Three contracts the v1 implementation must honor so chromadb can drop in cleanly without schema change or v1 code edits:

1. **`SessionArchive.search()` returns structured dicts, not formatted strings.** Each hit is a dict with `session_id`, `turn_id`, `ts`, `role`, `tool_name`, `content`, `context` (the ±1 surrounding turns), and `fts_rank`. Formatting lives in the skill handler. A rerank step slots between `search()` and the formatter.

2. **`SessionArchive.search()` accepts `oversample: int | None = None`.** When a reranker is plugged in, search returns top-N (e.g. 20) candidates instead of `limit`, and the reranker trims down to `limit`. v1 ignores `oversample` and returns `limit` directly.

3. **No vectors stored in sqlite.** When chromadb is added, it operates as a **lazy rerank** layer: embed only the query and the FTS5 top-N candidates at query time, never every turn at write time. This means:
   - No write-path embedding cost (preserves the v1 latency posture).
   - No schema migration — chromadb runs as a separate parallel store (new collection, distinct from the existing curated-memory collection).
   - On Hailo, embedding ~21 short texts (1 query + 20 candidates) at ~3ms each ≈ 60ms total per recall query. Invisible.

The Hailo-era upgrade is purely additive:

```python
# Future: core/session_archive_rerank.py
class ChromadbReranker:
    def rerank(self, query: str, hits: list[dict]) -> list[dict]: ...

# main.py:
archive = SessionArchive(reranker=ChromadbReranker() if HAILO_AVAILABLE else None)
```

`SessionArchive.search()` checks `self._reranker`; if set, oversamples and reranks; otherwise returns FTS5 results unchanged. v1 code paths stay live untouched.

**Why lazy rerank, not eager indexing:** eager (embed every turn at write) is exactly the cost we chose to avoid for v1. Lazy (embed only at query time, only top-N) bounds the cost and only pays it when recall actually fires. Recall is rare relative to writes, so the total cost stays small.

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `SESSION_ARCHIVE_PATH` | `~/.miniclaw/sessions.db` | sqlite file location |
| `SESSION_ARCHIVE_ENABLED` | `true` | kill switch — when false, all archive calls become no-ops |
| `SESSION_RECALL_DEFAULT_LIMIT` | `5` | default `limit` when the skill omits it |

## Failure modes

| Failure | Behavior |
|---|---|
| sqlite file missing or corrupt | `SessionArchive.__init__` recreates schema; if that fails, all methods become no-ops with a logged warning. Voice loop unaffected. |
| Disk full | `try/except` around writes — log warning, drop the turn. Voice loop unaffected. |
| FTS5 not compiled into sqlite | `__init__` detects via a probe query and disables the archive with a warning. `recall_session` returns "archive unavailable." |
| Tool summary serialization fails | Skip the tool turn (still record the user/assistant text). |
| Claude calls `recall_session` with malformed input | Handler returns a friendly error string; Claude can retry or explain. |

## Testing approach

- **Unit tests** for `SessionArchive` against a `:memory:` sqlite db: start_session → append_turn loop → end_session → search returns expected hits. Cover Porter stemming behavior, since-date filter, oversample param.
- **Integration test** for the `recall_session` native skill via `container_manager._execute_native_skill` with a seeded archive db.
- **Schema migration test** — open a v1 db, confirm `CREATE TABLE IF NOT EXISTS` is idempotent.
- **Failure injection** — point `SESSION_ARCHIVE_PATH` at an unwritable location and confirm voice loop continues unaffected.
- **Manual smoke test** on text mode: one process run, ask three questions, exit, restart, recall_session for content from the prior process.

CI hooks the unit + integration tests into the existing fast suite (parallel to the scheduler harness added 2026-04-19).
