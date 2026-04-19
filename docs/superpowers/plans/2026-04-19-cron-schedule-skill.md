# Cron / Scheduled Task Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a voice-creatable recurring task scheduler to MiniClaw that fires natural-language prompts through the existing orchestrator at cron-defined intervals.

**Architecture:** A daemon `SchedulerThread` in `core/scheduler.py` polls a yaml-backed `SchedulesStore` every 30 seconds, enqueues due fires onto an `Orchestrator`-owned queue, and the orchestrator processes each fire between voice turns. A single native `schedule` skill exposes create/list/cancel/modify actions to Claude. Three delivery modes (`immediate`, `next_wake`, `silent`) control when the output reaches the user.

**Tech Stack:** Python 3.10+, `croniter` for cron parsing, `threading` stdlib, `pyyaml` (already present), `unittest` for tests.

**Spec:** `docs/superpowers/specs/2026-04-19-cron-schedule-skill-design.md`

---

## File Structure

**New files:**
- `core/scheduler.py` — `ScheduleEntry`, `ScheduledFire`, `SchedulesStore`, `SchedulerThread`.
- `skills/schedule/SKILL.md` — Claude routing instructions.
- `skills/schedule/config.yaml` — native skill config.
- `tests/test_scheduler.py` — unit + integration tests for all scheduler pieces.
- `scripts/test_scheduler_harness.py` — end-to-end harness with real thread + stub orchestrator.

**Modified files:**
- `requirements.txt` — add `croniter`.
- `core/orchestrator.py` — add fire queue, `pending_next_wake_announcements`, `process_scheduled_fire`.
- `core/container_manager.py` — register `schedule` native handler, accept `SchedulesStore` injection.
- `main.py` — instantiate `SchedulesStore` + `SchedulerThread`, wire into orchestrator + container manager, start/stop lifecycle.
- `WORKING_MEMORY.md` — note the new native skill and capability.

---

## Task 1: Add croniter dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add croniter to requirements**

Append to `requirements.txt`:

```
croniter>=2.0.0
```

- [ ] **Step 2: Install into the active venv**

Run:

```bash
.venv/bin/pip install -r requirements.txt
```

Expected: `croniter` installs cleanly, no wheel errors.

- [ ] **Step 3: Verify the import works**

Run:

```bash
.venv/bin/python -c "from croniter import croniter; print(croniter('0 8 * * *', __import__('datetime').datetime.now()).get_next())"
```

Expected: prints a future datetime at 08:00 local time.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "deps: add croniter for scheduler"
```

---

## Task 2: ScheduleEntry dataclass + validation

**Files:**
- Create: `core/scheduler.py`
- Create: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests for ScheduleEntry**

Create `tests/test_scheduler.py`:

```python
import unittest
from datetime import datetime

from core.scheduler import ScheduleEntry, ScheduleValidationError


class ScheduleEntryTests(unittest.TestCase):
    def test_creates_with_auto_id(self):
        entry = ScheduleEntry.new(
            cron="0 8 * * *",
            prompt="tell me the weather",
            delivery="next_wake",
        )
        self.assertTrue(entry.id.startswith("sch_"))
        self.assertEqual(len(entry.id), 8)  # sch_ + 4 hex chars
        self.assertEqual(entry.cron, "0 8 * * *")
        self.assertEqual(entry.delivery, "next_wake")
        self.assertFalse(entry.disabled)
        self.assertIsNone(entry.last_fired)
        self.assertIsInstance(entry.created, datetime)

    def test_rejects_invalid_cron(self):
        with self.assertRaises(ScheduleValidationError):
            ScheduleEntry.new(cron="not a cron", prompt="x", delivery="immediate")

    def test_rejects_invalid_delivery(self):
        with self.assertRaises(ScheduleValidationError):
            ScheduleEntry.new(cron="0 8 * * *", prompt="x", delivery="bogus")

    def test_rejects_empty_prompt(self):
        with self.assertRaises(ScheduleValidationError):
            ScheduleEntry.new(cron="0 8 * * *", prompt="   ", delivery="immediate")

    def test_label_is_optional_and_trimmed(self):
        entry = ScheduleEntry.new(
            cron="0 8 * * *",
            prompt="p",
            delivery="immediate",
            label="  morning briefing  ",
        )
        self.assertEqual(entry.label, "morning briefing")

    def test_to_dict_and_from_dict_roundtrip(self):
        entry = ScheduleEntry.new(
            cron="*/5 * * * *",
            prompt="check web",
            delivery="silent",
            label="web checker",
        )
        restored = ScheduleEntry.from_dict(entry.to_dict())
        self.assertEqual(restored.id, entry.id)
        self.assertEqual(restored.cron, entry.cron)
        self.assertEqual(restored.prompt, entry.prompt)
        self.assertEqual(restored.delivery, entry.delivery)
        self.assertEqual(restored.label, entry.label)
        self.assertEqual(restored.created, entry.created)
```

- [ ] **Step 2: Run the test, expect failure**

```bash
.venv/bin/python -m unittest tests.test_scheduler -v
```

Expected: `ImportError: cannot import name 'ScheduleEntry' from 'core.scheduler'`.

- [ ] **Step 3: Implement ScheduleEntry**

Create `core/scheduler.py`:

```python
"""
Scheduler - Recurring task execution for MiniClaw.

Provides ScheduleEntry (one scheduled task), SchedulesStore (yaml-backed
persistence), ScheduledFire (a due-for-execution notification), and
SchedulerThread (the polling loop that turns cron hits into fires).

Fires are enqueued onto Orchestrator.scheduled_fire_queue and processed
between voice turns so they never interrupt an active conversation.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from croniter import croniter, CroniterBadCronError

logger = logging.getLogger(__name__)


DELIVERY_MODES = ("immediate", "next_wake", "silent")


class ScheduleValidationError(ValueError):
    """Raised when a schedule's fields fail validation."""


def _new_id() -> str:
    return "sch_" + secrets.token_hex(2)  # 4 hex chars


@dataclass
class ScheduleEntry:
    id: str
    cron: str
    prompt: str
    delivery: str
    created: datetime
    label: Optional[str] = None
    last_fired: Optional[datetime] = None
    disabled: bool = False

    @classmethod
    def new(
        cls,
        *,
        cron: str,
        prompt: str,
        delivery: str,
        label: str | None = None,
    ) -> "ScheduleEntry":
        cron = (cron or "").strip()
        prompt = (prompt or "").strip()
        label = label.strip() if label else None
        if not prompt:
            raise ScheduleValidationError("prompt must be non-empty")
        if delivery not in DELIVERY_MODES:
            raise ScheduleValidationError(
                f"delivery must be one of {DELIVERY_MODES}, got {delivery!r}"
            )
        try:
            croniter(cron)
        except (CroniterBadCronError, ValueError) as exc:
            raise ScheduleValidationError(f"invalid cron expression: {cron!r} ({exc})")
        return cls(
            id=_new_id(),
            cron=cron,
            prompt=prompt,
            delivery=delivery,
            label=label or None,
            created=datetime.now(),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "cron": self.cron,
            "prompt": self.prompt,
            "delivery": self.delivery,
            "label": self.label,
            "created": self.created.isoformat(),
            "last_fired": self.last_fired.isoformat() if self.last_fired else None,
            "disabled": self.disabled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduleEntry":
        def _dt(value):
            if value is None:
                return None
            if isinstance(value, datetime):
                return value
            return datetime.fromisoformat(value)

        return cls(
            id=data["id"],
            cron=data["cron"],
            prompt=data["prompt"],
            delivery=data["delivery"],
            label=data.get("label"),
            created=_dt(data["created"]),
            last_fired=_dt(data.get("last_fired")),
            disabled=bool(data.get("disabled", False)),
        )
```

- [ ] **Step 4: Run tests, expect pass**

```bash
.venv/bin/python -m unittest tests.test_scheduler -v
```

Expected: all 6 `ScheduleEntryTests` pass.

- [ ] **Step 5: Commit**

```bash
git add core/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): add ScheduleEntry with validation and serialization"
```

---

## Task 3: SchedulesStore — yaml load/save + atomic write

**Files:**
- Modify: `core/scheduler.py`
- Modify: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests for SchedulesStore**

Append to `tests/test_scheduler.py`:

```python
import tempfile
from pathlib import Path


