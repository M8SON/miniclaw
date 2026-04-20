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

    def test_from_dict_rejects_invalid_delivery(self):
        valid = ScheduleEntry.new(
            cron="0 8 * * *", prompt="p", delivery="immediate"
        ).to_dict()
        valid["delivery"] = "bogus"
        with self.assertRaises(ScheduleValidationError):
            ScheduleEntry.from_dict(valid)

    def test_from_dict_raises_for_missing_key(self):
        valid = ScheduleEntry.new(
            cron="0 8 * * *", prompt="p", delivery="immediate"
        ).to_dict()
        del valid["cron"]
        with self.assertRaises(ScheduleValidationError):
            ScheduleEntry.from_dict(valid)


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
