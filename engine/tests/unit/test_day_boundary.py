"""Day-boundary unification regression tests (#207).

Every surface that derives a daily date — the materialize backstop, the five
action-items mutators, the daily-path default, trigger daily caps, hook
day-stamps, and bootstrap's template date — must take "today" from the
CONFIGURED timezone via scout.config.today / resolve_timezone, never from the
host clock and never from a hardcoded zone.

Probe technique: the two zones below sit at the opposite extremes of the tz
database — their civil dates are 26 hours apart and therefore NEVER agree.
Whatever zone the host machine runs in, its date can coincide with at most
one of them; a code path that follows the host clock (or a hardcoded third
zone) fails the assertion under at least one probe zone at any time of day,
while a path that follows the configured zone passes under both.

The env override (SCOUT_USER_TIMEZONE) is the highest-precedence config layer
and is used here so the tests exercise the same merged-config resolution that
a vault scout-config.yaml goes through.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scout import config, paths
from scout.action_items.add_comment import add_comment
from scout.action_items.delete_comment import delete_comment
from scout.action_items.edit_comment import edit_comment
from scout.action_items.mark_done import mark_done
from scout.action_items.materialize import materialize
from scout.action_items.snooze import snooze
from scout.triggers.dedup import DedupStore

ZONE_EAST = "Pacific/Kiritimati"  # UTC+14 — the first zone to flip dates
ZONE_WEST = "Etc/GMT+12"  # UTC-12 — the last zone to flip dates
PROBE_ZONES = (ZONE_EAST, ZONE_WEST)


def _date_in(zone: str) -> dt.date:
    return dt.datetime.now(ZoneInfo(zone)).date()


def _run_boundary_safe(zone: str, fn):
    """Call fn() and return (result, acceptable_dates) — the configured-zone
    date sampled immediately before and after, so a midnight flip mid-test
    cannot produce a false failure."""
    before = _date_in(zone)
    result = fn()
    after = _date_in(zone)
    return result, {before, after}


def test_probe_zones_never_share_a_date() -> None:
    """Lemma for every test below: the two probe dates always differ, so the
    host date can match at most one of them."""
    assert _date_in(ZONE_EAST) != _date_in(ZONE_WEST)


@pytest.mark.parametrize("zone", PROBE_ZONES)
def test_config_today_follows_configured_zone(zone: str, fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_USER_TIMEZONE", zone)
    result, ok = _run_boundary_safe(zone, lambda: config.today(fake_data_dir))
    assert result in ok


def test_resolve_timezone_falls_back_inside_the_resolver(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed zone must degrade to the packaged default INSIDE the
    resolver — never raise — so every surface shifts together (#207)."""
    assert config.resolve_timezone(fake_data_dir).key == config.DEFAULT_TIMEZONE

    monkeypatch.setenv("SCOUT_USER_TIMEZONE", "Not/AZone")
    assert config.resolve_timezone(fake_data_dir).key == config.DEFAULT_TIMEZONE
    # today() keeps working off the fallback rather than raising.
    result, ok = _run_boundary_safe(config.DEFAULT_TIMEZONE, lambda: config.today(fake_data_dir))
    assert result in ok


