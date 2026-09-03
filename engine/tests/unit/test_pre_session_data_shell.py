"""Coverage of pre_session_data's shell-out layer and failure tolerance.

`test_pre_session_data.py` covers the KB date extractor, its mtime cache and
two `gather()` shapes. What's left is every path that must degrade to an empty
value rather than raise: the `_run` wrapper (nonzero exit / OSError /
timeout), the two `gh` JSON readers, the ontology-parser probe, cache
read/write corruption, and the `run()`/`main()` driver.

Tolerance is the whole point of this module — it runs in the preamble of every
scheduled session, so a raise here blocks the session rather than just
omitting one field.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scout.scripts import pre_session_data as psd


@pytest.fixture
def scout_dir(tmp_path: Path) -> Path:
    d = tmp_path / "Scout"
    (d / "knowledge-base").mkdir(parents=True)
    (d / ".scout-cache").mkdir(parents=True)
    return d


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


# ---------------------------------------------------------------------------
# _run
# ---------------------------------------------------------------------------


def test_run_returns_stripped_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "  output  \n"))
    assert psd._run(["true"]) == "output"


def test_run_returns_empty_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(1, "partial output"))
    assert psd._run(["false"]) == ""


@pytest.mark.parametrize("exc", [OSError("no such binary"), subprocess.TimeoutExpired("gh", 10)])
def test_run_returns_empty_when_the_process_cannot_run(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def boom(*_a: object, **_k: object):
        raise exc

    monkeypatch.setattr(subprocess, "run", boom)
    assert psd._run(["gh"]) == ""


def test_run_passes_cwd_and_timeout_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: seen.update(cmd=cmd, **k) or _Proc(0, ""))
    psd._run(["git", "status"], cwd=tmp_path, timeout=3)
    assert seen["cmd"] == ["git", "status"]
    assert seen["cwd"] == str(tmp_path)
    assert seen["timeout"] == 3


def test_run_passes_no_cwd_when_none_given(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: seen.update(**k) or _Proc(0, ""))
    psd._run(["gh"])
    assert seen["cwd"] is None


# ---------------------------------------------------------------------------
# get_git_recent
# ---------------------------------------------------------------------------


def test_git_recent_is_empty_without_a_git_dir(scout_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_a: object, **_k: object):
        raise AssertionError("git must not run without a .git dir")

    monkeypatch.setattr(subprocess, "run", fail)
    assert psd.get_git_recent(scout_dir) == ""


def test_git_recent_reads_the_oneline_log(scout_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (scout_dir / ".git").mkdir()
    seen: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: seen.append(cmd) or _Proc(0, "abc1234 update people.md\n"))
    assert psd.get_git_recent(scout_dir) == "abc1234 update people.md"
    assert seen[0][:3] == ["git", "log", "--oneline"]
    assert seen[0][3] == f"--since={psd.DEFAULT_GIT_LOG_LOOKBACK}"


def test_git_recent_honours_a_custom_since(scout_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (scout_dir / ".git").mkdir()
    seen: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: seen.append(cmd) or _Proc(0, ""))
    psd.get_git_recent(scout_dir, since="3 days ago")
    assert "--since=3 days ago" in seen[0]


# ---------------------------------------------------------------------------
# gh readers
# ---------------------------------------------------------------------------


def test_gh_json_parses_an_array(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"number": 1, "title": "Fix the parser"}]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, json.dumps(rows)))
    assert psd._gh_json(["pr", "list"]) == rows


@pytest.mark.parametrize(
    "stdout",
    [
        "",  # gh not authenticated / no output
        "not json at all",
        '{"number": 1}',  # a JSON object, not the expected array
        "null",
    ],
)
def test_gh_json_degrades_to_an_empty_list(monkeypatch: pytest.MonkeyPatch, stdout: str) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, stdout))
    assert psd._gh_json(["pr", "list"]) == []


def test_get_pr_authored_queries_open_prs_by_me(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: seen.append(cmd) or _Proc(0, "[]"))
    assert psd.get_pr_authored() == []
    argv = seen[0]
    assert argv[:2] == ["gh", "pr"]
    assert "--author" in argv and "@me" in argv
    assert argv[argv.index("--limit") + 1] == str(psd.PR_LIST_LIMIT)


def test_get_pr_review_requested_uses_gh_search(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: seen.append(cmd) or _Proc(0, "[]"))
    assert psd.get_pr_review_requested() == []
    argv = seen[0]
    assert argv[:3] == ["gh", "search", "prs"]
    assert "--review-requested" in argv


# ---------------------------------------------------------------------------
# get_personal_tasks
# ---------------------------------------------------------------------------


def test_personal_tasks_is_empty_without_the_ontology_parser(scout_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The ontology parser is optional; its absence must be silent, not fatal."""

    def fail(*_a: object, **_k: object):
        raise AssertionError("must not shell out when parser.py is absent")

    monkeypatch.setattr(subprocess, "run", fail)
    assert psd.get_personal_tasks(scout_dir) == ""


