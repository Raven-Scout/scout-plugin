"""scoutctl schedule install-wake-schedule [--uninstall].

Wraps `pmset repeat wakeorpoweron <DAYS> <HH:MM:SS>` to wake the Mac for the
earliest scheduled weekday slot. Documented limitation: only reliable on AC
power; on battery + lid-closed, Apple Silicon laptops enter standby with wake
timers suppressed.
"""

from __future__ import annotations

import subprocess

from scout.schedule import Schedule, Slot

_WEEKDAY_LETTER = {
    "Mon": "M",
    "Tue": "T",
    "Wed": "W",
    "Thu": "R",
    "Fri": "F",
    "Sat": "S",
    "Sun": "U",
}
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")


def compute_earliest_weekday_slot(sched: Schedule) -> Slot | None:
    """Return the slot with the earliest fires_at_local that has at least one weekday.

    Slots whose `weekdays` is entirely Sat/Sun are excluded — pmset's wake
    rule only applies on weekdays we name in the day-letter argument.
    """
    candidates = [s for s in sched.values() if any(d in _WEEKDAYS for d in s.weekdays)]
    if not candidates:
        return None
    return min(candidates, key=lambda s: s.fires_at_local)


def install_wake_schedule(sched: Schedule, *, dry_run: bool = False) -> str:
    """Install the pmset repeat rule. Returns the command summary string."""
    slot = compute_earliest_weekday_slot(sched)
    if slot is None:
        raise ValueError("no weekday slot found in schedule; cannot compute wake time")
    days = "".join(_WEEKDAY_LETTER[d] for d in slot.weekdays if d in _WEEKDAY_LETTER and d in _WEEKDAYS)
    if not days:
        raise ValueError(f"slot {slot.key} has no recognizable weekdays")
    hhmm = slot.fires_at_local
    cmd = ["pmset", "repeat", "wakeorpoweron", days, f"{hhmm}:00"]
    if dry_run:
        return f"[dry-run] would run: {' '.join(cmd)}"
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"pmset failed: {result.stderr.strip()}")
    return f"installed: {' '.join(cmd)}"


def uninstall_wake_schedule(*, dry_run: bool = False) -> str:
    cmd = ["pmset", "repeat", "cancel"]
    if dry_run:
        return f"[dry-run] would run: {' '.join(cmd)}"
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"pmset failed: {result.stderr.strip()}")
    return "uninstalled"