class SchedulesStoreTests(unittest.TestCase):
    def setUp(self):
        from core.scheduler import SchedulesStore
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "schedules.yaml"
        self.store = SchedulesStore(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_empty_when_file_missing(self):
        self.assertEqual(self.store.list_all(), [])

    def test_save_and_load_roundtrip(self):
        from core.scheduler import ScheduleEntry
        entry = ScheduleEntry.new(cron="0 8 * * *", prompt="p", delivery="next_wake")
        self.store.create(entry)

        other = type(self.store)(self.path)
        loaded = other.list_all()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].id, entry.id)
        self.assertEqual(loaded[0].cron, "0 8 * * *")

    def test_corrupt_yaml_returns_empty_without_overwriting(self):
        from core.scheduler import SchedulesStore
        self.path.write_text("this is: : not valid: yaml: [", encoding="utf-8")
        store = SchedulesStore(self.path)
        self.assertEqual(store.list_all(), [])
        # original file must be preserved
        self.assertIn("not valid", self.path.read_text(encoding="utf-8"))

    def test_atomic_write_leaves_no_tmp_file(self):
        from core.scheduler import ScheduleEntry
        entry = ScheduleEntry.new(cron="0 8 * * *", prompt="p", delivery="immediate")
        self.store.create(entry)
        siblings = list(self.path.parent.iterdir())
        self.assertEqual([p.name for p in siblings], ["schedules.yaml"])
```

- [ ] **Step 2: Run tests, expect failure**

```bash
.venv/bin/python -m unittest tests.test_scheduler.SchedulesStoreTests -v
```

Expected: `ImportError: cannot import name 'SchedulesStore'`.

- [ ] **Step 3: Implement SchedulesStore (load/save/create/list)**

Append to `core/scheduler.py`:

```python
class SchedulesStore:
    """
    YAML-backed, thread-safe store for ScheduleEntry records.

    File format:
        schedules:
          - id: sch_a3f1
            cron: "0 8 * * *"
            ...

    On corrupted yaml, load returns an empty list without rewriting the
    file so the user can recover manually.
    """

    MAX_SCHEDULES = 50

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._entries: list[ScheduleEntry] = []
        self._last_mtime: float = 0.0
        self._load_from_disk()

    # ---- public API ----

    def list_all(self) -> list[ScheduleEntry]:
        with self._lock:
            return [e for e in self._entries if not e.disabled]

    def list_raw(self) -> list[ScheduleEntry]:
        """All entries, including disabled ones. For management actions."""
        with self._lock:
            return list(self._entries)

    def create(self, entry: ScheduleEntry) -> ScheduleEntry:
        with self._lock:
            if len(self._entries) >= self.MAX_SCHEDULES:
                raise ScheduleValidationError(
                    f"schedule limit reached ({self.MAX_SCHEDULES})"
                )
            self._entries.append(entry)
            self._save_to_disk()
            return entry

    # ---- internals ----

    def _load_from_disk(self) -> None:
        if not self.path.exists():
            self._entries = []
            self._last_mtime = 0.0
            return
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.error("schedules.yaml is corrupt, running empty: %s", exc)
            self._entries = []
            self._last_mtime = self.path.stat().st_mtime
            return
        items = raw.get("schedules") or []
        parsed: list[ScheduleEntry] = []
        for item in items:
            try:
                parsed.append(ScheduleEntry.from_dict(item))
            except Exception as exc:  # one bad entry shouldn't lose the whole file
                logger.warning("skipping unreadable schedule entry: %s (%s)", item, exc)
        self._entries = parsed
        self._last_mtime = self.path.stat().st_mtime

    def _save_to_disk(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        data = {"schedules": [e.to_dict() for e in self._entries]}
        tmp.write_text(
            yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        tmp.replace(self.path)
        self._last_mtime = self.path.stat().st_mtime
```

- [ ] **Step 4: Run tests, expect pass**

```bash
.venv/bin/python -m unittest tests.test_scheduler.SchedulesStoreTests -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): add SchedulesStore with atomic yaml persistence"
```

---

## Task 4: SchedulesStore — cancel / modify / update_last_fired / cap

**Files:**
- Modify: `core/scheduler.py`
- Modify: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_scheduler.py` inside `SchedulesStoreTests`:

```python
    def test_cancel_by_exact_id(self):
        from core.scheduler import ScheduleEntry
        e = ScheduleEntry.new(cron="0 8 * * *", prompt="p", delivery="immediate")
        self.store.create(e)
        removed = self.store.cancel(e.id)
        self.assertIsNotNone(removed)
        self.assertEqual(removed.id, e.id)
        self.assertEqual(self.store.list_all(), [])

    def test_cancel_by_label_case_insensitive(self):
        from core.scheduler import ScheduleEntry
        e = ScheduleEntry.new(
            cron="0 8 * * *", prompt="p", delivery="immediate", label="Morning Briefing"
        )
        self.store.create(e)
        removed = self.store.cancel("morning briefing")
        self.assertIsNotNone(removed)
        self.assertEqual(removed.id, e.id)

    def test_cancel_returns_none_when_missing(self):
        self.assertIsNone(self.store.cancel("nope"))

    def test_modify_updates_fields(self):
        from core.scheduler import ScheduleEntry
        e = ScheduleEntry.new(
            cron="0 8 * * *", prompt="p", delivery="immediate", label="m"
        )
        self.store.create(e)
        modified = self.store.modify(e.id, cron="0 9 * * *", delivery="next_wake")
        self.assertIsNotNone(modified)
        self.assertEqual(modified.cron, "0 9 * * *")
        self.assertEqual(modified.delivery, "next_wake")
        # Persisted:
        reloaded = type(self.store)(self.path).list_all()[0]
        self.assertEqual(reloaded.cron, "0 9 * * *")

    def test_modify_validates_new_cron(self):
        from core.scheduler import ScheduleEntry, ScheduleValidationError
        e = ScheduleEntry.new(cron="0 8 * * *", prompt="p", delivery="immediate")
        self.store.create(e)
        with self.assertRaises(ScheduleValidationError):
            self.store.modify(e.id, cron="not a cron")

    def test_update_last_fired_persists(self):
        from core.scheduler import ScheduleEntry
        from datetime import datetime
        e = ScheduleEntry.new(cron="0 8 * * *", prompt="p", delivery="immediate")
        self.store.create(e)
        ts = datetime(2026, 4, 19, 8, 0, 0)
        self.store.update_last_fired(e.id, ts)
        reloaded = type(self.store)(self.path).list_all()[0]
        self.assertEqual(reloaded.last_fired, ts)

    def test_create_enforces_cap(self):
        from core.scheduler import ScheduleEntry, ScheduleValidationError
        for _ in range(50):
            self.store.create(
                ScheduleEntry.new(cron="0 8 * * *", prompt="p", delivery="immediate")
            )
        with self.assertRaises(ScheduleValidationError):
            self.store.create(
                ScheduleEntry.new(cron="0 8 * * *", prompt="x", delivery="immediate")
            )
```

- [ ] **Step 2: Run tests, expect failure**

```bash
.venv/bin/python -m unittest tests.test_scheduler.SchedulesStoreTests -v
```

Expected: `AttributeError: 'SchedulesStore' object has no attribute 'cancel'`.

- [ ] **Step 3: Implement cancel/modify/update_last_fired**

Append inside the `SchedulesStore` class (before the `# ---- internals ----` block):

```python
    def cancel(self, id_or_label: str) -> ScheduleEntry | None:
        with self._lock:
            idx = self._find_index(id_or_label)
            if idx is None:
                return None
            removed = self._entries.pop(idx)
            self._save_to_disk()
            return removed

    def modify(self, id_or_label: str, **updates) -> ScheduleEntry | None:
        with self._lock:
            idx = self._find_index(id_or_label)
            if idx is None:
                return None
            current = self._entries[idx]
            new_cron = updates.get("cron", current.cron)
            new_prompt = updates.get("prompt", current.prompt)
            new_delivery = updates.get("delivery", current.delivery)
            new_label = updates.get("label", current.label)
            # Reuse validation by constructing a fresh entry, then copy id/created over.
            fresh = ScheduleEntry.new(
                cron=new_cron,
                prompt=new_prompt,
                delivery=new_delivery,
                label=new_label,
            )
            fresh.id = current.id
            fresh.created = current.created
            fresh.last_fired = current.last_fired
            fresh.disabled = current.disabled
            self._entries[idx] = fresh
            self._save_to_disk()
            return fresh

    def update_last_fired(self, entry_id: str, when: datetime) -> None:
        with self._lock:
            for e in self._entries:
                if e.id == entry_id:
                    e.last_fired = when
                    self._save_to_disk()
                    return

    def _find_index(self, id_or_label: str) -> int | None:
        key = id_or_label.strip()
        for i, e in enumerate(self._entries):
            if e.id == key:
                return i
        key_lower = key.lower()
        for i, e in enumerate(self._entries):
            if e.label and e.label.lower() == key_lower:
                return i
        return None
```

- [ ] **Step 4: Run tests, expect pass**

```bash
.venv/bin/python -m unittest tests.test_scheduler.SchedulesStoreTests -v
```

Expected: all 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): cancel, modify, update_last_fired, 50-cap"
```

---

## Task 5: ScheduledFire + pure fire-computation logic

**Files:**
- Modify: `core/scheduler.py`
- Modify: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_scheduler.py`:

