# Cron / Scheduled Task Skill — Design Spec

**Status:** approved design, pending implementation plan
**Date:** 2026-04-19
**Author:** Mason Misch
**Context:** First of four Hermes-inspired enhancements. See conversation notes for the broader comparison; this spec covers only the scheduled-task feature.

## Motivation

MiniClaw today is reactive — it waits for a wake word, responds, goes quiet. A home assistant should also be *proactive*: morning briefings, hourly dashboard refreshes, daily logs. Hermes Agent ships a natural-language cron scheduler and it's the feature that most obviously maps onto MiniClaw's voice + native-skill model.

## Goals

- Let users create recurring tasks by voice: *"computer, every morning at 8 tell me the weather and top news."*
- Fire tasks through the full orchestrator so skills, tiering, and memory recall all work unchanged.
- Never interrupt an active voice conversation.
- Keep the implementation contained — one new module, one new native skill, no changes to Docker/voice/memory subsystems.

## Non-goals

- Not a medical-grade reminder system. Missed fires are skipped silently (see §5).
- Not a task queue for one-shot delayed jobs (no "remind me in 10 minutes"). Recurring cron only. One-shots can come later as a separate skill if demanded.
- Not distributed. Single-process, single-host, matches MiniClaw's Pi deployment model.
- Not user-facing cron syntax. Users speak natural language; Claude translates to cron at creation time.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ main.py                                                 │
│ ├─ voice loop (existing)                                │
│ └─ SchedulerThread (new, daemon thread)                 │
│     ├─ reads ~/.miniclaw/schedules.yaml on start        │
│     ├─ every 30s: mtime check + compute due schedules   │
│     └─ on fire → enqueues ScheduledFire                 │
│                                                         │
│ Orchestrator (existing) gains:                          │
│ ├─ scheduled_fire_queue: queue.Queue[ScheduledFire]     │
│ ├─ pending_next_wake_announcements: list[str]           │
│ └─ process_scheduled_fire(fire) -> None                 │
│                                                         │
│ core/scheduler.py (new)                                 │
│ ├─ SchedulerThread                                      │
│ ├─ SchedulesStore (yaml load/save, thread-safe)         │
│ ├─ ScheduleEntry (dataclass)                            │
│ └─ ScheduledFire (dataclass: entry + fired_at)          │
│                                                         │
│ skills/schedule/ (new native skill)                     │
│ ├─ SKILL.md                                             │
│ └─ config.yaml (type: native)                           │
│                                                         │
│ container_manager._execute_native_skill                 │
│ └─ new branch: "schedule" → SchedulesStore CRUD         │
└─────────────────────────────────────────────────────────┘
```

### New files

- `core/scheduler.py` — thread, store, dataclasses.
- `skills/schedule/SKILL.md` — routing instructions for Claude.
- `skills/schedule/config.yaml` — `type: native`, no image, no env passthrough.
- `tests/test_scheduler.py` — unit + integration.
- `scripts/test_scheduler_harness.py` — end-to-end harness with fake clock.

### Touched files

- `core/orchestrator.py` — add fire queue, `process_scheduled_fire`, `pending_next_wake_announcements` drain on each voice turn.
- `core/container_manager.py` — register `schedule` as a native skill; needs `SchedulesStore` injection.
- `main.py` — instantiate `SchedulerThread`, wire references, start/stop lifecycle.

### Dependencies

- `croniter` — add to `requirements.txt`. Pure Python, no extensions, ~40KB.

## Execution model

Scheduled tasks execute **natural-language prompts through the full orchestrator**, not fixed skill calls. Reasons documented in the design discussion:

- Matches how users naturally express schedules.
- Reuses the existing tier router / tool loop / memory injection stack.
- One schedule can invoke multiple skills in one fire.
- Prompts survive skill renames; pinned skill IDs would not.

The cost of a Claude round-trip per fire is mitigated by the existing `TierRouter` — deterministic patterns and Ollama routing take the cheap path when reasoning isn't needed.

## Storage

Single YAML file: `~/.miniclaw/schedules.yaml` (same root as the memory vault, Obsidian-friendly).

```yaml
schedules:
  - id: sch_a3f1
    label: morning briefing
    cron: "0 8 * * *"
    prompt: "tell me the weather and top news"
    delivery: next_wake
    created: 2026-04-19T14:22:00
    last_fired: 2026-04-19T08:00:00
    disabled: false
