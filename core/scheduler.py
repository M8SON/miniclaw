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
