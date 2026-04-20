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