@pytest.mark.parametrize("zone", PROBE_ZONES)
def test_daily_path_default_follows_configured_zone(
    zone: str, fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOUT_USER_TIMEZONE", zone)
    result, ok = _run_boundary_safe(zone, lambda: paths.action_items_daily_path(fake_data_dir))
    assert result.name in {f"action-items-{d.isoformat()}.md" for d in ok}


@pytest.mark.parametrize("zone", PROBE_ZONES)
def test_all_action_items_writers_share_one_day_boundary(
    zone: str, fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#207's headline defect: materialize() took the daily date from the
    configured zone while every mutator took the host clock. Under a probe
    zone whose date differs from the host's, the mutators then target a file
    that materialize never created. All six writers must agree."""
    monkeypatch.setenv("SCOUT_USER_TIMEZONE", zone)

    today = config.today(fake_data_dir)
    yesterday = today - dt.timedelta(days=1)
    prev = fake_data_dir / "action-items" / f"action-items-{yesterday.isoformat()}.md"
    prev.write_text(
        f"# Action Items — {yesterday.isoformat()}\n\n## 🔴 Urgent\n- [ ] [#AAAA] 🔴 call the bank\n",
        encoding="utf-8",
    )

    created = materialize(data_dir=fake_data_dir)
    assert created is not None, "materialize found nothing to do"
    daily = fake_data_dir / "action-items" / f"action-items-{today.isoformat()}.md"
    assert created == daily

    # Every mutator must resolve the SAME daily file materialize just wrote.
    # (Comment ops run before snooze/mark_done: the snooze marker would count
    # as comment #1, and by_subject matches open items only.)
    add_comment(by_subject="call the bank", comment="left a voicemail", data_dir=fake_data_dir)
    edit_comment(by_subject="call the bank", index=1, new_text="reached them", data_dir=fake_data_dir)
    delete_comment(by_subject="call the bank", index=1, data_dir=fake_data_dir)
    snooze(by_subject="call the bank", until=today + dt.timedelta(days=3), data_dir=fake_data_dir)
    mark_done(by_subject="call the bank", data_dir=fake_data_dir)

    text = daily.read_text(encoding="utf-8")
    assert "- [x] [#AAAA] 🔴 call the bank" in text
    assert f"snoozed-until: {(today + dt.timedelta(days=3)).isoformat()}" in text
    assert "reached them" not in text  # deleted again by delete_comment
    # And nothing leaked into a host-dated file (the seeded carry-forward
    # source may legitimately sit on the host date — exclude it).
    host_file = fake_data_dir / "action-items" / f"action-items-{dt.date.today().isoformat()}.md"
    if host_file not in (daily, prev):
        assert not host_file.exists()


def test_dedup_daily_cap_resets_on_configured_midnight(
    fake_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fires_today_date must be the configured-zone date of the fire instant,
    so daily caps reset at the user's local midnight (#207)."""
    instant = dt.datetime(2026, 8, 24, 1, 0, tzinfo=dt.UTC)
    expected = {
        ZONE_EAST: "2026-08-24",  # 15:00 local, same day
        ZONE_WEST: "2026-08-23",  # 13:00 local, previous day
    }
    for zone, day in expected.items():
        monkeypatch.setenv("SCOUT_USER_TIMEZONE", zone)
        store = DedupStore(tmp_path / f"trigger-fires-{zone.replace('/', '-')}.json")
        store.record_fire("t", "evt-1", instant)
        assert store.state("t")["fires_today_date"] == day, zone


@pytest.mark.parametrize("zone", PROBE_ZONES)
def test_hook_day_stamps_follow_configured_zone(
    zone: str, fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The connector-log JSONL filename date and the session-tool-log filename
    date bucket rows by day; both must use the configured zone (#207)."""
    from scout.hooks.connector_log import _local_date as connector_log_date
    from scout.hooks.session_tool_log import _local_date as tool_log_date

    monkeypatch.setenv("SCOUT_USER_TIMEZONE", zone)
    for stamp_fn in (connector_log_date, tool_log_date):
        result, ok = _run_boundary_safe(zone, stamp_fn)
        assert result in {d.isoformat() for d in ok}


@pytest.mark.parametrize("zone", PROBE_ZONES)
def test_bootstrap_template_date_uses_installed_timezone(zone: str, tmp_path: Path) -> None:
    """TODAY_DATE is rendered before the vault config exists, so it must come
    from the timezone being installed (cfg.timezone), not the host clock."""
    from scout.scripts.bootstrap import BootstrapConfig, _template_vars

    cfg = BootstrapConfig(
        vault=tmp_path / "Scout",
        plugin_root=tmp_path,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Alex Example",
        user_email="alex@example.com",
        timezone=zone,
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=set(),
        connector_inputs={},
    )
    result, ok = _run_boundary_safe(zone, lambda: _template_vars(cfg)["TODAY_DATE"])
    assert result in {d.isoformat() for d in ok}
