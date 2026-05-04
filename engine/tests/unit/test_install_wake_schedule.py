"""Unit tests for install_wake_schedule.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scout.schedule import load_default_schedule
from scout.scripts.install_wake_schedule import (
    compute_earliest_weekday_slot,
    install_wake_schedule,
    uninstall_wake_schedule,
)


def test_compute_earliest_weekday_slot_filters_to_weekday_only():
    sched = load_default_schedule()
    slot = compute_earliest_weekday_slot(sched)
    assert slot is not None
    weekdays = set(slot.weekdays)
    assert weekdays.intersection({"Mon", "Tue", "Wed", "Thu", "Fri"})
    # Slots that are weekend-only must NOT be returned.
    assert slot.key != "weekend-briefing"
    assert slot.key != "dreaming-weekend-morning"


def test_compute_earliest_weekday_slot_returns_morning_briefing_in_default():
    sched = load_default_schedule()
    slot = compute_earliest_weekday_slot(sched)
    # Default schedule's earliest weekday slot is morning-briefing 08:00.
    assert slot.key == "morning-briefing"
    assert slot.fires_at_local == "08:00"


def test_install_wake_schedule_invokes_pmset_repeat():
    sched = load_default_schedule()
    with patch("scout.scripts.install_wake_schedule.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        install_wake_schedule(sched, dry_run=False)
    args, kwargs = mock_run.call_args
    cmd = args[0]
    assert cmd[0] == "pmset"
    assert "repeat" in cmd
    assert "wakeorpoweron" in cmd
    # MTWRF letter form for Mon-Fri.
    assert "MTWRF" in cmd
    # Hour:minute:second form.
    assert "08:00:00" in cmd


def test_install_wake_schedule_dry_run_doesnt_invoke_pmset():
    sched = load_default_schedule()
    with patch("scout.scripts.install_wake_schedule.subprocess.run") as mock_run:
        result = install_wake_schedule(sched, dry_run=True)
    mock_run.assert_not_called()
    assert "dry-run" in result.lower()


def test_install_wake_schedule_raises_when_pmset_fails():
    sched = load_default_schedule()
    with patch("scout.scripts.install_wake_schedule.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "pmset: permission denied"
        with pytest.raises(RuntimeError, match="permission denied"):
            install_wake_schedule(sched, dry_run=False)


def test_uninstall_wake_schedule_invokes_pmset_repeat_cancel():
    with patch("scout.scripts.install_wake_schedule.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        uninstall_wake_schedule()
    args, kwargs = mock_run.call_args
    cmd = args[0]
    assert cmd[0] == "pmset"
    assert "repeat" in cmd
    assert "cancel" in cmd