```

Fields:

| Field | Type | Notes |
|---|---|---|
| `id` | string | Auto-assigned `sch_` + 4 hex chars. Stable. |
| `label` | string? | Optional human label for voice cancel. |
| `cron` | string | Standard 5-field cron, validated with `croniter`. |
| `prompt` | string | Natural-language prompt fed to the orchestrator. |
| `delivery` | enum | `immediate`, `next_wake`, or `silent`. |
| `created` | ISO timestamp | Creation time. |
| `last_fired` | ISO timestamp? | Informational only; NOT used for catch-up. |
| `disabled` | bool | Default `false`. Lets users pause without deleting. |

### Store contract

`SchedulesStore` exposes:

- `load() -> list[ScheduleEntry]` — read + validate yaml; on parse error, log and return empty list without overwriting.
- `save(entries) -> None` — atomic write (`schedules.yaml.tmp` + rename).
- `create(entry) -> ScheduleEntry` — assign id, append, save.
- `cancel(id_or_label) -> ScheduleEntry | None` — fuzzy match (exact id first, then case-insensitive label).
- `modify(id_or_label, **updates) -> ScheduleEntry | None` — same match semantics.
- `list_all() -> list[ScheduleEntry]` — enabled schedules only.
- `update_last_fired(id, ts) -> None` — persists the fired-at timestamp atomically, called from the scheduler loop immediately before enqueue.
- Thread-safe via a single `threading.Lock`.

## Scheduler loop

- Daemon thread, 30-second tick interval. (Cron granularity is one minute; 30s gives us headroom without being wasteful.)
- Each tick starts with an mtime check on `schedules.yaml` — if the file changed since last load, reload before computing fires. Supports manual edits without a restart, bounded by at most one tick of lag.
- Fire calculation per tick:
  ```
  for entry in store.list_all():
      if entry.disabled: continue
      cron = croniter(entry.cron, start_time=entry.last_fired or entry.created)
      next_due = cron.get_next(datetime)
      if next_due <= now:
          enqueue(ScheduledFire(entry=entry, fired_at=now))
          store.update_last_fired(entry.id, now)
  ```
  *(The `last_fired` update is persisted before enqueue to avoid double-fire on a scheduler restart.)*
- Startup catch-up is **disabled by default** — implements "skip missed fires silently." Concretely: on first load, for any schedule where `next_due` (computed from `last_fired or created`) is already in the past, the scheduler bumps `last_fired` to `now` without firing. Subsequent ticks then compute `next_due` in the future, so the missed window is skipped cleanly.
  - `next_wake` schedules need no special handling — queued announcements live only in-process memory, so a crash/reboot before drain effectively skips them anyway.

## Delivery modes

Three values for `delivery`:

| Mode | Behavior when voice is idle | Behavior during conversation | Behavior during TTS |
|---|---|---|---|
| `immediate` | Speak via TTS | Downgrade to `next_wake` | Queue, speak after current TTS completes |
| `next_wake` | Append to `pending_next_wake_announcements` | Append | Append |
| `silent` | Write to `~/.miniclaw/scheduler.log` | Same | Same |

**Concurrency rule:** if the orchestrator detects an active conversation session (i.e., within `CONVERSATION_IDLE_TIMEOUT`), `immediate` downgrades to `next_wake` automatically. This preserves the "never interrupt" guarantee without user configuration.

**Next-wake drain:** when the voice loop detects a wake word, before calling `listen()`, the orchestrator speaks any pending announcements via TTS as a preamble: *"Before we chat — \<announcement 1\>. \<announcement 2\>."* Then continues into the normal listen cycle. `pending_next_wake_announcements` is cleared on drain.

## Skill interface

Single native skill `schedule` with four actions. Input schema:

```json
// create
{
  "action": "create",
  "cron": "0 8 * * *",
  "prompt": "tell me the weather and top news",
  "delivery": "next_wake",
  "label": "morning briefing"
}

// list
{"action": "list"}

// cancel
{"action": "cancel", "id_or_label": "morning briefing"}