```python
class FireComputationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        from core.scheduler import SchedulesStore
        self.store = SchedulesStore(Path(self._tmp.name) / "schedules.yaml")

    def tearDown(self):
        self._tmp.cleanup()

    def test_fires_when_cron_due(self):
        from core.scheduler import ScheduleEntry, compute_due_fires
        from datetime import datetime, timedelta
        e = ScheduleEntry.new(cron="*/5 * * * *", prompt="p", delivery="immediate")
        e.last_fired = datetime(2026, 4, 19, 8, 0, 0)
        self.store.create(e)
        now = datetime(2026, 4, 19, 8, 7, 0)
        fires = compute_due_fires(self.store, now=now)
        self.assertEqual(len(fires), 1)
        self.assertEqual(fires[0].entry.id, e.id)
        self.assertEqual(fires[0].fired_at, now)

    def test_does_not_fire_before_due(self):
        from core.scheduler import ScheduleEntry, compute_due_fires
        from datetime import datetime
        e = ScheduleEntry.new(cron="0 9 * * *", prompt="p", delivery="immediate")
        e.last_fired = datetime(2026, 4, 19, 8, 30, 0)
        self.store.create(e)
        now = datetime(2026, 4, 19, 8, 45, 0)  # next due is 9:00
        self.assertEqual(compute_due_fires(self.store, now=now), [])

    def test_disabled_entries_do_not_fire(self):
        from core.scheduler import ScheduleEntry, compute_due_fires
        from datetime import datetime
        e = ScheduleEntry.new(cron="* * * * *", prompt="p", delivery="immediate")
        e.disabled = True
        self.store.create(e)
        now = datetime(2026, 4, 19, 8, 0, 0)
        self.assertEqual(compute_due_fires(self.store, now=now), [])

    def test_never_fired_uses_created_as_baseline(self):
        from core.scheduler import ScheduleEntry, compute_due_fires
        from datetime import datetime
        e = ScheduleEntry.new(cron="*/5 * * * *", prompt="p", delivery="immediate")
        e.created = datetime(2026, 4, 19, 8, 0, 0)
        e.last_fired = None
        self.store.create(e)
        now = datetime(2026, 4, 19, 8, 10, 0)
        fires = compute_due_fires(self.store, now=now)
        self.assertEqual(len(fires), 1)
```

- [ ] **Step 2: Run tests, expect failure**

```bash
.venv/bin/python -m unittest tests.test_scheduler.FireComputationTests -v
```

Expected: `ImportError: cannot import name 'compute_due_fires'`.

- [ ] **Step 3: Implement ScheduledFire + compute_due_fires**

Append to `core/scheduler.py` after the `SchedulesStore` class:

```python
@dataclass
class ScheduledFire:
    entry: ScheduleEntry
    fired_at: datetime


def compute_due_fires(store: "SchedulesStore", now: datetime) -> list[ScheduledFire]:
    """
    Pure function — inspects the store and returns a list of fires that
    are due as of `now`. Does NOT mutate last_fired; the caller is
    responsible for persisting that after a successful enqueue.

    Uses (last_fired or created) as the baseline for cron iteration so
    the first fire after creation respects the cron pattern.
    """
    fires: list[ScheduledFire] = []
    for entry in store.list_all():
        baseline = entry.last_fired or entry.created
        try:
            next_due = croniter(entry.cron, start_time=baseline).get_next(datetime)
        except (CroniterBadCronError, ValueError) as exc:
            logger.warning("skipping schedule %s with bad cron: %s", entry.id, exc)
            continue
        if next_due <= now:
            fires.append(ScheduledFire(entry=entry, fired_at=now))
    return fires
```

- [ ] **Step 4: Run tests, expect pass**

```bash
.venv/bin/python -m unittest tests.test_scheduler.FireComputationTests -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): pure compute_due_fires with injected clock"
```

---

## Task 6: Startup skip-missed behavior

