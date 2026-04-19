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
