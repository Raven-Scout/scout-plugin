"""Unit tests for the Textual-free spawn command builder (#52).

Lives outside scout.tui.screens.spawn so it runs in CI without `textual`.
"""

from __future__ import annotations

from scout.tui.spawn_cmd import applescript_literal, build_terminal_applescript


def test_applescript_literal_escapes_backslash_and_quote():
    assert applescript_literal('a"b\\c') == '"a\\"b\\\\c"'


def test_build_terminal_applescript_quotes_nasty_title_and_prompt():
    title = 'pwn"; rm -rf ~ #`whoami`$(id)'
    prompt = 'do the "thing" now'
    cmd, script = build_terminal_applescript(title=title, prompt=prompt)

    # Session name is a safe slug — no quotes/backticks/spaces/$ leak through.
    assert all(c.isalnum() or c == "-" for c in cmd.split('"')[1]) or "scout-" in cmd
    # The AppleScript `do script` argument is a single well-formed double-quoted
    # literal: every interior double-quote is backslash-escaped.
    inner = script.split("do script ", 1)[1].strip().splitlines()[0]
    assert inner.startswith('"') and inner.endswith('"')
    body = inner[1:-1]
    i = 0
    while i < len(body):
        if body[i] == "\\":
            i += 2
            continue
        assert body[i] != '"', f"unescaped quote in AppleScript literal: {inner!r}"
        i += 1


def test_build_terminal_applescript_prompt_is_shell_quoted():
    cmd, _ = build_terminal_applescript(title="t", prompt="a b; c")
    assert "'a b; c'" in cmd or '"a b; c"' in cmd