**Files:**
- Modify: `core/scheduler.py`
- Modify: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_scheduler.py`:

```python
class StartupCatchupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        from core.scheduler import SchedulesStore
        self.store = SchedulesStore(Path(self._tmp.name) / "schedules.yaml")

    def tearDown(self):
        self._tmp.cleanup()

    def test_bumps_stale_last_fired_without_firing(self):
        from core.scheduler import (
            ScheduleEntry, skip_missed_on_startup, compute_due_fires,
        )
        from datetime import datetime
        e = ScheduleEntry.new(cron="0 8 * * *", prompt="p", delivery="immediate")
        e.last_fired = datetime(2026, 4, 18, 8, 0, 0)  # yesterday
        self.store.create(e)

        now = datetime(2026, 4, 19, 12, 0, 0)  # well past today's 8am
        skip_missed_on_startup(self.store, now=now)

        # immediate fires should not accumulate on next tick
        self.assertEqual(compute_due_fires(self.store, now=now), [])
        # last_fired was bumped to now
        reloaded = self.store.list_raw()[0]
        self.assertEqual(reloaded.last_fired, now)

    def test_leaves_fresh_schedules_alone(self):
        from core.scheduler import ScheduleEntry, skip_missed_on_startup
        from datetime import datetime
        e = ScheduleEntry.new(cron="0 8 * * *", prompt="p", delivery="immediate")
        e.last_fired = datetime(2026, 4, 19, 8, 0, 0)
        self.store.create(e)

        now = datetime(2026, 4, 19, 8, 30, 0)  # 30 min after most recent fire
        skip_missed_on_startup(self.store, now=now)

        reloaded = self.store.list_raw()[0]
        self.assertEqual(reloaded.last_fired, datetime(2026, 4, 19, 8, 0, 0))
```

- [ ] **Step 2: Run tests, expect failure**

```bash
.venv/bin/python -m unittest tests.test_scheduler.StartupCatchupTests -v
```

Expected: `ImportError: cannot import name 'skip_missed_on_startup'`.

- [ ] **Step 3: Implement skip_missed_on_startup**

Append to `core/scheduler.py`:

```python
def skip_missed_on_startup(store: "SchedulesStore", now: datetime) -> None:
    """
    For every schedule whose next_due (from last_fired or created) is
    already in the past, bump last_fired to `now`. This is how
    "skip missed fires silently" is implemented — on startup, past
    due-windows are discarded rather than fired.
    """
    for entry in store.list_all():
        baseline = entry.last_fired or entry.created
        try:
            next_due = croniter(entry.cron, start_time=baseline).get_next(datetime)
        except (CroniterBadCronError, ValueError):
            continue
        if next_due <= now:
            store.update_last_fired(entry.id, now)
```

- [ ] **Step 4: Run tests, expect pass**

```bash
.venv/bin/python -m unittest tests.test_scheduler.StartupCatchupTests -v
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): skip missed fires on startup"
```

---

## Task 7: SchedulerThread — real threading lifecycle

**Files:**
- Modify: `core/scheduler.py`
- Modify: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_scheduler.py`:

```python
class SchedulerThreadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        from core.scheduler import SchedulesStore
        self.store = SchedulesStore(Path(self._tmp.name) / "schedules.yaml")

    def tearDown(self):
        self._tmp.cleanup()

    def test_start_then_stop_cleanly(self):
        from core.scheduler import SchedulerThread
        import queue
        q = queue.Queue()
        thread = SchedulerThread(store=self.store, fire_queue=q, tick_seconds=0.05)
        thread.start()
        time.sleep(0.2)  # let it spin a few times
        thread.stop()
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())

    def test_due_schedule_enqueues_fire(self):
        from core.scheduler import SchedulerThread, ScheduleEntry
        from datetime import datetime, timedelta
        import queue

        e = ScheduleEntry.new(cron="* * * * *", prompt="p", delivery="immediate")
        # force "already due" by pretending last_fired was long ago and created even earlier
        e.last_fired = datetime.now() - timedelta(minutes=5)
        e.created = datetime.now() - timedelta(minutes=10)
        self.store.create(e)

        q = queue.Queue()
        thread = SchedulerThread(store=self.store, fire_queue=q, tick_seconds=0.05)
        thread.start()
        try:
            fire = q.get(timeout=2.0)
        finally:
            thread.stop()
            thread.join(timeout=2.0)
        self.assertEqual(fire.entry.id, e.id)

    def test_last_fired_persisted_before_enqueue(self):
        # After a fire, the next tick must not re-fire the same schedule.
        from core.scheduler import SchedulerThread, ScheduleEntry
        from datetime import datetime, timedelta
        import queue

        e = ScheduleEntry.new(cron="* * * * *", prompt="p", delivery="immediate")
        e.last_fired = datetime.now() - timedelta(minutes=5)
        e.created = datetime.now() - timedelta(minutes=10)
        self.store.create(e)

        q = queue.Queue()
        thread = SchedulerThread(store=self.store, fire_queue=q, tick_seconds=0.05)
        thread.start()
        try:
            q.get(timeout=2.0)  # first fire
            # wait several ticks to confirm no duplicate
            time.sleep(0.4)
            extra = []
            while not q.empty():
                extra.append(q.get_nowait())
            self.assertEqual(extra, [])
        finally:
            thread.stop()
            thread.join(timeout=2.0)
```

- [ ] **Step 2: Run tests, expect failure**

```bash
.venv/bin/python -m unittest tests.test_scheduler.SchedulerThreadTests -v
```

Expected: `ImportError: cannot import name 'SchedulerThread'`.

- [ ] **Step 3: Implement SchedulerThread**

Append to `core/scheduler.py`:

```python
class SchedulerThread(threading.Thread):
    """
    Polls the store and enqueues due fires. Runs as a daemon so it
    doesn't block interpreter shutdown.

    Thread lifecycle:
      - start(): begin ticking
      - stop():  request shutdown; next tick exits
      - join():  wait for exit

    Fire ordering:
      1. compute_due_fires(now)
      2. update_last_fired(entry.id, now)   ← persisted BEFORE enqueue
      3. fire_queue.put(fire)

    This ordering prevents duplicate fires if the consumer is slow.
    """

    def __init__(
        self,
        *,
        store: SchedulesStore,
        fire_queue,                    # queue.Queue[ScheduledFire]
        tick_seconds: float = 30.0,
    ):
        super().__init__(name="MiniClawScheduler", daemon=True)
        self._store = store
        self._queue = fire_queue
        self._tick_seconds = tick_seconds
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        # One-time startup skip for missed fires.
        try:
            skip_missed_on_startup(self._store, now=datetime.now())
        except Exception:
            logger.exception("skip_missed_on_startup failed")

        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("scheduler tick crashed; continuing")
            self._stop_event.wait(self._tick_seconds)

    def _tick(self) -> None:
        now = datetime.now()
        for fire in compute_due_fires(self._store, now=now):
            self._store.update_last_fired(fire.entry.id, now)
            self._queue.put(fire)
```

- [ ] **Step 4: Run tests, expect pass**

```bash
.venv/bin/python -m unittest tests.test_scheduler.SchedulerThreadTests -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): SchedulerThread with safe fire ordering"
```

---

## Task 8: SchedulerThread — mtime hot reload

**Files:**
- Modify: `core/scheduler.py`
- Modify: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_scheduler.py`:

```python
class HotReloadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "schedules.yaml"

    def tearDown(self):
        self._tmp.cleanup()

    def test_external_edit_is_picked_up(self):
        from core.scheduler import SchedulesStore, SchedulerThread, ScheduleEntry
        from datetime import datetime, timedelta
        import queue

        store = SchedulesStore(self.path)
        q = queue.Queue()
        thread = SchedulerThread(store=store, fire_queue=q, tick_seconds=0.05)
        thread.start()
        try:
            # No schedules yet — nothing should fire.
            time.sleep(0.2)
            self.assertTrue(q.empty())

            # Second store instance simulating an external edit (e.g. Obsidian).
            other = SchedulesStore(self.path)
            e = ScheduleEntry.new(
                cron="* * * * *", prompt="p", delivery="immediate"
            )
            e.last_fired = datetime.now() - timedelta(minutes=5)
            e.created = datetime.now() - timedelta(minutes=10)
            # ensure mtime bumps on filesystems with 1-second granularity
            time.sleep(1.1)
            other.create(e)

            fire = q.get(timeout=3.0)
            self.assertEqual(fire.entry.id, e.id)
        finally:
            thread.stop()
            thread.join(timeout=2.0)
