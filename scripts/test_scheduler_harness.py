#!/usr/bin/env python3
"""
End-to-end harness for the scheduler.

Exercises the real SchedulerThread + real yaml store against a stub
consumer that simply records fires. No voice hardware, no Docker,
no Claude API.

Expected runtime: ~5 seconds.
"""

import queue
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.scheduler import ScheduleEntry, SchedulesStore, SchedulerThread


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        store = SchedulesStore(Path(tmp) / "schedules.yaml")
        q: queue.Queue = queue.Queue()
        thread = SchedulerThread(
            store=store,
            fire_queue=q,
            tick_seconds=0.1,
            catch_up_on_start=False,
        )
        thread.start()
        try:
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