def test_personal_tasks_invokes_the_parser_when_present(scout_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parser = scout_dir / "knowledge-base" / "ontology" / "parser.py"
    parser.parent.mkdir(parents=True)
    parser.write_text("# stub\n", encoding="utf-8")

    seen: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: seen.append(cmd) or _Proc(0, "- [ ] pay rent"))

    assert psd.get_personal_tasks(scout_dir) == "- [ ] pay rent"
    assert seen[0] == ["python3", str(parser), "query", "--type", "task"]


# ---------------------------------------------------------------------------
# KB dates cache read/write tolerance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        '["a", "list"]',  # right JSON, wrong shape
        "null",
    ],
)
def test_kb_dates_cache_load_rejects_a_corrupt_file(tmp_path: Path, body: str) -> None:
    cache = tmp_path / "kb-dates.cache.json"
    cache.write_text(body, encoding="utf-8")
    assert psd._load_kb_dates_cache(cache) == {}


def test_kb_dates_cache_load_skips_individual_bad_entries(tmp_path: Path) -> None:
    """One malformed row must not discard the whole cache — the rest still
    saves a file open each."""
    cache = tmp_path / "kb-dates.cache.json"
    cache.write_text(
        json.dumps(
            {
                "knowledge-base/good.md": {"mtime_ns": 123, "last_updated": "2026-04-15"},
                "knowledge-base/not-a-dict.md": "oops",
                "knowledge-base/no-mtime.md": {"last_updated": "2026-04-15"},
                "knowledge-base/bad-mtime.md": {"mtime_ns": "not-an-int"},
            }
        ),
        encoding="utf-8",
    )
    loaded = psd._load_kb_dates_cache(cache)
    assert set(loaded) == {"knowledge-base/good.md"}
    assert loaded["knowledge-base/good.md"].mtime_ns == 123


def test_kb_dates_cache_load_is_empty_when_missing(tmp_path: Path) -> None:
    assert psd._load_kb_dates_cache(tmp_path / "nope.json") == {}