// modify
{"action": "modify", "id_or_label": "morning briefing", "cron": "0 9 * * *"}
```

Output is a JSON envelope matching `save_memory` conventions:

```json
{"status": "ok", "schedule": {...}, "message": "scheduled morning briefing at 8am daily"}
{"status": "error", "message": "invalid cron expression: 0 99 * * *"}
```

### Claude routing instructions (`SKILL.md` excerpt)

- Translates natural-language timing to a cron expression at creation time.
- Always speaks the resolved cron back in English before calling the tool: *"I'll tell you the weather at 8am every day. Say confirm."*
- Chooses `delivery` by inspecting phrasing:
  - contains "remind me", "alert me", "tell me now", "right away" → `immediate`
  - contains "silently", "in the background", "just log" → `silent`
  - otherwise → `next_wake` (the safe default)
- For cancel: if the user's phrasing matches multiple schedules, lists them and asks which.
- For list: formats output as natural speech ("you have three scheduled items: a morning briefing at 8am, ...").

### Safety gates

- Two-step voice confirmation for create/modify/cancel (same pattern as `set_env_var`): Claude reads the resolved schedule back, waits for "confirm", then calls the tool.
- Hard cap of 50 schedules per user (rejected at `create` time). Prevents runaway growth.
- Cron validation via `croniter` at creation; malformed expressions rejected before write.

## Orchestrator changes

Three additions:

1. `self.scheduled_fire_queue: queue.Queue[ScheduledFire]` — thread-safe producer/consumer between `SchedulerThread` and the voice loop.
2. `self.pending_next_wake_announcements: list[str]` — FIFO, drained in insertion order on wake-word detection.
3. `def process_scheduled_fire(self, fire: ScheduledFire) -> None` — called from the voice loop between turns:
   - Resolves effective delivery mode (applies the conversation-downgrade rule).
   - Runs `fire.entry.prompt` through the normal tool-use loop.
   - Dispatches output by mode.

Voice loop integration (`main.py`): between each wake/listen iteration, drain any pending fires. During an active conversation, the queue is left alone — fires accumulate and are processed once the session ends.

## Error handling

| Error | Behavior |
|---|---|
| Invalid cron on create | Skill returns `{"status": "error"}`. Nothing written. |
| yaml file corrupted on load | Log error to `scheduler.log`. Empty store. DO NOT overwrite file. One-time voice warning on next wake. |
| Fire prompt errors (API down, tool failure) | Log to `scheduler.log`. No retry. No TTS. Silent to user. |
| Scheduler thread crashes | Caught at thread boundary. Log. Restart after 60s. |
| Clock skew / NTP jump | `croniter.get_next()` handles it. Worst case: one delayed or skipped fire. |
| Schedule cap exceeded (>50) | Skill returns `{"status": "error", "message": "schedule limit reached"}`. |

Logs live at `~/.miniclaw/scheduler.log`, rotated by size (keep last 1MB).

## Testing

Three layers, matching the existing test taxonomy.

### Unit tests (`tests/test_scheduler.py`)

- `SchedulesStore` yaml round-trip (create/read/update/delete).
- Atomic write: partial failure leaves original file intact.
- Corrupt yaml: store returns empty list, original file untouched.
- `ScheduleEntry` validation: cron syntax, delivery enum, label length.
- Fuzzy cancel matching (exact id beats label; label is case-insensitive).
- 50-entry cap enforcement.
- Fire computation with injected `now` — verify next-due logic for daily, weekly, hourly, weekday-only expressions.

### Integration tests

- `SchedulerThread` with a fake clock fixture (freezegun or manual tick).
- Mock orchestrator: assert `process_scheduled_fire` called at the right wall-clock moments.
- Skip-missed-fires behavior: set `last_fired` in the past, start scheduler, verify no backlog fires.
- Hot reload: modify yaml mid-run, verify new schedule picked up within one tick + mtime window.

### End-to-end (`scripts/test_scheduler_harness.py`)

- Spin up real `SchedulerThread` + real yaml file + stub orchestrator that captures fires.
- Exercise: create → wait past fire time → assert stub was called → cancel → wait another fire → assert no additional call.
- No voice hardware, no Docker, no Claude API. Runs in ~5s.

Wired into `./scripts/test.sh` alongside the existing `test_voice_mode_harness.py`.

## Migration / backward compatibility

- Zero breaking changes. No new env vars required.
- Users without `schedules.yaml` get default behavior (scheduler thread runs but fires nothing).
- Adds one dependency (`croniter`) — rebuilt on next `./run.sh`.

## Open questions (deferred)

These were considered and deliberately deferred:

- **One-shot "remind me in 10 minutes" timers.** Different data shape, different UX. Revisit after cron is stable.
- **Cross-device schedules.** Only relevant if MiniClaw grows a multi-device deployment model.
- **Web UI for schedule management.** The yaml file + Obsidian is enough for now.
- **Catch-up policy per schedule.** Current skip-silently behavior is the right default; revisit only if a user explicitly asks.

## Success criteria

- User can say *"every morning at 8 tell me the weather"* and it works end-to-end within a single conversation.
- `schedules.yaml` is hand-editable and survives MiniClaw restarts.
- A scheduled fire never interrupts an active voice conversation.
- Failed fires never crash the main voice loop.
- `./scripts/test.sh` covers creation, firing, cancellation, and missed-fire semantics without touching voice hardware or Docker.
