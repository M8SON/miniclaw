"""
Scheduler - Recurring task execution for MiniClaw.

Provides ScheduleEntry (one scheduled task), SchedulesStore (yaml-backed
persistence), ScheduledFire (a due-for-execution notification), and
SchedulerThread (the polling loop that turns cron hits into fires).

Fires are enqueued onto Orchestrator.scheduled_fire_queue and processed
between voice turns so they never interrupt an active conversation.
"""

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


def _validate(*, cron: str, prompt: str, delivery: str) -> tuple[str, str]:
    cron = (cron or "").strip()
    prompt = (prompt or "").strip()
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
    return cron, prompt


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
        label: Optional[str] = None,
    ) -> "ScheduleEntry":
        cron, prompt = _validate(cron=cron, prompt=prompt, delivery=delivery)
        label = label.strip() if label else None
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

        required_keys = ("id", "cron", "prompt", "delivery", "created")
        for key in required_keys:
            if key not in data:
                raise ScheduleValidationError(f"missing required field: {key!r}")

        cron, prompt = _validate(
            cron=data["cron"],
            prompt=data["prompt"],
            delivery=data["delivery"],
        )

        return cls(
            id=data["id"],
            cron=cron,
            prompt=prompt,
            delivery=data["delivery"],
            label=data.get("label"),
            created=_dt(data["created"]),
            last_fired=_dt(data.get("last_fired")),
            disabled=bool(data.get("disabled", False)),
        )


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
            except Exception as exc:
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