def test_kb_dates_cache_write_cleans_up_after_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed atomic write must not leave a .tmp turd next to the cache."""
    cache = tmp_path / "kb-dates.cache.json"
    real_open = Path.open

    def maybe_boom(self: Path, *a: object, **k: object):
        if self.name.endswith(".json.tmp"):
            raise OSError("disk full")
        return real_open(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", maybe_boom)
    psd._write_kb_dates_cache(cache, {"a.md": psd._KbEntry(mtime_ns=1, last_updated="x")})
    assert not cache.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_extract_last_updated_returns_empty_for_an_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "kb.md"
    f.write_text("Last updated: 2026-04-15\n", encoding="utf-8")

    def boom(*_a: object, **_k: object):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", boom)
    assert psd.extract_last_updated(f) == ""


def test_gather_kb_file_dates_skips_files_outside_the_scout_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rglob returning a path that isn't under scout_dir (a symlink escape)
    must be skipped, not crash `relative_to`."""
    scout = tmp_path / "Scout"
    kb = scout / "knowledge-base"
    kb.mkdir(parents=True)
    (kb / "inside.md").write_text("Last updated: 2026-04-15\n", encoding="utf-8")

    outside = tmp_path / "elsewhere.md"
    outside.write_text("Last updated: 2026-01-01\n", encoding="utf-8")

    real_rglob = Path.rglob
    monkeypatch.setattr(Path, "rglob", lambda self, pat: [*real_rglob(self, pat), outside])

    out = psd.gather_kb_file_dates(kb, scout_dir=scout, cache_path=tmp_path / "c.json")
    assert out == {"knowledge-base/inside.md": "2026-04-15"}


def test_gather_kb_file_dates_skips_a_file_it_cannot_stat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scout = tmp_path / "Scout"
    kb = scout / "knowledge-base"
    kb.mkdir(parents=True)
    gone = kb / "gone.md"
    gone.write_text("Last updated: 2026-04-15\n", encoding="utf-8")
    (kb / "kept.md").write_text("Last updated: 2026-04-16\n", encoding="utf-8")

    real_stat = Path.stat

    def maybe_boom(self: Path, *a: object, **k: object):
        if self == gone:
            raise OSError("vanished mid-walk")
        return real_stat(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", maybe_boom)
    out = psd.gather_kb_file_dates(kb, scout_dir=scout, cache_path=tmp_path / "c.json")
    assert out == {"knowledge-base/kept.md": "2026-04-16"}


# ---------------------------------------------------------------------------
# write_context / run / main
# ---------------------------------------------------------------------------


def test_write_context_cleans_up_after_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "session-context.json"
    ctx = psd.SessionContext(generated_at="2026-04-15T08:00:00", session_type="briefing")
    real_open = Path.open

    def maybe_boom(self: Path, *a: object, **k: object):
        if self.name.endswith(".json.tmp"):
            raise OSError("disk full")
        return real_open(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", maybe_boom)
    psd.write_context(ctx, out)
    assert not out.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_gather_stamps_the_generated_at_in_the_given_timezone(scout_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(1, ""))
    noon_utc = datetime(2026, 4, 15, 16, 0, tzinfo=ZoneInfo("UTC"))
    ctx = psd.gather("briefing", scout_dir=scout_dir, tz_name="America/New_York", now=noon_utc)
    # 16:00 UTC is 12:00 EDT on 2026-04-15.
    assert ctx.generated_at == "2026-04-15T12:00:00"
    assert ctx.session_type == "briefing"


def test_run_writes_the_context_into_the_cache_dir(scout_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(1, ""))
    out = psd.run("consolidation", data_dir=scout_dir)
    assert out == scout_dir / ".scout-cache" / psd.OUTPUT_FILENAME
    payload = json.loads(out.read_text())
    assert payload["session_type"] == "consolidation"
    assert payload["kb_file_dates"] == {}


def test_run_resolves_the_data_dir_from_the_environment(scout_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(scout_dir))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(1, ""))
    assert psd.run("research") == scout_dir / ".scout-cache" / psd.OUTPUT_FILENAME


def test_main_prints_the_output_path(
    scout_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(scout_dir))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(1, ""))
    assert psd.main("briefing") == 0
    out = capsys.readouterr().out
    assert "Pre-session data written to" in out
    assert "(briefing)" in out


def test_main_returns_zero_even_when_gathering_blows_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """This runs in the preamble of every scheduled session — a crash here must
    never be the reason a session doesn't start."""

    def boom(*_a: object, **_k: object):
        raise RuntimeError("unreachable state")

    monkeypatch.setattr(psd, "run", boom)
    assert psd.main("briefing") == 0