```

- [ ] **Step 2: Run tests, expect failure**

```bash
.venv/bin/python -m unittest tests.test_scheduler.HotReloadTests -v
```

Expected: test times out — the in-memory store instance never sees the external write.

- [ ] **Step 3: Implement mtime hot reload**

Inside `SchedulesStore`, add at the bottom of the class:

```python
    def reload_if_changed(self) -> bool:
        """Re-read the file if its mtime advanced. Returns True on reload."""
        with self._lock:
            if not self.path.exists():
                if self._entries:
                    self._entries = []
                    self._last_mtime = 0.0
                    return True
                return False
            current = self.path.stat().st_mtime
            if current <= self._last_mtime:
                return False
            self._load_from_disk()
            return True
```

Inside `SchedulerThread._tick`, before computing fires, call:

```python
    def _tick(self) -> None:
        self._store.reload_if_changed()
        now = datetime.now()
        for fire in compute_due_fires(self._store, now=now):
            self._store.update_last_fired(fire.entry.id, now)
            self._queue.put(fire)
```

- [ ] **Step 4: Run tests, expect pass**

```bash
.venv/bin/python -m unittest tests.test_scheduler -v
```

Expected: all scheduler tests pass, including hot-reload.

- [ ] **Step 5: Commit**

```bash
git add core/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): mtime-based hot reload"
```

---

## Task 9: Orchestrator — fire queue + pending announcements

**Files:**
- Modify: `core/orchestrator.py`
- Modify: `tests/test_orchestrator_routing.py` (or create `tests/test_orchestrator_scheduler.py`)

- [ ] **Step 1: Write failing test**

Create `tests/test_orchestrator_scheduler.py`:

```python
import queue
import unittest
from unittest.mock import MagicMock, patch


class OrchestratorSchedulerHooksTests(unittest.TestCase):
    def _make_orchestrator(self):
        # Avoid real Anthropic init and real skill loading.
        with patch("core.orchestrator.anthropic.Anthropic"), \
             patch("core.orchestrator.SkillLoader"), \
             patch("core.orchestrator.ContainerManager"), \
             patch("core.orchestrator.MemoryProvider"), \
             patch("core.orchestrator.PromptBuilder"), \
             patch("core.orchestrator.SkillSelector"), \
             patch("core.orchestrator.ToolLoop"):
            from core.orchestrator import Orchestrator
            return Orchestrator(anthropic_api_key="test-key")

    def test_exposes_scheduled_fire_queue(self):
        orch = self._make_orchestrator()
        self.assertIsInstance(orch.scheduled_fire_queue, queue.Queue)

    def test_exposes_pending_next_wake_announcements(self):
        orch = self._make_orchestrator()
        self.assertEqual(orch.pending_next_wake_announcements, [])

    def test_drain_pending_announcements_returns_and_clears_fifo(self):
        orch = self._make_orchestrator()
        orch.pending_next_wake_announcements.extend(["a", "b", "c"])
        drained = orch.drain_pending_announcements()
        self.assertEqual(drained, ["a", "b", "c"])
        self.assertEqual(orch.pending_next_wake_announcements, [])
```

- [ ] **Step 2: Run test, expect failure**

```bash
.venv/bin/python -m unittest tests.test_orchestrator_scheduler -v
```

Expected: `AttributeError: 'Orchestrator' object has no attribute 'scheduled_fire_queue'`.

- [ ] **Step 3: Add the three additions to Orchestrator**

In `core/orchestrator.py`, add import at top:

```python
import queue as _queue
```

Inside `Orchestrator.__init__` (end of the method, after existing setup), add:

```python
        # --- scheduler hooks ---
        self.scheduled_fire_queue: _queue.Queue = _queue.Queue()
        self.pending_next_wake_announcements: list[str] = []
```

Add a new method on `Orchestrator`:

```python
    def drain_pending_announcements(self) -> list[str]:
        """Return queued next_wake announcements in FIFO order, clearing them."""
        drained = list(self.pending_next_wake_announcements)
        self.pending_next_wake_announcements.clear()
        return drained
```

- [ ] **Step 4: Run tests, expect pass**

```bash
.venv/bin/python -m unittest tests.test_orchestrator_scheduler -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator.py tests/test_orchestrator_scheduler.py
git commit -m "feat(orchestrator): scheduler fire queue and pending announcements"
```

---

## Task 10: Orchestrator — process_scheduled_fire for each delivery mode

**Files:**
- Modify: `core/orchestrator.py`
- Modify: `tests/test_orchestrator_scheduler.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_orchestrator_scheduler.py`:

```python
class ProcessScheduledFireTests(unittest.TestCase):
    def _make_orchestrator_with_tool_loop(self, tool_loop_result="the weather is sunny"):
        from unittest.mock import patch, MagicMock
        with patch("core.orchestrator.anthropic.Anthropic"), \
             patch("core.orchestrator.SkillLoader"), \
             patch("core.orchestrator.ContainerManager"), \
             patch("core.orchestrator.MemoryProvider"), \
             patch("core.orchestrator.PromptBuilder") as pb, \
             patch("core.orchestrator.SkillSelector"), \
             patch("core.orchestrator.ToolLoop") as tl:
            pb.return_value.build.return_value = "SYSTEM"
            tl.return_value.run.return_value = tool_loop_result
            from core.orchestrator import Orchestrator
            orch = Orchestrator(anthropic_api_key="test-key")
            return orch

    def _make_fire(self, delivery):
        from core.scheduler import ScheduleEntry, ScheduledFire
        from datetime import datetime
        entry = ScheduleEntry.new(
            cron="0 8 * * *", prompt="tell me the weather", delivery=delivery
        )
        return ScheduledFire(entry=entry, fired_at=datetime.now())

    def test_next_wake_appends_to_pending(self):
        orch = self._make_orchestrator_with_tool_loop("weather update")
        orch.process_scheduled_fire(self._make_fire("next_wake"))
        self.assertEqual(orch.pending_next_wake_announcements, ["weather update"])

    def test_immediate_when_idle_calls_speak(self):
        orch = self._make_orchestrator_with_tool_loop("weather update")
        speak_calls = []
        orch.speak_callback = lambda text: speak_calls.append(text)
        orch.process_scheduled_fire(self._make_fire("immediate"))
        self.assertEqual(speak_calls, ["weather update"])
        self.assertEqual(orch.pending_next_wake_announcements, [])

    def test_immediate_downgrades_when_conversation_active(self):
        orch = self._make_orchestrator_with_tool_loop("weather update")
        orch.speak_callback = lambda text: None
        orch.is_conversation_active = lambda: True
        orch.process_scheduled_fire(self._make_fire("immediate"))
        self.assertEqual(orch.pending_next_wake_announcements, ["weather update"])

    def test_silent_logs_and_does_nothing_user_facing(self):
        import tempfile, os
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "scheduler.log"
            orch = self._make_orchestrator_with_tool_loop("web check done")
            orch.scheduler_log_path = log_path
            speak_calls = []
            orch.speak_callback = lambda text: speak_calls.append(text)
            orch.process_scheduled_fire(self._make_fire("silent"))
            self.assertEqual(speak_calls, [])
            self.assertEqual(orch.pending_next_wake_announcements, [])
            self.assertTrue(log_path.exists())
            self.assertIn("web check done", log_path.read_text(encoding="utf-8"))

    def test_tool_loop_failure_does_not_raise(self):
        from unittest.mock import patch
        orch = self._make_orchestrator_with_tool_loop("unused")
        orch.tool_loop.run.side_effect = RuntimeError("API down")
        orch.speak_callback = lambda text: None
        # Should log and return, not raise.
        orch.process_scheduled_fire(self._make_fire("immediate"))
        self.assertEqual(orch.pending_next_wake_announcements, [])
```

- [ ] **Step 2: Run tests, expect failure**

```bash
.venv/bin/python -m unittest tests.test_orchestrator_scheduler.ProcessScheduledFireTests -v
```

Expected: `AttributeError: 'Orchestrator' object has no attribute 'process_scheduled_fire'`.

- [ ] **Step 3: Implement process_scheduled_fire**

In `core/orchestrator.py`, add at the top of the file:

```python
from pathlib import Path as _Path
```

Inside `Orchestrator.__init__`, just after the scheduler hooks added in Task 9, add:

```python
        # Injected from main.py — None in text/test mode means "don't speak".
        self.speak_callback = None
        # Predicate injected from main.py. Default to "no active conversation".
        self.is_conversation_active = lambda: False
        # Log destination for silent-mode schedules. Overridden from main.py.
        self.scheduler_log_path: _Path = _Path.home() / ".miniclaw" / "scheduler.log"
```

Add a new method on `Orchestrator`:

```python
    def process_scheduled_fire(self, fire) -> None:
        """
        Execute a scheduled fire through the orchestrator's tool loop and
        dispatch its output based on delivery mode. Never raises — a crash
        here must not take down the voice loop.
        """
        entry = fire.entry
        try:
            system_prompt = self.prompt_builder.build(
                user_message=entry.prompt,
                conversation_state=self.conversation_state,
            )
            output = self.tool_loop.run(
                user_message=entry.prompt,
                system_prompt=system_prompt,
            )
        except Exception:
            logger.exception("scheduled fire %s failed during tool loop", entry.id)
            return

        delivery = entry.delivery
        if delivery == "immediate" and self.is_conversation_active():
            delivery = "next_wake"  # concurrency downgrade

        if delivery == "immediate":
            if self.speak_callback is not None:
                try:
                    self.speak_callback(output)
                except Exception:
                    logger.exception("speak_callback failed for schedule %s", entry.id)
            else:
                logger.info("[sched %s immediate, no speak_callback] %s", entry.id, output)
        elif delivery == "next_wake":
            self.pending_next_wake_announcements.append(output)
        elif delivery == "silent":
            try:
                self.scheduler_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.scheduler_log_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"[{fire.fired_at.isoformat()}] {entry.id}: {output}\n")
            except Exception:
                logger.exception("failed writing silent-schedule log for %s", entry.id)
        else:
            logger.warning("unknown delivery mode %r for schedule %s", delivery, entry.id)
```

- [ ] **Step 4: Run tests, expect pass**

```bash
.venv/bin/python -m unittest tests.test_orchestrator_scheduler -v
```

Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator.py tests/test_orchestrator_scheduler.py
git commit -m "feat(orchestrator): process_scheduled_fire with delivery-mode dispatch"
```

---

## Task 11: `schedule` native skill files

**Files:**
- Create: `skills/schedule/config.yaml`
- Create: `skills/schedule/SKILL.md`
- Modify: `tests/test_scheduler.py`

- [ ] **Step 1: Write a failing loader test**

Append to `tests/test_scheduler.py`:

```python
class ScheduleSkillLoadsTests(unittest.TestCase):
    def test_skill_loader_picks_up_schedule(self):
        from core.skill_loader import SkillLoader
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        loader = SkillLoader(skill_paths=[repo_root / "skills"])
        loader.load_all()
        names = [s.name for s in loader.eligible_skills()]
        self.assertIn("schedule", names)
```

- [ ] **Step 2: Run test, expect failure**

```bash
.venv/bin/python -m unittest tests.test_scheduler.ScheduleSkillLoadsTests -v
```

Expected: `AssertionError: 'schedule' not found in [...]`.

- [ ] **Step 3: Create config.yaml**

Write `skills/schedule/config.yaml`:

```yaml
type: native
timeout_seconds: 10
```

- [ ] **Step 4: Create SKILL.md**

Write `skills/schedule/SKILL.md`:

```markdown
---
name: schedule
description: Create, list, cancel, or modify recurring scheduled tasks the assistant fires on a cron schedule.
---

# Schedule

Use this skill when the user wants something to happen on a repeating schedule — morning briefings, hourly checks, weekly summaries, and so on. Do not use it for one-shot reminders like "in 10 minutes" (no one-shot support yet).

## When to use

- "Every morning at 8 tell me the weather"
- "Every weekday at 7am open the dashboard"
- "What do I have scheduled?"
- "Cancel my morning briefing"
- "Change my morning briefing to 9am"

## Actions

### create

Convert the user's natural-language timing to a standard 5-field cron expression. Then speak the resolved time back in plain English and wait for the user to say "confirm" before calling the tool.

Choose delivery by phrasing:
- "remind me", "alert me", "tell me now", "right away" → `immediate`
- "silently", "in the background", "just log" → `silent`
- anything else → `next_wake`

Input schema:

```yaml
action: create
cron: "0 8 * * *"
prompt: "tell me the weather and top news"
delivery: next_wake
label: morning briefing   # optional; short, voice-friendly
```

### list

Input: `{"action": "list"}`. Speak the result as natural language: "You have three scheduled items: a morning briefing at 8am every day, ...".

### cancel

Input: `{"action": "cancel", "id_or_label": "morning briefing"}`. If multiple schedules could match, list them and ask which to cancel before calling the tool.

### modify

Input: `{"action": "modify", "id_or_label": "morning briefing", "cron": "0 9 * * *"}`. Only include the fields the user wants to change. Confirm the new schedule in plain English before calling.

## Confirmation rule

For create, cancel, and modify: always read the resolved action back to the user and wait for a "confirm" before calling the tool. This mirrors the `set_env_var` and `save_memory` patterns.
```

- [ ] **Step 5: Run test, expect pass**

```bash
.venv/bin/python -m unittest tests.test_scheduler.ScheduleSkillLoadsTests -v
```

Expected: passes.

- [ ] **Step 6: Commit**

```bash
git add skills/schedule/ tests/test_scheduler.py
git commit -m "feat(schedule-skill): add SKILL.md and config.yaml"
```

---

## Task 12: ContainerManager — schedule native handler

**Files:**
- Modify: `core/container_manager.py`
- Modify: `tests/test_container_manager.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_container_manager.py`:

```python
class ScheduleNativeHandlerTests(unittest.TestCase):
    def setUp(self):
        from core.container_manager import ContainerManager
        from core.scheduler import SchedulesStore
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SchedulesStore(Path(self._tmp.name) / "schedules.yaml")
        self.manager = ContainerManager()
        self.manager._schedules_store = self.store

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_returns_ok_and_persists(self):
        import json
        out = self.manager._execute_schedule({
            "action": "create",
            "cron": "0 8 * * *",
            "prompt": "tell me the weather",
            "delivery": "next_wake",
            "label": "morning briefing",
        })
        payload = json.loads(out)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(self.store.list_raw()[0].label, "morning briefing")

    def test_create_rejects_bad_cron(self):
        import json
        out = self.manager._execute_schedule({
            "action": "create",
            "cron": "not a cron",
            "prompt": "p",
            "delivery": "next_wake",
        })
        payload = json.loads(out)
        self.assertEqual(payload["status"], "error")
        self.assertIn("cron", payload["message"])

    def test_list_returns_all_enabled(self):
        import json
        from core.scheduler import ScheduleEntry
        self.store.create(ScheduleEntry.new(
            cron="0 8 * * *", prompt="a", delivery="next_wake", label="one",
        ))
        self.store.create(ScheduleEntry.new(
            cron="0 9 * * *", prompt="b", delivery="next_wake", label="two",
        ))
        out = self.manager._execute_schedule({"action": "list"})
        payload = json.loads(out)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["schedules"]), 2)

    def test_cancel_by_label_removes(self):
        import json
        from core.scheduler import ScheduleEntry
        self.store.create(ScheduleEntry.new(
            cron="0 8 * * *", prompt="a", delivery="next_wake", label="one",
        ))
        out = self.manager._execute_schedule({
            "action": "cancel", "id_or_label": "one",
        })
        payload = json.loads(out)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(self.store.list_all(), [])

    def test_cancel_missing_returns_error(self):
        import json
        out = self.manager._execute_schedule({
            "action": "cancel", "id_or_label": "nope",
        })
        payload = json.loads(out)
        self.assertEqual(payload["status"], "error")

    def test_modify_updates_cron(self):
        import json
        from core.scheduler import ScheduleEntry
        self.store.create(ScheduleEntry.new(
            cron="0 8 * * *", prompt="a", delivery="next_wake", label="one",
        ))
        out = self.manager._execute_schedule({
            "action": "modify", "id_or_label": "one", "cron": "0 9 * * *",
        })
        payload = json.loads(out)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(self.store.list_raw()[0].cron, "0 9 * * *")

    def test_unknown_action_returns_error(self):
        import json
        out = self.manager._execute_schedule({"action": "bogus"})
        payload = json.loads(out)
        self.assertEqual(payload["status"], "error")
```

- [ ] **Step 2: Run tests, expect failure**

```bash
.venv/bin/python -m unittest tests.test_container_manager.ScheduleNativeHandlerTests -v
```

Expected: `AttributeError: 'ContainerManager' object has no attribute '_execute_schedule'`.

- [ ] **Step 3: Register handler and implement it**

In `core/container_manager.py`:

At the top of the file, add:

```python
from core.scheduler import ScheduleEntry, ScheduleValidationError
```

In `ContainerManager.__init__`, after the existing `self._orchestrator = None` line, add:

```python
        self._schedules_store = None  # injected from main.py
```

Add `"schedule"` to the `_native_handlers` dict:

```python
        self._native_handlers = {
            "install_skill": self._execute_install_skill,
            "set_env_var": self._execute_set_env_var,
            "save_memory": self._execute_save_memory,
            "dashboard": self._execute_dashboard,
            "soundcloud_play": self._execute_soundcloud,
            "schedule": self._execute_schedule,
        }
```

Add the handler method on `ContainerManager`:

```python
    def _execute_schedule(self, tool_input: dict) -> str:
        """Native handler for the `schedule` skill."""
        import json as _json

        if self._schedules_store is None:
            return _json.dumps({
                "status": "error",
                "message": "scheduler not initialized",
            })

        action = tool_input.get("action")
        store = self._schedules_store

        try:
            if action == "create":
                entry = ScheduleEntry.new(
                    cron=tool_input.get("cron", ""),
                    prompt=tool_input.get("prompt", ""),
                    delivery=tool_input.get("delivery", "next_wake"),
                    label=tool_input.get("label"),
                )
                store.create(entry)
                return _json.dumps({
                    "status": "ok",
                    "schedule": entry.to_dict(),
                    "message": f"scheduled {entry.label or entry.id}",
                })

            if action == "list":
                return _json.dumps({
                    "status": "ok",
                    "schedules": [e.to_dict() for e in store.list_all()],
                })

            if action == "cancel":
                removed = store.cancel(tool_input.get("id_or_label", ""))
                if removed is None:
                    return _json.dumps({
                        "status": "error",
                        "message": f"no schedule matching "
                                   f"{tool_input.get('id_or_label')!r}",
                    })
                return _json.dumps({
                    "status": "ok",
                    "schedule": removed.to_dict(),
                    "message": f"cancelled {removed.label or removed.id}",
                })

            if action == "modify":
                updates = {
                    k: tool_input[k]
                    for k in ("cron", "prompt", "delivery", "label")
                    if k in tool_input
                }
                modified = store.modify(
                    tool_input.get("id_or_label", ""), **updates
                )
                if modified is None:
                    return _json.dumps({
                        "status": "error",
                        "message": f"no schedule matching "
                                   f"{tool_input.get('id_or_label')!r}",
                    })
                return _json.dumps({
                    "status": "ok",
                    "schedule": modified.to_dict(),
                    "message": f"modified {modified.label or modified.id}",
                })

            return _json.dumps({
                "status": "error",
                "message": f"unknown action: {action!r}",
            })

        except ScheduleValidationError as exc:
            return _json.dumps({"status": "error", "message": str(exc)})
```

- [ ] **Step 4: Run tests, expect pass**

```bash
.venv/bin/python -m unittest tests.test_container_manager.ScheduleNativeHandlerTests -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/container_manager.py tests/test_container_manager.py
git commit -m "feat(container-manager): schedule native handler"
```

---

## Task 13: Wire scheduler into main.py

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Locate the orchestrator/container-manager construction**

Run:

```bash
grep -n "Orchestrator(\|ContainerManager\|reload_skills\|KeyboardInterrupt" main.py
```

Expected: shows the lines where the orchestrator and container manager are constructed and where the main loop exits.

- [ ] **Step 2: Add imports**

At the top of `main.py`, alongside other `core.*` imports, add:

```python
from core.scheduler import SchedulesStore, SchedulerThread
```

- [ ] **Step 3: Construct store + thread and wire them**

Immediately after the `Orchestrator(...)` is constructed (and after `ContainerManager` is attached), add:

```python
    # --- scheduler wiring ---
    schedules_path = Path.home() / ".miniclaw" / "schedules.yaml"
    schedules_store = SchedulesStore(schedules_path)

    # Let ContainerManager serve the `schedule` skill.
    orchestrator.container_manager._schedules_store = schedules_store

    # Give the orchestrator the callbacks it needs for delivery modes.
    orchestrator.speak_callback = voice.speak if voice_enabled else (lambda _t: None)
    orchestrator.is_conversation_active = lambda: conversation_state_active[0]
    orchestrator.scheduler_log_path = Path.home() / ".miniclaw" / "scheduler.log"

    scheduler_thread = SchedulerThread(
        store=schedules_store,
        fire_queue=orchestrator.scheduled_fire_queue,
    )
    scheduler_thread.start()
```

**Notes for the engineer:**
- `voice_enabled` is already a boolean in `main.py` (text vs voice mode). If the exact variable name differs, use the equivalent.
- `conversation_state_active` is a 1-element list used as a mutable flag, because the orchestrator needs to *read* the current state. Introduce it near the voice loop:

  ```python
  conversation_state_active = [False]
  ```

  Set `conversation_state_active[0] = True` when entering an active conversation session (after wake word) and `False` when the idle timeout elapses. These toggle points already exist in the voice loop — just attach this flag alongside them.

- [ ] **Step 4: Drain pending announcements at the start of each voice iteration**

In the voice loop, *immediately after wake-word detection and before `voice.listen(...)`*, add:

```python
    pending = orchestrator.drain_pending_announcements()
    if pending and voice_enabled:
        preamble = "Before we chat — " + " ".join(pending)
        voice.speak(preamble)
```

- [ ] **Step 5: Drain the fire queue between voice turns**

Also near the top of each voice iteration (after drain_pending_announcements, before listen), add:

```python
    while not orchestrator.scheduled_fire_queue.empty():
        try:
            fire = orchestrator.scheduled_fire_queue.get_nowait()
        except Exception:
            break
        orchestrator.process_scheduled_fire(fire)
```

This runs *outside* an active conversation — the `is_conversation_active` flag is False at this point, so `immediate` schedules will speak.

During an active conversation, fires accumulate in the queue and are processed on the next wake cycle. This preserves the "never interrupt" rule without any additional synchronization.

- [ ] **Step 6: Stop the thread cleanly on shutdown**

Find the top-level exception handler / `finally` block that cleans up `voice.close()` etc. Add:

```python
    try:
        scheduler_thread.stop()
        scheduler_thread.join(timeout=2.0)
    except Exception:
        pass
```

- [ ] **Step 7: Smoke test**

Run:

```bash
./run.sh --list
```

Expected: `schedule` appears in the loaded-skills list.

Then:

```bash
./run.sh
```

Expected: MiniClaw starts in text mode, no tracebacks, and typing `what do I have scheduled` returns an empty-list response from the skill.

- [ ] **Step 8: Commit**

```bash
git add main.py
git commit -m "feat(main): wire SchedulerThread and scheduled-fire drain"
```

---

## Task 14: End-to-end harness script

**Files:**
- Create: `scripts/test_scheduler_harness.py`

- [ ] **Step 1: Write the harness**

Create `scripts/test_scheduler_harness.py`:

```python
#!/usr/bin/env python3
"""
End-to-end harness for the scheduler.

Exercises the real SchedulerThread + real yaml store against a stub
orchestrator that simply records fires. No voice hardware, no Docker,
no Claude API.

Expected runtime: ~5 seconds.
"""

from __future__ import annotations

import queue
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

# Make repo importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.scheduler import ScheduleEntry, SchedulesStore, SchedulerThread


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        store = SchedulesStore(Path(tmp) / "schedules.yaml")
        q: queue.Queue = queue.Queue()
        thread = SchedulerThread(store=store, fire_queue=q, tick_seconds=0.1)
        thread.start()
        try:
            # Create a schedule that should fire on the next tick.
            entry = ScheduleEntry.new(
                cron="* * * * *", prompt="hello", delivery="immediate",
                label="harness",
            )
            entry.last_fired = datetime.now() - timedelta(minutes=5)
            entry.created = datetime.now() - timedelta(minutes=10)
            store.create(entry)

            fire = q.get(timeout=3.0)
            assert fire.entry.id == entry.id, "wrong fire id"
            print(f"OK: received fire for {entry.label}")

            # Cancel and confirm no further fires within the window.
            store.cancel("harness")
            time.sleep(0.5)
            extra = []
            while not q.empty():
                extra.append(q.get_nowait())
            assert extra == [], f"unexpected fires after cancel: {extra}"
            print("OK: no fires after cancel")

        finally:
            thread.stop()
            thread.join(timeout=2.0)

    print("harness PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Make it executable and run it**

```bash
chmod +x scripts/test_scheduler_harness.py
.venv/bin/python scripts/test_scheduler_harness.py
```

Expected output:

```
OK: received fire for harness
OK: no fires after cancel
harness PASSED
```

- [ ] **Step 3: Hook into `./scripts/test.sh`**

Open `scripts/test.sh`, find the block that runs the voice-mode harness, and add an analogous line for the scheduler harness. If the script follows this pattern:

```bash
if [[ "$1" == "--voice" || "$1" == "--all" ]]; then
  .venv/bin/python scripts/test_voice_mode_harness.py
fi
```

Add, after it:

```bash
if [[ "$1" == "--scheduler" || "$1" == "--all" ]]; then
  .venv/bin/python scripts/test_scheduler_harness.py
fi
```

Then verify:

```bash
./scripts/test.sh --scheduler
```

Expected: harness runs and prints `harness PASSED`.

- [ ] **Step 4: Run the full unittest suite**

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Expected: every test passes, including the new `test_scheduler.py`, `test_orchestrator_scheduler.py`, and the expanded `test_container_manager.py`.

- [ ] **Step 5: Commit**

```bash
git add scripts/test_scheduler_harness.py scripts/test.sh
git commit -m "test(scheduler): end-to-end harness + test.sh wiring"
```

---

## Task 15: Update WORKING_MEMORY.md

**Files:**
- Modify: `WORKING_MEMORY.md`

- [ ] **Step 1: Update the native-skills line and milestones**

Open `WORKING_MEMORY.md`. Find the line:

```
- Current native skills: `dashboard`, `soundcloud_play`, `install_skill`, `set_env_var`, `save_memory`.
```

Replace with:

```
- Current native skills: `dashboard`, `soundcloud_play`, `install_skill`, `set_env_var`, `save_memory`, `schedule`.
```

In the `## Recent Milestones` section, add (keeping the latest at the top of that block):

```
- 2026-04-19:
  - shipped the `schedule` native skill with yaml-backed recurring tasks
  - SchedulerThread drains into the orchestrator between voice turns; never interrupts conversation
  - delivery modes: `immediate`, `next_wake` (default, queues for next wake-word), `silent` (log-only)
  - missed fires are skipped on startup
```

- [ ] **Step 2: Commit**

```bash
git add WORKING_MEMORY.md
git commit -m "docs: note schedule skill in WORKING_MEMORY"
```

---

## Self-Review Notes

**Spec coverage:**

- §Architecture → Tasks 2-8 (`core/scheduler.py` module), Tasks 9-10 (orchestrator additions), Task 13 (main.py wiring).
- §Storage format → Tasks 3-4 (SchedulesStore round-trip and atomic write).
- §Scheduler loop (30s tick, mtime reload, skip-missed) → Tasks 6, 7, 8.
- §Delivery modes table → Task 10.
- §Concurrency rule (downgrade during active conversation) → Task 10 (`is_conversation_active` check), Task 13 (flag wiring).
- §Skill interface (4 actions, JSON envelope) → Tasks 11-12.
- §Error handling table → distributed across Tasks 3 (corrupt yaml), 7 (tick crash), 10 (tool loop failure), 12 (validation errors).
- §Testing (unit / integration / e2e) → Tasks 2-8 (unit), 7-8 (integration), 14 (e2e harness).
- §Migration → no env vars required, `croniter` is the only new dep (Task 1), no breaking changes.

All sections of the spec map to at least one task.

**Type consistency:** `ScheduleEntry`, `ScheduledFire`, `SchedulesStore`, `SchedulerThread`, `compute_due_fires`, `skip_missed_on_startup`, `reload_if_changed`, `update_last_fired`, `process_scheduled_fire`, `drain_pending_announcements` — same names used throughout.

**Placeholder scan:** no TBDs, no "implement later", no "handle edge cases" steps. Every code step contains concrete code.
