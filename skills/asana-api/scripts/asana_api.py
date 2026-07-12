#!/usr/bin/env python3
"""
Thin Asana API helper for local AI agent skills.

Features:
- Reads PAT from ASANA_ACCESS_TOKEN or a local token file
- Reads default workspace/team IDs from a local context file when present
- Supports generic GET/POST/PUT/DELETE requests
- Supports JSON and multipart/form-data requests
- Includes convenience subcommands for common Asana flows
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import html
import json
import mimetypes
import os
import re
import socket
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib import error, parse, request


BASE_URL = "https://app.asana.com/api/1.0"
BATCH_ACTION_LIMIT = 10
SKILL_DIR = Path(__file__).resolve().parent.parent
LOCAL_STATE_DIR = Path.home() / ".agent-skills" / "asana"
LEGACY_LOCAL_STATE_DIR = Path.home() / ".codex" / "skills-data" / "asana"
DEFAULT_TOKEN_FILE = LOCAL_STATE_DIR / "asana_pat"
LEGACY_SHARED_TOKEN_FILE = LEGACY_LOCAL_STATE_DIR / "asana_pat"
LEGACY_TOKEN_FILE = SKILL_DIR / ".secrets" / "asana_pat"
DEFAULT_CONTEXT_FILE = LOCAL_STATE_DIR / "asana-context.json"
LEGACY_SHARED_CONTEXT_FILE = LEGACY_LOCAL_STATE_DIR / "asana-context.json"
LEGACY_CONTEXT_FILE = SKILL_DIR / "asana-context.json"
DEFAULT_CACHE_FILE = LOCAL_STATE_DIR / "asana-cache.json"
LEGACY_SHARED_CACHE_FILE = LEGACY_LOCAL_STATE_DIR / "asana-cache.json"
LEGACY_CACHE_FILE = SKILL_DIR / ".cache" / "asana-cache.json"
API_TIMEOUT_SECONDS = 30
API_NETWORK_RETRY_ATTEMPTS = 3
API_NETWORK_RETRY_DELAY_SECONDS = 1.0
DEFAULT_SORTING_TAG_LABELS = (
    "Quick Win",
    "Delegate",
    "Close Out",
    "Deep Work",
    "Waiting",
    "Needs Clarity",
)

INBOX_CLEANUP_REVIEW_SECTIONS = {
    "ready_to_close": "Review: Likely Ready To Close",
    "needs_verification": "Review: Needs Verification",
    "waiting_on_others": "Review: Waiting On Others",
    "needs_next_action": "Review: Needs Next Action",
    "backlog_cleanup": "Review: Backlog Cleanup",
}
INBOX_CLEANUP_SNAPSHOT_WORKFLOW = "asana_inbox_cleanup_snapshot"
INBOX_CLEANUP_PLAN_WORKFLOW = "asana_inbox_cleanup_plan"
INBOX_CLEANUP_PLAN_VERSION = 1
INBOX_CLEANUP_CATEGORY_SEEDS = [
    {
        "slug": "execute_now",
        "name": "Execute Now",
        "description": "Tasks that should actively drive work today or this week.",
        "suggested_section_name": "WIP",
    },
    {
        "slug": "needs_verification",
        "name": "Needs Verification",
        "description": "Tasks that look implemented and need QA, close-out, or release tracking.",
        "suggested_section_name": "Review: Needs Verification",
    },
    {
        "slug": "waiting_on_others",
        "name": "Waiting On Others",
        "description": "Tasks blocked on another person, team, or external dependency.",
        "suggested_section_name": "Review: Waiting On Others",
    },
    {
        "slug": "likely_ready_to_close",
        "name": "Likely Ready To Close",
        "description": "Tasks that likely only need a final confirmation and close-out.",
        "suggested_section_name": "Review: Likely Ready To Close",
    },
    {
        "slug": "backlog_or_not_now",
        "name": "Backlog / Not Now",
        "description": "Tasks that should stay visible but should not drive the current work cycle.",
        "suggested_section_name": "Review: Backlog Cleanup",
    },
    {
        "slug": "needs_user_input",
        "name": "Needs User Input",
        "description": "Tasks the AI should not auto-bucket until the user answers a specific question.",
        "suggested_section_name": "",
    },
]
DEFAULT_INBOX_CLEANUP_SOURCE_SECTIONS = ("Recently assigned",)
DAILY_BRIEFING_SNAPSHOT_WORKFLOW = "asana_daily_briefing_snapshot"
DAILY_BRIEFING_PLAN_WORKFLOW = "asana_daily_briefing_plan"
DAILY_BRIEFING_PLAN_VERSION = 1
DAILY_BRIEFING_BUCKET_SEEDS = [
    {
        "slug": "execute-now",
        "name": "Execute Now",
        "description": "Tasks the AI believes are the best candidates for active work today.",
        "display_order": 1,
    },
    {
        "slug": "release-watch",
        "name": "Release / Ship Watch",
        "description": "Tasks that need rollout watching, shipping awareness, or post-implementation monitoring.",
        "display_order": 2,
    },
    {
        "slug": "needs-verification",
        "name": "Needs Verification",
        "description": "Tasks where the next move is checking, QA, or validating output.",
        "display_order": 3,
    },
    {
        "slug": "needs-follow-up",
        "name": "Needs Follow-Up",
        "description": "Tasks that need a response, unblock, or external coordination step.",
        "display_order": 4,
    },
    {
        "slug": "ready-to-close",
        "name": "Likely Ready To Close",
        "description": "Tasks that appear effectively done and need final confirmation or close-out.",
        "display_order": 5,
    },
    {
        "slug": "background",
        "name": "Background / Not Today",
        "description": "Tasks that should stay visible but should not drive today's work.",
        "display_order": 6,
    },
]
DAILY_BRIEFING_DONE_LIKE_PATTERN = re.compile(
    r"\b(done|test|testing|staging|qa|production|complete|completed|released|shipped)\b",
    re.IGNORECASE,
)
DAILY_BRIEFING_ADMIN_NOISE_PATTERN = re.compile(
    r"\b(goal|goals|trainual|1:1|one on one|travel|reminder|birthday|shopping)\b",
    re.IGNORECASE,
)
DAILY_BRIEFING_URGENT_PATTERN = re.compile(
    r"\b(urgent|today|asap|overdue|priority)\b",
    re.IGNORECASE,
)


def token_file() -> Path:
    configured = os.environ.get("ASANA_TOKEN_FILE")
    if configured:
        return Path(configured).expanduser()
    if DEFAULT_TOKEN_FILE.exists():
        return DEFAULT_TOKEN_FILE
    if LEGACY_SHARED_TOKEN_FILE.exists():
        return LEGACY_SHARED_TOKEN_FILE
    return LEGACY_TOKEN_FILE


def context_file() -> Path:
    configured = os.environ.get("ASANA_CONTEXT_FILE")
    if configured:
        return Path(configured).expanduser()
    if DEFAULT_CONTEXT_FILE.exists():
        return DEFAULT_CONTEXT_FILE
    if LEGACY_SHARED_CONTEXT_FILE.exists():
        return LEGACY_SHARED_CONTEXT_FILE
    return LEGACY_CONTEXT_FILE


def cache_file() -> Path:
    configured = os.environ.get("ASANA_CACHE_FILE")
    if configured:
        return Path(configured).expanduser()
    if DEFAULT_CACHE_FILE.exists():
        return DEFAULT_CACHE_FILE
    if LEGACY_SHARED_CACHE_FILE.exists():
        return LEGACY_SHARED_CACHE_FILE
    if LEGACY_CACHE_FILE.exists():
        return LEGACY_CACHE_FILE
    return DEFAULT_CACHE_FILE


def load_context() -> dict[str, Any]:
    file_path = context_file()
    if not file_path.exists():
        return {}
    return json.loads(file_path.read_text())


def empty_cache() -> dict[str, Any]:
    return {
        "metadata": {"updated_at": None},
        "workspaces": {"by_gid": {}},
        "teams": {"by_gid": {}},
        "projects": {"by_gid": {}},
        "users": {"by_gid": {}},
        "tags": {"by_gid": {}},
    }


def ensure_cache_shape(cache: dict[str, Any]) -> dict[str, Any]:
    cache.setdefault("metadata", {})
    for bucket_name in ("workspaces", "teams", "projects", "users", "tags"):
        bucket = cache.setdefault(bucket_name, {})
        if not isinstance(bucket, dict):
            cache[bucket_name] = {"by_gid": {}}
            continue
        bucket.setdefault("by_gid", {})
        if not isinstance(bucket["by_gid"], dict):
            bucket["by_gid"] = {}
    return cache


def merge_nested_dicts(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = merge_nested_dicts(existing, value)
        elif value is not None:
            merged[key] = value
    return merged


def load_cache() -> dict[str, Any]:
    file_path = cache_file()
    if not file_path.exists():
        return empty_cache()
    return ensure_cache_shape(json.loads(file_path.read_text()))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def metadata_bucket(cache: dict[str, Any], key: str) -> dict[str, Any]:
    metadata = ensure_cache_shape(cache).setdefault("metadata", {})
    bucket = metadata.setdefault(key, {})
    if not isinstance(bucket, dict):
        metadata[key] = {}
    return metadata[key]


def parse_iso_timestamp(value: str | None) -> datetime:
    token = str(value or "").strip()
    if not token:
        return datetime.min.replace(tzinfo=timezone.utc)
    if token.endswith("Z"):
        token = f"{token[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(token)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def merge_cache_records(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if value is not None:
            merged[key] = value

    existing_cached_at = parse_iso_timestamp(existing.get("cached_at"))
    incoming_cached_at = parse_iso_timestamp(incoming.get("cached_at"))
    merged["cached_at"] = (
        incoming.get("cached_at")
        if incoming_cached_at >= existing_cached_at
        else existing.get("cached_at")
    )
    return merged


def merge_cache_data(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = ensure_cache_shape(dict(base))
    incoming_normalized = ensure_cache_shape(dict(incoming))

    for bucket_name in ("workspaces", "teams", "projects", "users", "tags"):
        merged_bucket = cache_bucket(merged, bucket_name)
        incoming_bucket = cache_bucket(incoming_normalized, bucket_name)
        for gid, record in incoming_bucket["by_gid"].items():
            if not isinstance(record, dict):
                continue
            existing_record = merged_bucket["by_gid"].get(gid, {})
            if isinstance(existing_record, dict):
                merged_bucket["by_gid"][gid] = merge_cache_records(existing_record, record)
            else:
                merged_bucket["by_gid"][gid] = dict(record)

    merged_metadata = merged.setdefault("metadata", {})
    incoming_metadata = incoming_normalized.get("metadata", {})
    merged["metadata"] = merge_nested_dicts(merged_metadata, incoming_metadata)
    return merged


@contextlib.contextmanager
def cache_lock(file_path: Path):
    lock_path = file_path.with_name(f"{file_path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def save_cache(cache: dict[str, Any]) -> Path:
    file_path = cache_file()
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with cache_lock(file_path):
        on_disk = empty_cache()
        if file_path.exists():
            on_disk = ensure_cache_shape(json.loads(file_path.read_text()))

        normalized = merge_cache_data(on_disk, cache)
        normalized["metadata"]["updated_at"] = now_iso()

        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=file_path.parent,
            prefix=f"{file_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_file.write(f"{json.dumps(normalized, indent=2, sort_keys=True)}\n")
            temp_path = Path(tmp_file.name)

        os.replace(temp_path, file_path)
    return file_path


def cache_bucket(cache: dict[str, Any], bucket_name: str) -> dict[str, Any]:
    return ensure_cache_shape(cache).setdefault(bucket_name, {"by_gid": {}})


def cache_record(cache: dict[str, Any], bucket_name: str, record: dict[str, Any]) -> None:
    gid = str(record.get("gid") or "").strip()
    if not gid:
        return
    bucket = cache_bucket(cache, bucket_name)
    existing = bucket["by_gid"].get(gid, {})
    merged = dict(existing)
    merged.update({key: value for key, value in record.items() if value is not None})
    merged["cached_at"] = now_iso()
    bucket["by_gid"][gid] = merged


def cache_records(cache: dict[str, Any], bucket_name: str, records: list[dict[str, Any]]) -> None:
    for record in records:
        if isinstance(record, dict):
            cache_record(cache, bucket_name, record)


def normalize_match_key(value: str | None) -> str:
    return str(value or "").strip().casefold()


def is_gid_like(value: str | None) -> bool:
    token = str(value or "").strip()
    return bool(token) and token.isdigit()


def find_cached_record(
    cache: dict[str, Any],
    bucket_name: str,
    identifier: str | None,
    *,
    fields: tuple[str, ...],
) -> dict[str, Any] | None:
    token = str(identifier or "").strip()
    if not token:
        return None
    if is_gid_like(token):
        return cache_bucket(cache, bucket_name)["by_gid"].get(token)

    lowered = normalize_match_key(token)
    matches: list[dict[str, Any]] = []
    for record in cache_bucket(cache, bucket_name)["by_gid"].values():
        if not isinstance(record, dict):
            continue
        if any(normalize_match_key(record.get(field)) == lowered for field in fields):
            matches.append(record)

    if not matches:
        return None
    unique_matches: dict[str, dict[str, Any]] = {}
    for record in matches:
        gid = str(record.get("gid") or "").strip()
        if gid:
            unique_matches[gid] = record
    if len(unique_matches) == 1:
        return next(iter(unique_matches.values()))
    match_list = ", ".join(
        f"{record.get('name', record.get('gid'))} ({record.get('gid')})"
        for record in unique_matches.values()
    )
    raise SystemExit(f"Ambiguous cached {bucket_name[:-1]} identifier '{token}': {match_list}")


def find_section_record(
    sections: list[dict[str, Any]],
    identifier: str | None,
) -> dict[str, Any]:
    token = str(identifier or "").strip()
    if not token:
        raise SystemExit("Missing section identifier.")

    if is_gid_like(token):
        for section in sections:
            if str(section.get("gid") or "").strip() == token:
                return dict(section)
        raise SystemExit(f"No section with gid '{token}' exists in this project.")

    lowered = normalize_match_key(token)
    matches = [
        dict(section)
        for section in sections
        if normalize_match_key(section.get("name")) == lowered
    ]
    if not matches:
        raise SystemExit(f"No section named '{token}' exists in this project.")
    if len(matches) == 1:
        return matches[0]

    match_list = ", ".join(
        f"{section.get('name', section.get('gid'))} ({section.get('gid')})"
        for section in matches
    )
    raise SystemExit(f"Ambiguous section identifier '{token}': {match_list}")


def user_cache_record(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "gid": user.get("gid"),
        "name": user.get("name"),
        "email": user.get("email"),
    }


def workspace_cache_record(workspace: dict[str, Any]) -> dict[str, Any]:
    return {
        "gid": workspace.get("gid"),
        "name": workspace.get("name"),
    }


def team_cache_record(team: dict[str, Any], *, workspace_gid: str | None = None) -> dict[str, Any]:
    return {
        "gid": team.get("gid"),
        "name": team.get("name"),
        "workspace_gid": workspace_gid,
    }


def project_cache_record(project: dict[str, Any], *, team_gid: str | None = None) -> dict[str, Any]:
    team = project.get("team") if isinstance(project.get("team"), dict) else {}
    owner = project.get("owner") if isinstance(project.get("owner"), dict) else {}
    return {
        "gid": project.get("gid"),
        "name": project.get("name"),
        "team_gid": team.get("gid") or team_gid,
        "team_name": team.get("name"),
        "owner_gid": owner.get("gid"),
        "owner_name": owner.get("name"),
    }


def tag_cache_record(tag: dict[str, Any], *, workspace_gid: str | None = None) -> dict[str, Any]:
    return {
        "gid": tag.get("gid"),
        "name": tag.get("name"),
        "color": tag.get("color"),
        "workspace_gid": workspace_gid,
    }


def find_cached_tag_record(
    cache: dict[str, Any],
    identifier: str | None,
    *,
    workspace_gid: str | None = None,
) -> dict[str, Any] | None:
    token = str(identifier or "").strip()
    if not token:
        return None
    if is_gid_like(token):
        return cache_bucket(cache, "tags")["by_gid"].get(token)

    lowered = normalize_match_key(token)
    matches: list[dict[str, Any]] = []
    for record in cache_bucket(cache, "tags")["by_gid"].values():
        if not isinstance(record, dict):
            continue
        if normalize_match_key(record.get("name")) != lowered:
            continue
        record_workspace_gid = str(record.get("workspace_gid") or "").strip()
        if workspace_gid and record_workspace_gid and record_workspace_gid != workspace_gid:
            continue
        matches.append(record)

    if not matches:
        return None

    unique_matches: dict[str, dict[str, Any]] = {}
    for record in matches:
        gid = str(record.get("gid") or "").strip()
        if gid:
            unique_matches[gid] = record
    if len(unique_matches) == 1:
        return next(iter(unique_matches.values()))

    match_list = ", ".join(
        f"{record.get('name', record.get('gid'))} ({record.get('gid')})"
        for record in unique_matches.values()
    )
    raise SystemExit(f"Ambiguous cached tag identifier '{token}': {match_list}")


def get_token(args: argparse.Namespace) -> str:
    file_path = token_file()

    token = (
        os.environ.get("ASANA_ACCESS_TOKEN")
        or (file_path.read_text().strip() if file_path.exists() else "")
    )
    if not token:
        raise SystemExit(
            "Missing Asana token. Set ASANA_ACCESS_TOKEN or write the PAT to "
            f"{file_path}."
        )
    return token


def parse_bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "y"}:
        return True
    if lowered in {"0", "false", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected a boolean value, got: {value}")


def parse_kv(items: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"Expected KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        result[key] = value
    return result


def parse_many_gid(items: list[str] | None) -> list[str]:
    values: list[str] = []
    for item in items or []:
        for part in item.split(","):
            cleaned = part.strip()
            if cleaned:
                values.append(cleaned)
    return values


def parse_gid_args(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return parse_many_gid([value])
    if isinstance(value, (list, tuple)):
        return parse_many_gid([str(item) for item in value if item is not None])
    raise SystemExit(f"Expected a gid or list of gids, got: {type(value).__name__}")


def nullable_arg(value: str | None) -> str | None:
    if value is None:
        return None
    if value.strip().lower() == "null":
        return None
    return value


def task_position_args(args: argparse.Namespace) -> tuple[str | None, str | None]:
    insert_before = getattr(args, "insert_before", None)
    insert_after = getattr(args, "insert_after", None)
    if insert_before is not None and insert_after is not None:
        raise SystemExit("Pass only one of --insert-before or --insert-after.")
    return insert_before, insert_after


def load_inline_or_file_value(
    inline_value: str | None,
    file_path: str | None,
    *,
    field_name: str,
) -> str | None:
    if inline_value is not None and file_path is not None:
        raise SystemExit(f"Pass only one of --{field_name} or --{field_name}-file")
    if file_path is None:
        return inline_value
    return Path(file_path).read_text()


AI_MESSAGE_HEADER_RE = re.compile(
    r"(?P<header><strong>\s*AI MESSAGE DISCLAIMER\s*</strong>)(?P<rest>.*)",
    flags=re.IGNORECASE | re.DOTALL,
)
AI_MESSAGE_LABEL_RE = re.compile(
    r"(<strong>\s*(?!AI MESSAGE DISCLAIMER\b)[^<]+?:\s*</strong>)(.*?)(?=(<strong>\s*(?!AI MESSAGE DISCLAIMER\b)[^<]+?:\s*</strong>)|$)",
    flags=re.IGNORECASE | re.DOTALL,
)
AI_MESSAGE_LABEL_ONLY_RE = re.compile(
    r"^(<strong>\s*(?!AI MESSAGE DISCLAIMER\b)[^<]+?:\s*</strong>)$",
    flags=re.IGNORECASE | re.DOTALL,
)
AI_MESSAGE_LABEL_WITH_CONTENT_RE = re.compile(
    r"^(<strong>\s*(?!AI MESSAGE DISCLAIMER\b)[^<]+?:\s*</strong>)(.+)$",
    flags=re.IGNORECASE | re.DOTALL,
)
AI_MESSAGE_FOOTNOTE_RE = re.compile(
    r"^\s*<blockquote>\s*<strong>\s*AI MESSAGE DISCLAIMER\s*</strong>\s*(?P<text>.*?)</blockquote>\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)


def collapse_html_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def render_ai_message_footnote(disclaimer_text: str) -> str:
    scalar = collapse_html_whitespace(disclaimer_text)
    if not scalar:
        return ""
    return f"<blockquote><strong>AI MESSAGE DISCLAIMER</strong> {scalar}</blockquote>"


def render_ai_message_sections(
    sections: list[tuple[str | None, str, str | list[str]]],
) -> str | None:
    if not sections:
        return None

    parts = ["<body>"]
    disclaimer_text: str | None = None
    for label_html, kind, content in sections:
        if label_html is None and kind == "scalar" and disclaimer_text is None:
            scalar = collapse_html_whitespace(str(content))
            if scalar:
                disclaimer_text = scalar
                continue

        if label_html is None:
            scalar = collapse_html_whitespace(str(content))
            if scalar:
                parts.append(f"<blockquote>{scalar}</blockquote>")
            continue

        parts.append(label_html)
        if kind == "list":
            items = [
                f"<li>{collapse_html_whitespace(item)}</li>"
                for item in content
                if collapse_html_whitespace(item)
            ]
            if items:
                parts.append(f"<ul>{''.join(items)}</ul>")
            continue

        scalar = collapse_html_whitespace(str(content))
        if scalar:
            parts.append(f"<blockquote>{scalar}</blockquote>")

    if disclaimer_text:
        parts.append(render_ai_message_footnote(disclaimer_text))

    parts.append("</body>")
    return "".join(parts)


def canonicalize_ai_authored_markup(value: str) -> str:
    compact = re.sub(r">\s+<", "><", value.strip(), flags=re.DOTALL)

    def _trim_tag_content(tag: str, text: str) -> str:
        pattern = re.compile(
            rf"<{tag}\b(?P<attrs>[^>]*)>(?P<content>.*?)</{tag}>",
            flags=re.IGNORECASE | re.DOTALL,
        )

        def _replace(match: re.Match[str]) -> str:
            attrs = match.group("attrs")
            content = collapse_html_whitespace(match.group("content"))
            return f"<{tag}{attrs}>{content}</{tag}>"

        return pattern.sub(_replace, text)

    for tag_name in ("blockquote", "li", "strong"):
        compact = _trim_tag_content(tag_name, compact)
    return compact


def normalize_structured_ai_message(inner: str) -> str | None:
    disclaimer_match = AI_MESSAGE_HEADER_RE.match(inner)
    if not disclaimer_match:
        if AI_MESSAGE_FOOTNOTE_RE.search(inner):
            return canonicalize_ai_authored_markup(f"<body>{inner}</body>")
        return None

    rest = disclaimer_match.group("rest").strip()
    if not rest:
        return canonicalize_ai_authored_markup(
            f"<body>{render_ai_message_footnote('This message was generated by AI.')}</body>"
        )

    leading_blockquote_match = re.match(
        r"^<blockquote\b[^>]*>(?P<text>.*?)</blockquote>(?P<rest>.*)$",
        rest,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if leading_blockquote_match:
        disclaimer_text = collapse_html_whitespace(leading_blockquote_match.group("text"))
        body_rest = leading_blockquote_match.group("rest").strip()
        parts = ["<body>"]
        if body_rest:
            parts.append(body_rest)
        if disclaimer_text:
            parts.append(render_ai_message_footnote(disclaimer_text))
        parts.append("</body>")
        return canonicalize_ai_authored_markup("".join(parts))

    return None


def normalize_legacy_ai_message_list(inner: str) -> str | None:
    disclaimer_match = AI_MESSAGE_HEADER_RE.match(inner)
    if not disclaimer_match:
        return None

    rest = disclaimer_match.group("rest").strip()
    if re.search(r"<(ol|blockquote|pre)\b", rest, flags=re.IGNORECASE):
        return None

    ul_match = re.fullmatch(r"<ul\b[^>]*>(?P<items>.*)</ul>", rest, flags=re.IGNORECASE | re.DOTALL)
    if not ul_match:
        return None

    item_matches = re.findall(
        r"<li\b[^>]*>(.*?)</li>",
        ul_match.group("items"),
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not item_matches:
        return None

    sections: list[tuple[str | None, str, str | list[str]]] = []
    pending_list_label: str | None = None
    pending_list_items: list[str] = []

    def flush_pending_list() -> None:
        nonlocal pending_list_label, pending_list_items
        if pending_list_label is None:
            return
        if pending_list_items:
            sections.append((pending_list_label, "list", pending_list_items[:]))
        else:
            sections.append((pending_list_label, "scalar", ""))
        pending_list_label = None
        pending_list_items = []

    for raw_item in item_matches:
        item = collapse_html_whitespace(raw_item)
        if not item:
            continue

        label_only_match = AI_MESSAGE_LABEL_ONLY_RE.fullmatch(item)
        if label_only_match:
            flush_pending_list()
            pending_list_label = label_only_match.group(1).strip()
            pending_list_items = []
            continue

        label_with_content_match = AI_MESSAGE_LABEL_WITH_CONTENT_RE.fullmatch(item)
        if label_with_content_match:
            flush_pending_list()
            sections.append(
                (
                    label_with_content_match.group(1).strip(),
                    "scalar",
                    label_with_content_match.group(2).strip(),
                )
            )
            continue

        if pending_list_label is not None:
            pending_list_items.append(item)
        else:
            sections.append((None, "scalar", item))

    flush_pending_list()
    return render_ai_message_sections(sections)


def normalize_ai_authored_rich_text(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw or "AI MESSAGE DISCLAIMER" not in raw:
        return value

    body_match = re.search(r"<body\b[^>]*>(.*)</body>", raw, flags=re.IGNORECASE | re.DOTALL)
    inner = body_match.group(1) if body_match else raw
    inner = collapse_html_whitespace(inner)

    legacy_list_normalized = normalize_legacy_ai_message_list(inner)
    if legacy_list_normalized is not None:
        return canonicalize_ai_authored_markup(legacy_list_normalized)

    structured_normalized = normalize_structured_ai_message(inner)
    if structured_normalized is not None:
        return structured_normalized

    if re.search(r"<(ul|ol|blockquote|pre)\b", raw, flags=re.IGNORECASE):
        return canonicalize_ai_authored_markup(raw)

    disclaimer_match = AI_MESSAGE_HEADER_RE.match(inner)
    if not disclaimer_match:
        return value

    rest = disclaimer_match.group("rest").strip()
    sections: list[tuple[str | None, str, str | list[str]]] = []
    first_label = AI_MESSAGE_LABEL_RE.search(rest)
    intro = rest[: first_label.start()].strip() if first_label else rest
    if intro:
        sections.append((None, "scalar", intro))

    for match in AI_MESSAGE_LABEL_RE.finditer(rest[first_label.start() :] if first_label else ""):
        label_html = match.group(1).strip()
        content_html = match.group(2).strip()
        sections.append((label_html, "scalar", content_html))

    normalized = render_ai_message_sections(sections)
    if normalized is None:
        return value
    return canonicalize_ai_authored_markup(normalized)


def comment_payload_from_args(args: argparse.Namespace) -> dict[str, str]:
    text_value = load_inline_or_file_value(args.text, args.text_file, field_name="text")
    html_text_value = load_inline_or_file_value(
        args.html_text,
        args.html_text_file,
        field_name="html-text",
    )

    provided_count = sum(value is not None for value in (text_value, html_text_value))
    if provided_count != 1:
        raise SystemExit(
            "Pass exactly one of --text/--text-file or --html-text/--html-text-file"
        )

    if html_text_value is not None:
        return {"html_text": normalize_ai_authored_rich_text(html_text_value) or ""}
    return {"text": text_value or ""}


def maybe_wrap_data(payload: Any, no_wrap_data: bool) -> Any:
    if no_wrap_data:
        return payload
    if isinstance(payload, dict) and "data" in payload:
        return payload
    return {"data": payload}


def build_multipart(form_fields: dict[str, str], file_fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = f"----codex-asana-{uuid.uuid4().hex}"
    body = bytearray()

    for name, value in form_fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
        )
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")

    for field, path_str in file_fields.items():
        path = Path(path_str).expanduser().resolve()
        content = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{field}"; '
                f'filename="{path.name}"\r\n'
            ).encode("utf-8")
        )
        body.extend(f"Content-Type: {mime}\r\n\r\n".encode("utf-8"))
        body.extend(content)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def build_url(path_or_url: str, query: dict[str, str]) -> str:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        base = path_or_url
    else:
        path = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
        base = f"{BASE_URL}{path}"
    if not query:
        return base
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{parse.urlencode(query, doseq=True)}"


def network_error_detail(exc: BaseException) -> str:
    if isinstance(exc, error.URLError):
        reason = getattr(exc, "reason", exc)
        return str(reason)
    return str(exc)


def is_retryable_network_error(exc: BaseException) -> bool:
    if isinstance(exc, socket.gaierror | TimeoutError):
        return True
    if not isinstance(exc, error.URLError):
        return False
    reason = getattr(exc, "reason", None)
    if isinstance(reason, socket.gaierror | TimeoutError):
        return True
    reason_text = str(reason or "").casefold()
    return any(
        fragment in reason_text
        for fragment in (
            "temporary failure in name resolution",
            "nodename nor servname provided",
            "name or service not known",
            "timed out",
            "timeout",
        )
    )


def api_request(
    *,
    token: str,
    method: str,
    path_or_url: str,
    query: dict[str, str] | None = None,
    json_body: Any | None = None,
    multipart_form: dict[str, str] | None = None,
    multipart_files: dict[str, str] | None = None,
) -> Any:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    data = None

    if multipart_form or multipart_files:
        data, content_type = build_multipart(multipart_form or {}, multipart_files or {})
        headers["Content-Type"] = content_type
    elif json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(
        build_url(path_or_url, query or {}),
        data=data,
        headers=headers,
        method=method.upper(),
    )

    payload = ""
    for attempt in range(1, API_NETWORK_RETRY_ATTEMPTS + 1):
        try:
            with request.urlopen(req, timeout=API_TIMEOUT_SECONDS) as response:
                payload = response.read().decode("utf-8")
            break
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SystemExit(f"HTTP {exc.code} {exc.reason}\n{detail}") from exc
        except (error.URLError, TimeoutError, socket.gaierror) as exc:
            if attempt < API_NETWORK_RETRY_ATTEMPTS and is_retryable_network_error(exc):
                time.sleep(API_NETWORK_RETRY_DELAY_SECONDS * attempt)
                continue
            raise SystemExit(f"Network error while calling Asana: {network_error_detail(exc)}") from exc

    if not payload:
        return {}
    return json.loads(payload)


def maybe_paginate(token: str, first_response: Any, enabled: bool, limit_pages: int) -> Any:
    if not enabled:
        return first_response

    data = first_response.get("data")
    if not isinstance(data, list):
        return first_response

    merged = list(data)
    response = first_response
    pages = 1

    while response.get("next_page") and response["next_page"].get("uri"):
        if limit_pages and pages >= limit_pages:
            break
        response = api_request(
            token=token,
            method="GET",
            path_or_url=response["next_page"]["uri"],
        )
        page_data = response.get("data", [])
        if not isinstance(page_data, list):
            break
        merged.extend(page_data)
        pages += 1

    final_response = dict(first_response)
    final_response["data"] = merged
    final_response["pagination_pages"] = pages
    return final_response


def print_json(payload: Any, compact: bool) -> None:
    if compact:
        print(json.dumps(payload, separators=(",", ":")))
        return
    print(json.dumps(payload, indent=2, sort_keys=True))


def task_write_opt_fields() -> str:
    return (
        "gid,name,completed,completed_at,permalink_url,assignee.gid,assignee.name,"
        "due_on,due_at,parent.gid,parent.name"
    )


def story_write_opt_fields() -> str:
    return (
        "gid,created_at,resource_subtype,permalink_url,target.gid,target.name,"
        "target.permalink_url"
    )


def response_with_review_links(response: Any) -> Any:
    if not isinstance(response, dict):
        return response

    data = response.get("data")
    if not isinstance(data, dict):
        return response

    enriched = dict(response)
    review_url = str(data.get("permalink_url") or "").strip()
    if review_url:
        enriched["review_url"] = review_url

    target = data.get("target")
    if isinstance(target, dict):
        target_review_url = str(target.get("permalink_url") or "").strip()
        if target_review_url:
            enriched["target_review_url"] = target_review_url

    return enriched


def batch_actions_request(token: str, actions: list[dict[str, Any]]) -> Any:
    return api_request(
        token=token,
        method="POST",
        path_or_url="/batch",
        json_body={"data": {"actions": actions}},
    )


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        raise ValueError("Chunk size must be positive")
    return [items[index : index + size] for index in range(0, len(items), size)]


def batch_actions_request_chunked(token: str, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aggregated: list[dict[str, Any]] = []
    for action_group in chunked(actions, BATCH_ACTION_LIMIT):
        response = batch_actions_request(token, action_group)
        data = response.get("data", [])
        if not isinstance(data, list):
            raise SystemExit("Batch response did not contain a data array")
        aggregated.extend(item for item in data if isinstance(item, dict))
    return aggregated


def batch_body_at(response: Any, index: int) -> Any:
    data = response.get("data", [])
    if index >= len(data):
        return {}
    item = data[index]
    if item.get("status_code", 200) >= 400:
        return {"error": item}
    return item.get("body", {})


def section_order_from_sections(sections: list[dict[str, Any]]) -> dict[str, int]:
    order_map: dict[str, int] = {}
    for index, section in enumerate(sections, start=1):
        section_gid = section.get("gid")
        if section_gid:
            order_map[section_gid] = index
    return order_map


def render_many_results(command_name: str, entries: list[dict[str, Any]], compact: bool) -> dict[str, Any]:
    payload = {
        "command": command_name,
        "count": len(entries),
        "items": entries,
    }
    print_json(payload, compact)
    return payload


def extract_image_urls_from_html(html: str | None) -> list[str]:
    if not html:
        return []
    return re.findall(r'<img[^>]+src="([^"]+)"', html)


def comment_stories_only(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        story
        for story in stories
        if story.get("type") == "comment" or story.get("resource_subtype") == "comment_added"
    ]


def strip_html_to_text(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"</(li|blockquote|div|p|ul|ol|body)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li\b[^>]*>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        return [items[:]]
    return [items[index : index + size] for index in range(0, len(items), size)]


def user_task_list_opt_fields(default: str | None = None) -> str:
    return default or (
        "gid,name,completed,created_at,modified_at,due_on,due_at,permalink_url,"
        "resource_subtype,parent.gid,parent.name,projects.gid,projects.name,"
        "memberships.project.gid,memberships.project.name,memberships.section.gid,"
        "memberships.section.name,assignee_status,assignee_section.gid,assignee_section.name"
    )


def my_tasks_task_detail_fields(default: str | None = None) -> str:
    return default or (
        "gid,name,notes,html_notes,resource_subtype,completed,completed_at,created_at,"
        "modified_at,due_on,due_at,permalink_url,parent.gid,parent.name,"
        "assignee_status,assignee_section.gid,assignee_section.name,"
        "assignee.gid,assignee.name,followers.gid,followers.name,"
        "collaborators.gid,collaborators.name,"
        "projects.gid,projects.name,memberships.project.gid,memberships.project.name,"
        "memberships.section.gid,memberships.section.name,custom_fields.gid,"
        "custom_fields.name,custom_fields.resource_subtype,custom_fields.display_value,"
        "custom_fields.enum_value.name,tags.gid,tags.name,tags.color"
    )


def my_tasks_project(token: str, workspace_gid: str) -> dict[str, Any]:
    response = api_request(
        token=token,
        method="GET",
        path_or_url="/users/me/user_task_list",
        query={
            "workspace": workspace_gid,
            "opt_fields": "gid,name,owner.gid,owner.name,workspace.gid,workspace.name",
        },
    )
    return response.get("data", {})


def my_tasks_sections(token: str, user_task_list_gid: str) -> list[dict[str, Any]]:
    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/projects/{user_task_list_gid}/sections",
        query={"opt_fields": section_opt_fields()},
    )
    return response.get("data", [])


def my_tasks_tasks(
    token: str,
    user_task_list_gid: str,
    *,
    paginate: bool,
    limit_pages: int,
) -> list[dict[str, Any]]:
    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/user_task_lists/{user_task_list_gid}/tasks",
        query={
            "completed_since": "now",
            "limit": "100",
            "opt_fields": user_task_list_opt_fields(),
        },
    )
    response = maybe_paginate(token, response, paginate, limit_pages)
    data = response.get("data", [])
    return data if isinstance(data, list) else []


def get_task_tags(
    token: str,
    task_gid: str,
    *,
    opt_fields: str | None = None,
) -> list[dict[str, Any]]:
    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/tasks/{task_gid}/tags",
        query={"opt_fields": opt_fields or tag_opt_fields("gid,name,color")},
    )
    data = response.get("data", [])
    return data if isinstance(data, list) else []


def fetch_task_review_context(
    token: str,
    task_gids: list[str],
) -> dict[str, dict[str, Any]]:
    context_by_gid: dict[str, dict[str, Any]] = {}
    for gid_chunk in chunked(task_gids, 5):
        if not gid_chunk:
            continue
        actions: list[dict[str, Any]] = []
        for task_gid in gid_chunk:
            actions.append(
                {
                    "method": "get",
                    "relative_path": f"/tasks/{task_gid}",
                    "options": {"fields": field_list(my_tasks_task_detail_fields())},
                }
            )
            actions.append(
                {
                    "method": "get",
                    "relative_path": f"/tasks/{task_gid}/stories",
                    "options": {"fields": field_list(story_opt_fields())},
                }
            )
        batch_response = batch_actions_request(token, actions)
        for index, task_gid in enumerate(gid_chunk):
            task_body = batch_body_at(batch_response, index * 2).get("data", {})
            story_body = batch_body_at(batch_response, index * 2 + 1).get("data", [])
            context_by_gid[task_gid] = {
                "task": task_body if isinstance(task_body, dict) else {},
                "stories": story_body if isinstance(story_body, list) else [],
            }
    return context_by_gid


def task_membership_labels(task: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for membership in task.get("memberships", []) or []:
        if not isinstance(membership, dict):
            continue
        project = membership.get("project") or {}
        section = membership.get("section") or {}
        project_name = project.get("name") or "No project"
        section_name = section.get("name") or "No section"
        labels.append(f"{project_name} :: {section_name}")
    return labels


def task_project_names(task: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for project in task.get("projects", []) or []:
        if isinstance(project, dict) and project.get("name"):
            names.append(str(project["name"]))
    return names


def story_author_gids(stories: list[dict[str, Any]]) -> set[str]:
    author_gids: set[str] = set()
    for story in comment_stories_only(stories):
        created_by = story.get("created_by") or {}
        author_gid = str(created_by.get("gid") or "").strip()
        if author_gid:
            author_gids.add(author_gid)
    return author_gids


def task_participant_gids(task: dict[str, Any], field_name: str) -> set[str]:
    gids: set[str] = set()
    for participant in task.get(field_name, []) or []:
        if not isinstance(participant, dict):
            continue
        participant_gid = str(participant.get("gid") or "").strip()
        if participant_gid:
            gids.add(participant_gid)
    return gids


def task_is_shared_for_manager_comments(
    task: dict[str, Any],
    stories: list[dict[str, Any]],
) -> tuple[bool, str | None]:
    owner_gid = str(((task.get("assignee") or {}).get("gid")) or "").strip()
    project_names = task_project_names(task)
    membership_labels = task_membership_labels(task)
    parent = task.get("parent") or {}
    if project_names or membership_labels:
        return True, "Task belongs to a project/section that is likely shared with others."
    if isinstance(parent, dict) and parent.get("gid"):
        return True, "Task is a subtask/child task and may be visible in shared parent context."
    if not owner_gid:
        return True, "Task has no clear personal owner, so AI PM comments stay disabled."

    follower_gids = task_participant_gids(task, "followers")
    if follower_gids - {owner_gid}:
        return True, "Task has followers other than the assignee, so it is treated as shared."

    collaborator_gids = task_participant_gids(task, "collaborators")
    if collaborator_gids - {owner_gid}:
        return True, "Task has collaborators other than the assignee, so it is treated as shared."

    commenter_gids = story_author_gids(stories)
    if commenter_gids - {owner_gid}:
        return True, "Task has comment history from other people, so it is treated as shared."
    return False, None


def story_text(story: dict[str, Any]) -> str:
    return strip_html_to_text(story.get("html_text") or story.get("text"))


def recent_comments(task_context: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    comments = comment_stories_only(task_context.get("stories", []))
    comments.sort(key=lambda story: parse_iso_timestamp(story.get("created_at")), reverse=True)
    return comments[:limit]


def short_date(value: str | None) -> str | None:
    token = str(value or "").strip()
    if not token:
        return None
    return token.split("T", 1)[0]


def inbox_cleanup_comment_html(
    *,
    category_label: str,
    evidence_lines: list[str],
) -> str:
    sections: list[tuple[str | None, str, str | list[str]]] = [
        (
            None,
            "scalar",
            "This message was generated by AI to summarize My Tasks cleanup review state.",
        ),
        ("<strong>Review state:</strong>", "scalar", category_label),
        ("<strong>Why this looks done:</strong>", "list", evidence_lines),
        (
            "<strong>Requested action:</strong>",
            "scalar",
            "Please verify and mark complete if this matches reality.",
        ),
    ]
    return render_ai_message_sections(sections) or ""


def manager_plan_comment_html(
    *,
    category_label: str,
    work_type: str,
    task_read: str,
    classification_basis: str,
    next_action: str,
    todo_label: str,
    todo_items: list[str],
    ask_user: str,
    ai_help_summary: str,
    execution_prompt: str | None,
) -> str:
    sections: list[tuple[str | None, str, str | list[str]]] = [
        (
            None,
            "scalar",
            "This message was generated by AI to propose a personal project-manager next step for this task.",
        ),
        ("<strong>Review state:</strong>", "scalar", category_label),
        ("<strong>Work type:</strong>", "scalar", work_type),
        ("<strong>Task read:</strong>", "scalar", task_read),
        ("<strong>Why this bucket:</strong>", "scalar", classification_basis),
        ("<strong>Suggested next action:</strong>", "scalar", next_action),
        (f"<strong>{html.escape(todo_label)}:</strong>", "list", todo_items),
        ("<strong>Ask before acting:</strong>", "scalar", ask_user),
        ("<strong>How AI can help:</strong>", "scalar", ai_help_summary),
    ]
    if execution_prompt:
        sections.append(("<strong>Execution option:</strong>", "scalar", execution_prompt))
    return render_ai_message_sections(sections) or ""


def comment_already_mentions_inbox_cleanup(stories: list[dict[str, Any]], category_label: str) -> bool:
    category_token = category_label.casefold()
    for story in comment_stories_only(stories):
        text = story_text(story).casefold()
        if "my tasks cleanup review state" in text and category_token in text:
            return True
    return False


def comment_already_mentions_manager_plan(stories: list[dict[str, Any]], category_label: str) -> bool:
    category_token = category_label.casefold()
    for story in comment_stories_only(stories):
        text = story_text(story).casefold()
        if "personal project-manager next step" in text and category_token in text:
            return True
    return False


def looks_like_person_name_only(name: str) -> bool:
    token = re.sub(r"\s+", " ", str(name or "").strip())
    if not token:
        return False
    if re.search(r"[:/@#\-\(\)\[\]\d]", token):
        return False
    parts = token.split(" ")
    if not 1 < len(parts) <= 3:
        return False
    return all(part[:1].isupper() and part[1:].islower() for part in parts if part)


def infer_task_work_type(combined_text: str, name: str) -> str:
    if looks_like_person_name_only(name):
        return "admin"
    if re.search(r"\b(research|investigate|audit|analyze|analysis|r&d|information gathering|review|discuss|spec|plan)\b", combined_text):
        return "research"
    if re.search(r"\b(meeting|follow up|follow-up|email|coordinate|contact|waiting|partner|vendor|ops dashboard|details needed)\b", combined_text):
        return "coordination"
    if re.search(r"\b(bug|broken|issue|error|not working|regression|fix)\b", combined_text):
        return "bug"
    if re.search(r"\b(build|implement|create|add|remove|update|move|streamline|deploy|release|ship|endpoint|automation|hookup)\b", combined_text):
        return "implementation"
    if re.search(r"\b(check in|check-in|birthday|shopping|travel|comp time|p&l|scope of work)\b", combined_text) or "eli -" in name.casefold():
        return "admin"
    return "implementation"


def has_substantive_manager_context(
    *,
    task: dict[str, Any],
    recent_comment_lines: list[str],
) -> bool:
    notes = strip_html_to_text(task.get("html_notes") or task.get("notes"))
    if len(notes) >= 80:
        return True
    if any(len(line) >= 80 for line in recent_comment_lines):
        return True
    if task.get("due_on") and (notes or recent_comment_lines):
        return True
    return False


def extract_urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s<>\"]+", text)


def extract_github_pr_links(text: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    pattern = re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
    for match in pattern.finditer(text):
        owner, repo, pr_number = match.groups()
        links.append(
            {
                "url": match.group(0),
                "owner": owner,
                "repo": repo,
                "pr_number": pr_number,
            }
        )
    seen_pr_numbers = {str(link.get("pr_number") or "").strip() for link in links}
    for match in re.finditer(r"\bPR\s*#(\d+)\b", text, flags=re.IGNORECASE):
        pr_number = match.group(1)
        if pr_number in seen_pr_numbers:
            continue
        links.append(
            {
                "url": "",
                "owner": "",
                "repo": "",
                "pr_number": pr_number,
            }
        )
        seen_pr_numbers.add(pr_number)
    return links


def normalize_whitespace(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def shorten_text(value: str | None, *, limit: int = 180) -> str:
    token = normalize_whitespace(value)
    if len(token) <= limit:
        return token
    return f"{token[: max(0, limit - 3)].rstrip()}..."


def primary_pr_label(linked_prs: list[dict[str, str]]) -> str | None:
    if not linked_prs:
        return None
    pr_number = str((linked_prs[0] or {}).get("pr_number") or "").strip()
    if not pr_number:
        return None
    return f"PR #{pr_number}"


def task_title_kind(name: str, combined_text: str) -> str:
    source = f"{name} {combined_text}".casefold()
    if re.search(r"\b(decide|decision|clarify|validate|scope|define|recommend)\b", source):
        return "decision"
    if re.search(r"\b(draft|memo|write[\s-]?up|writeup)\b", source):
        return "writing"
    if re.search(r"\b(wireframe|mockup|design)\b", source):
        return "design"
    if re.search(r"\b(follow up|follow-up|intro|call|meeting|contact|email)\b", source):
        return "follow_up"
    return "general"


def active_ai_action_for_task(
    *,
    category_key: str,
    work_type: str,
    ready_signal: bool,
    verify_signal: bool,
    waiting_signal: bool,
    negative_signal: bool,
    linked_prs: list[dict[str, str]],
) -> dict[str, Any]:
    if ready_signal and category_key == "ready_to_close":
        return {
            "action": "ask_to_close",
            "reason": "Recent comments indicate this is likely fixed and ready for manual close-out.",
        }
    if verify_signal and category_key == "needs_verification" and not negative_signal:
        return {
            "action": "ask_to_verify",
            "reason": "This looks like verification/QA follow-up rather than net-new work.",
        }
    if linked_prs and work_type in {"implementation", "bug", "research"}:
        return {
            "action": "ask_to_execute_now",
            "reason": "This task references a PR/code path and looks like a good candidate for immediate AI execution after confirmation.",
        }
    if verify_signal and not negative_signal:
        return {
            "action": "ask_to_verify",
            "reason": "This looks like verification/QA follow-up rather than net-new work.",
        }
    if waiting_signal:
        return {
            "action": "ask_to_follow_up",
            "reason": "This looks blocked on another person or team and needs a targeted follow-up.",
        }
    if category_key == "needs_next_action" and work_type in {"research", "implementation", "bug"}:
        return {
            "action": "ask_to_execute_now",
            "reason": "This still looks open-ended, but it is concrete enough for the AI to take a first pass after confirmation.",
        }
    return {
        "action": "no_ai_action",
        "reason": "No immediate AI action is recommended from current task context.",
    }


def infer_execution_candidate(
    *,
    category_key: str,
    work_type: str,
    combined_text: str,
) -> tuple[bool, str | None]:
    if category_key in {"waiting_on_others", "backlog_cleanup"}:
        return False, None
    if work_type in {"implementation", "bug", "research"} and re.search(
        r"\b(pr\b|github|preview|branch|webgui|api|plugin|repo|account app|automation|endpoint|beta|test|memo|draft|clarify|validate|decide|scope|recommendation|doc|document|spec)\b",
        combined_text,
    ):
        return True, "Ask whether I should investigate or execute this task now, and I can try to solve it end-to-end."
    return False, None


def manager_plan_for_task(
    *,
    name: str,
    work_type: str,
    category_key: str,
    task: dict[str, Any],
    combined_text: str,
    reasons: list[str],
    recent_comment_lines: list[str],
    linked_prs: list[dict[str, str]],
    ready_signal: bool,
    verify_signal: bool,
    waiting_signal: bool,
    negative_signal: bool,
) -> dict[str, Any]:
    name = str(name or task.get("name") or "")
    due_on = task.get("due_on")
    pr_label = primary_pr_label(linked_prs)
    kind = task_title_kind(name, combined_text)
    project_state = ""
    if reasons:
        first_reason = str(reasons[0])
        if first_reason.startswith("Project state: "):
            project_state = first_reason.removeprefix("Project state: ").strip()
    comment_excerpt = shorten_text(recent_comment_lines[0] if recent_comment_lines else "", limit=160)
    task_read = ""
    classification_basis = ""
    todo_label = "Suggested TODO"
    next_action = "Review the task and decide the concrete next owner/action."
    ask_user = "Should this stay active right now, or should it be parked until there is a concrete next step?"
    ai_help_summary = "This needs your prioritization more than immediate AI action."
    todo_items: list[str] = []

    if category_key == "ready_to_close":
        task_read = "This looks substantially done already and is behaving more like close-out than active execution work."
        if project_state:
            task_read = f"{task_read} Project state already reads as `{project_state}`."
        if comment_excerpt:
            task_read = f"{task_read} Latest signal: {comment_excerpt}"
        classification_basis = "Recent comments plus project-state context both point toward close-out."
        next_action = "Do one final verification pass, then close it or reopen it with the exact remaining issue."
        todo_label = "Close-Out TODO"
        todo_items = [
            "Check the latest build, preview, or shipped state one more time.",
            "Capture one clear pass/fail note so the task does not stay in limbo.",
            "If it passes, post the close-out note and mark it complete.",
            "If it fails, reopen it with the exact remaining repro or gap.",
        ]
        ask_user = "Do you want me to draft the close-out note after a quick verification read?"
        ai_help_summary = "I can review the latest context and draft the close-out note or the reopen summary."
    elif category_key == "needs_verification":
        verification_target = pr_label or "the latest claimed fix"
        if project_state:
            task_read = f"This no longer looks like fresh implementation work. It looks like verification against `{project_state}`"
        else:
            task_read = "This no longer looks like fresh implementation work. It looks like verification against the latest claimed fix"
        if pr_label:
            task_read = f"{task_read} and {pr_label}."
        else:
            task_read = f"{task_read}."
        if comment_excerpt:
            task_read = f"{task_read} Latest signal: {comment_excerpt}"
        classification_basis = "Verification language or done-like workflow state outweighed net-new implementation signals."
        next_action = f"Verify {verification_target} on the latest relevant environment and decide between close-out and a new implementation step."
        todo_label = "Verification TODO"
        todo_items = [
            "Collect the exact build, branch, or environment you should test against.",
            f"Verify {verification_target} and record what passed or failed.",
            "If it passes, move it toward close-out with evidence.",
            "If it fails, write the smallest remaining gap as the new implementation step.",
        ]
        ask_user = "Do you want me to run the verification pass now, or just leave this staged in verification?"
        ai_help_summary = "I can verify the latest change, summarize pass/fail evidence, and recommend close-out versus reopen."
    elif category_key == "waiting_on_others" or waiting_signal:
        task_read = "This looks blocked on another person, team, or external dependency rather than blocked on your own implementation work."
        if comment_excerpt:
            task_read = f"{task_read} Latest signal: {comment_excerpt}"
        classification_basis = "The strongest signal here is dependency-follow-up, not independent execution."
        todo_label = "Coordination TODO"
        next_action = "Send one concrete unblock message, make the ask explicit, and record the expected reply or handoff date."
        todo_items = [
            "Identify the exact person or team who needs to respond.",
            "State the concrete ask, not just a status nudge.",
            "Record the expected reply date or next checkpoint in the task.",
            "Move it back to execution only after the dependency clears.",
        ]
        ask_user = "Who should be nudged here, and do you want me to draft the exact follow-up message?"
        ai_help_summary = "I can draft the unblock message or task comment for you."
    elif work_type == "research":
        todo_label = "Research TODO"
        if kind == "writing":
            task_read = "This is a writing/recommendation task, not a vague research placeholder. The real missing artifact is the first draft."
            next_action = "Write the first draft now, then call out the recommendation, open questions, and owner."
            todo_items = [
                "Write the exact question this memo or write-up needs to answer.",
                "Draft the recommendation or narrative directly in the task or linked doc.",
                "Call out what is still unknown or needs sign-off.",
                "Name the next reviewer or owner so it does not stall after drafting.",
            ]
            ask_user = "Do you want me to draft the first version now, or should this remain a placeholder?"
            ai_help_summary = "I can draft the memo or recommendation from the current task context."
        else:
            task_read = "This is a decision/research task. The missing deliverable is a short recommendation that turns the open question into a call to action."
            next_action = "Turn this into a short written recommendation with a clear decision owner and follow-up."
            todo_items = [
                "Read the task description, linked docs, PRs, and the latest comments.",
                "Write a 3-5 bullet summary of the current state and what is still unknown.",
                "List the specific decision this research should unblock.",
                "Recommend one next action, one owner, and one target date.",
            ]
            ask_user = "Do you want me to turn this into a recommendation now, or is this waiting on human discussion first?"
            ai_help_summary = "I can synthesize the current context into a recommendation and next-step note."
        classification_basis = "Research-style language points to a recommendation artifact, not just another round of vague investigation."
        if re.search(r"\b(spreadsheet|sheet|docs.google.com|document|spec)\b", combined_text):
            todo_items.insert(1, "Review the linked document/spreadsheet and capture the key takeaways in the task.")
    elif work_type == "bug":
        todo_label = "Bug TODO"
        task_read = "This is still a bug triage task. The useful outcome is not more discussion; it is either a confirmed repro or a confirmed fix."
        if pr_label:
            task_read = f"{task_read} There is already a concrete code signal via {pr_label}."
        if negative_signal:
            task_read = f"{task_read} Recent context also suggests the issue may still be failing."
        classification_basis = "Bug language is the dominant signal, so the task should drive toward repro-versus-fixed, not vague planning."
        next_action = "Confirm whether this is still reproducible, then either close it as fixed or create the smallest remaining implementation step."
        todo_items = [
            "Collect the latest repro details, version, and any screenshots or logs.",
            "Verify whether the issue still reproduces on the latest relevant build.",
            "If fixed, comment with evidence and ask for close-out.",
            "If not fixed, define the smallest next engineering step and owner.",
        ]
        ask_user = "Do you want me to investigate this now and come back with either a narrowed repro or the smallest next fix?"
        ai_help_summary = "I can narrow the repro path or draft the concrete remaining fix step."
    elif work_type == "implementation":
        todo_label = "Implementation TODO"
        if kind == "decision":
            task_read = "This is not really implementation yet. It is a scoping or decision task that still needs a concrete recommendation before code can move cleanly."
            next_action = "Write the scoped recommendation, name the owner, and define the first testable milestone that would follow from that decision."
            todo_items = [
                "Write the exact decision or scope question in one sentence.",
                "List the constraints or options that matter.",
                "Recommend one direction instead of leaving it open-ended.",
                "Define the first milestone that would start once the decision is accepted.",
            ]
            ask_user = "Do you want me to draft the recommendation now, or is this waiting on discussion first?"
            ai_help_summary = "I can draft the recommendation and reduce this to a shippable first milestone."
        elif kind == "writing":
            task_read = "This is really a writing/output task hiding inside implementation. The missing artifact is a concrete document, not another placeholder."
            next_action = "Draft the output, then move the task into review or verification once the first pass exists."
            todo_items = [
                "Define the output artifact this task is supposed to produce.",
                "Draft the first pass instead of keeping the task broad.",
                "Link the draft or working area back into the task.",
                "Move it to review once there is something testable or reviewable.",
            ]
            ask_user = "Do you want me to produce the first draft now?"
            ai_help_summary = "I can create the first pass so this stops being a placeholder."
        else:
            task_read = "This is an implementation task, but it is still too broad to drive execution cleanly from the task as written."
            if pr_label:
                task_read = f"{task_read} There is already a code signal via {pr_label}, so the best next move is to define the next testable milestone."
            classification_basis = "Implementation signals are present, but the task still needs a smaller, more testable milestone."
            next_action = "Reduce this to one concrete shipping step instead of leaving it as a broad open task."
            todo_items = [
                "Confirm the exact deliverable for this task.",
                "Link the active PR, branch, or code area if one exists.",
                "Define the next testable milestone.",
                "Move it to verification once that milestone is shipped.",
            ]
            ask_user = "Do you want me to turn this into a concrete first milestone now?"
            ai_help_summary = "I can define the first milestone and, if there is code context, push it forward."
        if not classification_basis:
            classification_basis = "The title and recent context still read as open implementation work, but not yet as a crisp milestone."
    elif work_type == "coordination":
        todo_label = "Coordination TODO"
        task_read = "This is primarily a coordination task. The value comes from creating movement with one clear ask, not from doing more solo work."
        classification_basis = "The strongest signal is handoff/follow-up language instead of implementation details."
        next_action = "Push the external dependency forward with one specific follow-up."
        todo_items = [
            "Identify the external person or team currently blocking progress.",
            "Post a follow-up comment or send a ping with the concrete ask.",
            "Record the expected reply date or handoff date in the task.",
            "Move back to execution once the dependency clears.",
        ]
        ask_user = "Who should receive the follow-up, and do you want me to draft it?"
        ai_help_summary = "I can draft the follow-up or summarize the current ask."
    else:
        todo_label = "Admin TODO"
        task_read = "This reads more like a reminder or admin placeholder than meaningful engineering work."
        classification_basis = "The task does not currently contain enough execution substance to justify active focus."
        next_action = "Either schedule the work or move it out of active My Tasks if it is not actionable this cycle."
        todo_items = [
            "Decide whether this belongs in active My Tasks right now.",
            "If yes, assign a date or next checkpoint.",
            "If no, move it to backlog or an appropriate holding section.",
        ]
        ask_user = "Should this stay in your active queue, or should it be parked in a holding section?"
        ai_help_summary = "I can help re-scope or re-file it, but it does not look like a good execution target."

    if not task_read:
        task_read = "This task needs a clearer definition before it can drive execution."
    if not classification_basis:
        classification_basis = "The current signals are mixed, so the safest move is to define the next concrete owner/action."

    if due_on:
        todo_items.append(f"Check whether the due date `{due_on}` still makes sense.")
    if reasons:
        todo_items.append(f"Use this context when updating the task: {reasons[0]}")
    if recent_comment_lines:
        todo_items.append("Incorporate the latest comment context before changing scope or status.")

    execution_candidate, execution_prompt = infer_execution_candidate(
        category_key=category_key,
        work_type=work_type,
        combined_text=combined_text,
    )

    return {
        "work_type": work_type,
        "task_read": task_read,
        "classification_basis": classification_basis,
        "next_action": next_action,
        "todo_label": todo_label,
        "todo_items": todo_items[:6],
        "ask_user": ask_user,
        "ai_help_now": category_key != "backlog_cleanup",
        "ai_help_summary": ai_help_summary,
        "execution_candidate": execution_candidate,
        "execution_prompt": execution_prompt,
    }


def classify_inbox_cleanup_task(task_context: dict[str, Any], now: datetime) -> dict[str, Any]:
    task = task_context.get("task", {})
    stories = task_context.get("stories", [])
    name = str(task.get("name") or "")
    notes = strip_html_to_text(task.get("html_notes") or task.get("notes"))
    membership_labels = task_membership_labels(task)
    project_names = task_project_names(task)
    current_my_tasks_section = ((task.get("assignee_section") or {}).get("name") or "").strip()
    recent = recent_comments(task_context, limit=3)
    recent_comment_lines = [
        f"{comment.get('created_by', {}).get('name') or 'Unknown'} on {short_date(comment.get('created_at'))}: {story_text(comment)}"
        for comment in recent
        if story_text(comment)
    ]

    combined_text = " ".join(
        [
            name,
            notes,
            " ".join(membership_labels),
            " ".join(project_names),
            " ".join(recent_comment_lines),
        ]
    ).casefold()
    modified_at = parse_iso_timestamp(task.get("modified_at"))
    created_at = parse_iso_timestamp(task.get("created_at"))
    stale_days = max(0, int((now - modified_at).total_seconds() // 86400))
    age_days = max(0, int((now - created_at).total_seconds() // 86400))
    linked_prs = extract_github_pr_links(combined_text)
    linked_urls = extract_urls(combined_text)

    ready_signal = bool(
        re.search(
            r"\b(confirmed fixed|confirmed both working|fully resolved|most likely fully resolved|working for beta|works on|working for|shows correctly|looks ok to me|looks done|shipped)\b",
            combined_text,
        )
    )
    verify_signal = bool(
        re.search(
            r"\b(pr\b|preview|staging|test|beta|rc|should be out now|should be fixed|please test|confirmed)\b",
            combined_text,
        )
    )
    waiting_signal = bool(
        re.search(
            r"\b(blocked|waiting|emailed|email|cc'd|contact|confirm with|needs info|details needed|partner|vendor|external)\b",
            combined_text,
        )
    ) or "[blocked]" in name.casefold()
    negative_signal = bool(
        re.search(
            r"\b(still happening|still broken|not working|another fix|needs another fix|difficult to be 100% confident)\b",
            combined_text,
        )
    )
    backlog_signal = stale_days >= 30 or bool(
        re.search(r"\b(backlog|nice to have|initiatives|feature backlog)\b", combined_text)
    )
    done_like_signal = bool(
        re.search(r"\b(production|done|qa|completed|staging|test)\b", " ".join(membership_labels).casefold())
    )

    reasons: list[str] = []
    if membership_labels:
        reasons.append(f"Project state: {membership_labels[0]}")
    if task.get("due_on"):
        reasons.append(f"Due date: {task['due_on']}")
    reasons.append(f"Last updated {stale_days} day(s) ago")
    if recent_comment_lines:
        reasons.extend(recent_comment_lines[:2])

    work_type = infer_task_work_type(combined_text, name)
    shared_for_manager_comments, shared_reason = task_is_shared_for_manager_comments(task, stories)
    substantive_manager_context = has_substantive_manager_context(
        task=task,
        recent_comment_lines=recent_comment_lines,
    )

    category_key = "needs_next_action"
    if ready_signal and done_like_signal and not negative_signal:
        category_key = "ready_to_close"
    elif verify_signal and not negative_signal:
        category_key = "needs_verification"
    elif waiting_signal:
        category_key = "waiting_on_others"
    elif backlog_signal and stale_days >= 14:
        category_key = "backlog_cleanup"

    # Fresh tasks with little context should stay in the action bucket instead of backlog.
    if category_key == "backlog_cleanup" and age_days < 14:
        category_key = "needs_next_action"

    manager_plan = manager_plan_for_task(
        name=name,
        work_type=work_type,
        category_key=category_key,
        task=task,
        combined_text=combined_text,
        reasons=reasons,
        recent_comment_lines=recent_comment_lines,
        linked_prs=linked_prs,
        ready_signal=ready_signal,
        verify_signal=verify_signal,
        waiting_signal=waiting_signal,
        negative_signal=negative_signal,
    )
    manager_comment_allowed = (
        not shared_for_manager_comments
        and bool(substantive_manager_context)
        and work_type != "admin"
    )
    active_ai_action = active_ai_action_for_task(
        category_key=category_key,
        work_type=work_type,
        ready_signal=ready_signal,
        verify_signal=verify_signal,
        waiting_signal=waiting_signal,
        negative_signal=negative_signal,
        linked_prs=linked_prs,
    )

    return {
        "category_key": category_key,
        "category_label": INBOX_CLEANUP_REVIEW_SECTIONS[category_key],
        "current_my_tasks_section": current_my_tasks_section,
        "reasons": reasons,
        "stale_days": stale_days,
        "age_days": age_days,
        "work_type": work_type,
        "manager_plan": manager_plan,
        "shared_for_manager_comments": shared_for_manager_comments,
        "shared_for_manager_comments_reason": shared_reason,
        "substantive_manager_context": substantive_manager_context,
        "manager_comment_allowed": manager_comment_allowed,
        "linked_prs": linked_prs,
        "linked_urls": linked_urls[:10],
        "active_ai_action": active_ai_action,
    }


def ensure_review_sections(
    token: str,
    *,
    user_task_list_gid: str,
    existing_sections: list[dict[str, Any]],
    apply: bool,
) -> dict[str, dict[str, Any]]:
    sections_by_name: dict[str, dict[str, Any]] = {
        str(section.get("name")): dict(section)
        for section in existing_sections
        if isinstance(section, dict) and section.get("name")
    }
    for section_name in INBOX_CLEANUP_REVIEW_SECTIONS.values():
        if section_name in sections_by_name or not apply:
            continue
        created = api_request(
            token=token,
            method="POST",
            path_or_url=f"/projects/{user_task_list_gid}/sections",
            json_body={"data": {"name": section_name}},
        ).get("data", {})
        if isinstance(created, dict) and created.get("name"):
            sections_by_name[str(created["name"])] = created
    return sections_by_name


def infer_workspace_gid_from_payload(payload: dict[str, Any], cache: dict[str, Any]) -> str | None:
    data = payload.get("data")
    if isinstance(data, dict):
        workspace = data.get("workspace")
        if isinstance(workspace, dict) and workspace.get("gid"):
            return str(workspace["gid"])
        workspaces = data.get("workspaces")
        if isinstance(workspaces, list) and workspaces:
            first = workspaces[0]
            if isinstance(first, dict) and first.get("gid"):
                return str(first["gid"])
    workspaces = cache_bucket(cache, "workspaces").get("by_gid", {})
    if len(workspaces) == 1:
        return next(iter(workspaces.keys()))
    return None


def my_tasks_summary(
    token: str,
    *,
    workspace_gid: str,
    cache: dict[str, Any],
    refresh: bool = False,
    max_age_hours: int = 6,
) -> dict[str, Any]:
    summaries = metadata_bucket(cache, "my_tasks_summary_by_workspace")
    existing = summaries.get(workspace_gid)
    if isinstance(existing, dict) and not refresh:
        captured_at = parse_iso_timestamp(existing.get("captured_at"))
        age_seconds = (datetime.now(timezone.utc) - captured_at).total_seconds()
        if age_seconds <= max_age_hours * 3600:
            return existing

    user_task_list = my_tasks_project(token, workspace_gid)
    user_task_list_gid = str(user_task_list.get("gid") or "").strip()
    tasks = my_tasks_tasks(token, user_task_list_gid, paginate=True, limit_pages=0) if user_task_list_gid else []
    section_counts: dict[str, int] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        section_name = str(((task.get("assignee_section") or {}).get("name")) or "Unsectioned")
        section_counts[section_name] = section_counts.get(section_name, 0) + 1

    summary = {
        "captured_at": now_iso(),
        "user_task_list_gid": user_task_list_gid,
        "user_task_list_name": user_task_list.get("name"),
        "open_task_count": len(tasks),
        "recently_assigned_count": section_counts.get("Recently assigned", 0),
        "review_task_count": sum(
            count
            for section_name, count in section_counts.items()
            if section_name in INBOX_CLEANUP_REVIEW_SECTIONS.values()
        ),
        "section_counts": section_counts,
    }
    summaries[workspace_gid] = summary
    save_cache(cache)
    return summary


def advertising_message_for_my_tasks(summary: dict[str, Any]) -> dict[str, Any]:
    open_count = int(summary.get("open_task_count") or 0)
    recently_assigned = int(summary.get("recently_assigned_count") or 0)
    review_count = int(summary.get("review_task_count") or 0)

    recommendation = {
        "priority": "low",
        "message": "My Tasks looks manageable. Use inbox cleanup when you want an AI-gated category plan for your tasks.",
        "command": "python3 scripts/asana_api.py inbox-cleanup",
    }
    if recently_assigned >= 50:
        recommendation = {
            "priority": "high",
            "message": f"My Tasks intake is large: {recently_assigned} tasks in Recently assigned and {open_count} open overall.",
            "command": "python3 scripts/asana_api.py inbox-cleanup",
        }
    elif recently_assigned >= 15:
        recommendation = {
            "priority": "medium",
            "message": f"My Tasks intake is building up: {recently_assigned} tasks in Recently assigned.",
            "command": "python3 scripts/asana_api.py inbox-cleanup",
        }
    elif review_count > 0:
        recommendation = {
            "priority": "low",
            "message": f"My Tasks already has {review_count} tasks in Review sections. Start with a morning command center before doing another cleanup pass.",
            "command": "python3 scripts/asana_api.py daily-briefing",
        }
    return recommendation


def skill_feature_highlights() -> list[dict[str, str]]:
    return [
        {
            "command": "python3 scripts/asana_api.py inbox-cleanup",
            "use_when": "Generate an AI-gated cleanup snapshot and plan scaffold for My Tasks before any bucket moves happen.",
        },
        {
            "command": "python3 scripts/asana_api.py inbox-cleanup --plan-file /tmp/asana-inbox-plan.json --apply",
            "use_when": "Apply an AI-authored cleanup plan after categories and bucket decisions have been reviewed.",
        },
        {
            "command": "python3 scripts/asana_api.py daily-briefing",
            "use_when": "Run the AI-gated daily briefing workflow: generate the snapshot internally, auto-author the briefing plan, and only ask the user about truly ambiguous tasks.",
        },
        {
            "command": "python3 scripts/asana_api.py daily-briefing --plan-file /tmp/asana-daily-briefing-plan.json --markdown",
            "use_when": "Render an AI-authored morning command center after the briefing plan has been reviewed.",
        },
        {
            "command": "python3 scripts/asana_api.py task-bundle <task_gid> --project-gid <project_gid>",
            "use_when": "Pull one task with notes, comments, attachments, and project workflow context.",
        },
        {
            "command": "python3 scripts/asana_api.py project-assigned-tasks <project_gid> --completed false --include-task-position --include-comments --comment-limit 3 --include-attachments",
            "use_when": "Get your actual assigned work in one project with triage context.",
        },
        {
            "command": "python3 scripts/asana_api.py close-out-sections <project_gid> --section \"Old Section\" --move-to \"Work Completed\" --completed-mode completed --apply",
            "use_when": "Relocate tasks out of stale personal sections, then delete the section once it is empty.",
        },
        {
            "command": "python3 scripts/asana_api.py search-tasks --text \"<query>\" --assignee me",
            "use_when": "Find tasks quickly across the workspace without clicking around.",
        },
    ]


def attach_skill_advertising(
    *,
    args: argparse.Namespace,
    payload: Any,
    token: str | None = None,
    workspace_gid: str | None = None,
    cache: dict[str, Any] | None = None,
    include_first_run: bool = True,
    refresh_summary: bool = False,
) -> Any:
    if not isinstance(payload, dict):
        return payload

    cache = cache or load_cache()
    metadata = ensure_cache_shape(cache).setdefault("metadata", {})
    current_user_gid = str(metadata.get("current_user_gid") or "").strip()
    if not current_user_gid:
        current_user_gid = str((((payload.get("data") or {}) if isinstance(payload.get("data"), dict) else {}).get("gid")) or "").strip()

    resolved_workspace_gid = workspace_gid or infer_workspace_gid_from_payload(payload, cache)
    my_tasks_info = None
    recommendation = None
    if token and resolved_workspace_gid:
        my_tasks_info = my_tasks_summary(
            token,
            workspace_gid=resolved_workspace_gid,
            cache=cache,
            refresh=refresh_summary,
        )
        recommendation = advertising_message_for_my_tasks(my_tasks_info)

    onboarding_bucket = metadata_bucket(cache, "feature_onboarding")
    onboarding_key = f"{current_user_gid}:{resolved_workspace_gid or 'unknown'}"
    first_run = include_first_run and current_user_gid and onboarding_key not in onboarding_bucket

    advertising: dict[str, Any] = {
        "my_tasks": my_tasks_info,
        "recommended_next_step": recommendation,
    }
    if first_run:
        advertising["first_run"] = True
        advertising["message"] = (
            "This Asana integration can triage My Tasks, inspect single tickets with full context, "
            "and pull your assigned work inside projects."
        )
        advertising["feature_highlights"] = skill_feature_highlights()
        onboarding_bucket[onboarding_key] = {
            "shown_at": now_iso(),
            "command": getattr(args, "command", None),
        }
        save_cache(cache)
    else:
        advertising["first_run"] = False

    enriched = dict(payload)
    enriched["skill_advertising"] = advertising
    return enriched


def recent_comment_stories(
    stories: list[dict[str, Any]],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    comments = sorted(
        comment_stories_only(stories),
        key=lambda story: parse_iso_timestamp(story.get("created_at")),
    )
    if limit is None or limit <= 0:
        return comments
    return comments[-limit:]


def field_list(csv_fields: str) -> list[str]:
    return [field.strip() for field in csv_fields.split(",") if field.strip()]


def workspace_default(
    args: argparse.Namespace,
    context: dict[str, Any],
    cache: dict[str, Any] | None = None,
) -> str | None:
    value = getattr(args, "workspace", None) or context.get("workspace_gid")
    if not value:
        return None
    if value == context.get("workspace_name"):
        return context.get("workspace_gid")
    if cache:
        record = find_cached_record(cache, "workspaces", value, fields=("name",))
        if record:
            return str(record.get("gid"))
    return value


def team_default(
    args: argparse.Namespace,
    context: dict[str, Any],
    cache: dict[str, Any] | None = None,
) -> str | None:
    value = getattr(args, "team", None) or context.get("team_gid")
    if not value:
        return None
    teams = context.get("teams", {})
    if value in teams:
        return value
    lowered = value.casefold()
    for gid, name in teams.items():
        if str(name).casefold() == lowered:
            return gid
    if cache:
        record = find_cached_record(cache, "teams", value, fields=("name",))
        if record:
            return str(record.get("gid"))
    return value


def resolve_user_identifier(
    value: str | None,
    context: dict[str, Any],
    cache: dict[str, Any] | None = None,
) -> str | None:
    token = str(value or "").strip()
    if not token:
        return None
    if token == "me":
        return context.get("user_gid") or "me"
    if token == context.get("user_name") or token == context.get("user_gid"):
        return context.get("user_gid") or token
    if is_gid_like(token):
        return token
    if cache:
        record = find_cached_record(cache, "users", token, fields=("name", "email"))
        if record:
            return str(record.get("gid"))
    return token


def resolve_many_user_identifiers(
    values: list[str] | None,
    context: dict[str, Any],
    cache: dict[str, Any] | None = None,
) -> list[str]:
    resolved: list[str] = []
    for item in parse_many_gid(values):
        user_gid = resolve_user_identifier(item, context, cache)
        if user_gid:
            resolved.append(user_gid)
    return resolved


def refresh_workspace_tags(
    token: str,
    workspace_gid: str,
    cache: dict[str, Any],
) -> list[dict[str, Any]]:
    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/workspaces/{workspace_gid}/tags",
        query={"opt_fields": tag_opt_fields("gid,name,color")},
    )
    tags = response.get("data", [])
    if isinstance(tags, list):
        cache_records(
            cache,
            "tags",
            [tag_cache_record(item, workspace_gid=workspace_gid) for item in tags if isinstance(item, dict)],
        )
        save_cache(cache)
        return tags
    return []


def resolve_tag_identifier(
    value: str | None,
    *,
    token: str,
    cache: dict[str, Any],
    workspace_gid: str | None = None,
) -> dict[str, Any]:
    token_value = str(value or "").strip()
    if not token_value:
        raise SystemExit("Missing tag identifier.")

    record = find_cached_tag_record(cache, token_value, workspace_gid=workspace_gid)
    if record:
        return record

    if workspace_gid:
        refresh_workspace_tags(token, workspace_gid, cache)
        record = find_cached_tag_record(cache, token_value, workspace_gid=workspace_gid)
        if record:
            return record

    workspace_note = f" in workspace {workspace_gid}" if workspace_gid else ""
    raise SystemExit(f"No tag matching '{token_value}' was found{workspace_note}.")


def normalize_sorting_tag_labels(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in values or list(DEFAULT_SORTING_TAG_LABELS):
        label = str(raw_value or "").strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(label)
    return normalized


def add_common_output_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--compact", action="store_true", help="Print compact JSON")


def task_opt_fields(default: str | None = None) -> str:
    return default or (
        "gid,name,completed,assignee.name,due_on,due_at,projects.name,"
        "memberships.section.name,parent.name,permalink_url"
    )


def section_close_out_task_opt_fields(default: str | None = None) -> str:
    return default or "gid,name,completed,completed_at,permalink_url"


def project_assigned_task_opt_fields(default: str | None = None) -> str:
    return default or (
        "gid,name,completed,assignee.gid,assignee.name,due_on,due_at,permalink_url,"
        "parent.gid,parent.name,projects.gid,projects.name,memberships.project.gid,"
        "memberships.project.name,memberships.section.gid,memberships.section.name"
    )


def parent_task_context_opt_fields(default: str | None = None) -> str:
    return default or (
        "gid,name,permalink_url,memberships.project.gid,memberships.project.name,"
        "memberships.section.gid,memberships.section.name"
    )


def section_opt_fields(default: str | None = None) -> str:
    return default or "gid,name,project.name,created_at"


def tag_opt_fields(default: str | None = None) -> str:
    return default or "gid,name,color,notes,followers.name,permalink_url"


def story_opt_fields(default: str | None = None) -> str:
    return default or "gid,created_at,created_by.gid,created_by.name,text,html_text,type,resource_subtype"


def story_detail_opt_fields(default: str | None = None) -> str:
    return default or (
        "gid,created_at,created_by.gid,created_by.name,text,html_text,type,"
        "resource_subtype,permalink_url,target.gid,target.name,target.resource_type,"
        "target.permalink_url"
    )


def custom_field_setting_opt_fields(default: str | None = None) -> str:
    return default or (
        "gid,is_important,custom_field.gid,custom_field.name,"
        "custom_field.resource_subtype,custom_field.display_value,"
        "custom_field.enum_options.name"
    )


def attachment_opt_fields(default: str | None = None) -> str:
    return default or "gid,name,resource_subtype,download_url,permanent_url,view_url,host,parent_type,parent.name"


def task_status_fields(default: str | None = None) -> str:
    return default or (
        "gid,name,completed,completed_at,permalink_url,assignee.name,due_on,due_at,"
        "memberships.project.gid,memberships.project.name,memberships.section.gid,"
        "memberships.section.name,custom_fields.gid,custom_fields.name,"
        "custom_fields.resource_subtype,custom_fields.display_value"
    )


def build_task_list_query(args: argparse.Namespace, default_fields: str | None = None) -> dict[str, str]:
    return {"opt_fields": args.opt_fields or task_opt_fields(default_fields)}


def post_task_relationship(
    args: argparse.Namespace,
    path: str,
    payload: dict[str, Any],
) -> Any:
    token = get_token(args)
    response = api_request(
        token=token,
        method="POST",
        path_or_url=path,
        json_body={"data": payload},
    )
    print_json(response, args.compact)
    return response


def section_order_map(token: str, project_gid: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/projects/{project_gid}/sections",
        query={"opt_fields": section_opt_fields()},
    )
    sections = response.get("data", [])
    order_map: dict[str, int] = {}
    for index, section in enumerate(sections, start=1):
        section_gid = section.get("gid")
        if section_gid:
            order_map[section_gid] = index
    return sections, order_map


def section_task_positions_map(
    token: str,
    section_gids: list[str],
) -> dict[str, dict[str, int]]:
    unique_section_gids: list[str] = []
    seen: set[str] = set()
    for section_gid in section_gids:
        if section_gid and section_gid not in seen:
            seen.add(section_gid)
            unique_section_gids.append(section_gid)

    positions_by_section: dict[str, dict[str, int]] = {}
    for section_gid in unique_section_gids:
        section_tasks = api_request(
            token=token,
            method="GET",
            path_or_url=f"/sections/{section_gid}/tasks",
            query={"opt_fields": "gid"},
        ).get("data", [])
        positions_by_section[section_gid] = {
            str(section_task.get("gid")): index
            for index, section_task in enumerate(section_tasks, start=1)
            if isinstance(section_task, dict) and section_task.get("gid")
        }
    return positions_by_section


def fetch_all_section_tasks(
    token: str,
    section_gid: str,
    *,
    opt_fields: str | None = None,
    limit_pages: int = 0,
) -> list[dict[str, Any]]:
    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/sections/{section_gid}/tasks",
        query={
            "opt_fields": opt_fields or section_close_out_task_opt_fields(),
            "limit": "100",
        },
    )
    response = maybe_paginate(token, response, True, limit_pages)
    data = response.get("data", [])
    if not isinstance(data, list):
        return []
    return [task for task in data if isinstance(task, dict)]


def select_section_tasks(
    tasks: list[dict[str, Any]],
    completed_mode: str,
) -> list[dict[str, Any]]:
    if completed_mode == "all":
        return list(tasks)
    if completed_mode == "completed":
        return [task for task in tasks if bool(task.get("completed"))]
    if completed_mode == "incomplete":
        return [task for task in tasks if not bool(task.get("completed"))]
    raise SystemExit(f"Unsupported completed mode: {completed_mode}")


def command_request(args: argparse.Namespace) -> Any:
    token = get_token(args)
    query = parse_kv(args.query)
    if args.opt_fields:
        query["opt_fields"] = args.opt_fields
    if args.opt_expand:
        query["opt_expand"] = args.opt_expand

    json_body = None
    form_body = parse_kv(args.form)
    file_body = parse_kv(args.file)

    if args.data and args.data_file:
        raise SystemExit("Pass only one of --data or --data-file")

    if args.data:
        json_body = maybe_wrap_data(json.loads(args.data), args.no_wrap_data)
    elif args.data_file:
        json_body = maybe_wrap_data(json.loads(Path(args.data_file).read_text()), args.no_wrap_data)

    response = api_request(
        token=token,
        method=args.method,
        path_or_url=args.path,
        query=query,
        json_body=json_body,
        multipart_form=form_body,
        multipart_files=file_body,
    )
    response = maybe_paginate(token, response, args.paginate, args.limit_pages)
    print_json(response, args.compact)
    return response


def command_whoami(args: argparse.Namespace) -> Any:
    token = get_token(args)
    response = api_request(
        token=token,
        method="GET",
        path_or_url="/users/me",
        query={"opt_fields": "gid,name,email,workspaces.name"},
    )
    cache = load_cache()
    user = response.get("data", {})
    if isinstance(user, dict):
        cache_record(cache, "users", user_cache_record(user))
        workspaces = user.get("workspaces", [])
        if isinstance(workspaces, list):
            cache_records(cache, "workspaces", [workspace_cache_record(item) for item in workspaces if isinstance(item, dict)])
        cache["metadata"]["current_user_gid"] = user.get("gid")
        cache["metadata"]["current_user_name"] = user.get("name")
        save_cache(cache)
    enriched = attach_skill_advertising(
        args=args,
        payload=response,
        token=token,
        cache=cache,
        refresh_summary=True,
    )
    print_json(enriched, args.compact)
    return enriched


def command_workspaces(args: argparse.Namespace) -> Any:
    token = get_token(args)
    response = api_request(
        token=token,
        method="GET",
        path_or_url="/users/me/workspaces",
    )
    cache = load_cache()
    workspaces = response.get("data", [])
    if isinstance(workspaces, list):
        cache_records(cache, "workspaces", [workspace_cache_record(item) for item in workspaces if isinstance(item, dict)])
        save_cache(cache)
    enriched = attach_skill_advertising(
        args=args,
        payload=response,
        token=token,
        cache=cache,
    )
    print_json(enriched, args.compact)
    return enriched


def command_teams(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    workspace = workspace_default(args, context, cache)
    if not workspace:
        raise SystemExit("No workspace GID provided and no default workspace in asana-context.json")
    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/workspaces/{workspace}/teams",
    )
    teams = response.get("data", [])
    if isinstance(teams, list):
        cache_records(
            cache,
            "teams",
            [team_cache_record(item, workspace_gid=workspace) for item in teams if isinstance(item, dict)],
        )
        save_cache(cache)
    enriched = attach_skill_advertising(
        args=args,
        payload=response,
        token=token,
        workspace_gid=workspace,
        cache=cache,
    )
    print_json(enriched, args.compact)
    return enriched


def command_projects(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    team = team_default(args, context, cache)
    if not team:
        raise SystemExit("No team GID provided and no default team in asana-context.json")
    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/teams/{team}/projects",
    )
    projects = response.get("data", [])
    if isinstance(projects, list):
        cache_records(
            cache,
            "projects",
            [project_cache_record(item, team_gid=team) for item in projects if isinstance(item, dict)],
        )
        save_cache(cache)
    workspace_gid = None
    team_record = find_cached_record(cache, "teams", team, fields=("workspace_gid",))
    if isinstance(team_record, dict) and team_record.get("workspace_gid"):
        workspace_gid = str(team_record["workspace_gid"])
    enriched = attach_skill_advertising(
        args=args,
        payload=response,
        token=token,
        workspace_gid=workspace_gid,
        cache=cache,
    )
    print_json(enriched, args.compact)
    return enriched


def command_users(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    workspace = workspace_default(args, context, cache)
    if not workspace:
        raise SystemExit("No workspace GID provided and no default workspace in asana-context.json")
    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/workspaces/{workspace}/users",
        query={"opt_fields": args.opt_fields or "gid,name,email"},
    )
    response = maybe_paginate(token, response, args.paginate, args.limit_pages)
    users = response.get("data", [])
    if isinstance(users, list):
        cache_records(cache, "users", [user_cache_record(item) for item in users if isinstance(item, dict)])
        save_cache(cache)
    enriched = attach_skill_advertising(
        args=args,
        payload=response,
        token=token,
        workspace_gid=workspace,
        cache=cache,
    )
    print_json(enriched, args.compact)
    return enriched


def command_task(args: argparse.Namespace) -> Any:
    token = get_token(args)
    task_gids = parse_gid_args(getattr(args, "task_gids", None) or getattr(args, "task_gid", None))
    opt_fields = (
        args.opt_fields
        or "gid,name,resource_subtype,completed,assignee.name,due_on,due_at,"
        "projects.name,memberships.section.name,parent.name,permalink_url,notes"
    )
    actions = [
        {
            "method": "get",
            "relative_path": f"/tasks/{task_gid}",
            "options": {"fields": field_list(opt_fields)},
        }
        for task_gid in task_gids
    ]
    items = batch_actions_request_chunked(token, actions)
    return render_many_results(
        "task",
        [
            {
                "requested_gid": task_gid,
                "status_code": item.get("status_code", 200),
                "result": item.get("body", {}),
            }
            for task_gid, item in zip(task_gids, items)
        ],
        args.compact,
    )


def command_story(args: argparse.Namespace) -> Any:
    token = get_token(args)
    story_gids = parse_gid_args(getattr(args, "story_gids", None) or getattr(args, "story_gid", None))
    opt_fields = args.opt_fields or story_detail_opt_fields()
    actions = [
        {
            "method": "get",
            "relative_path": f"/stories/{story_gid}",
            "options": {"fields": field_list(opt_fields)},
        }
        for story_gid in story_gids
    ]
    items = batch_actions_request_chunked(token, actions)
    return render_many_results(
        "story",
        [
            {
                "requested_gid": story_gid,
                "status_code": item.get("status_code", 200),
                "result": response_with_review_links(item.get("body", {})),
            }
            for story_gid, item in zip(story_gids, items)
        ],
        args.compact,
    )


def command_project(args: argparse.Namespace) -> Any:
    token = get_token(args)
    project_gids = parse_gid_args(getattr(args, "project_gids", None) or getattr(args, "project_gid", None))
    opt_fields = (
        args.opt_fields
        or "gid,name,team.name,owner.name,public,archived,current_status,"
        "default_view,created_at,permalink_url,notes"
    )
    cache = load_cache()
    actions = [
        {
            "method": "get",
            "relative_path": f"/projects/{project_gid}",
            "options": {"fields": field_list(opt_fields)},
        }
        for project_gid in project_gids
    ]
    items = batch_actions_request_chunked(token, actions)
    for item in items:
        project = (item.get("body") or {}).get("data", {})
        if isinstance(project, dict):
            cache_record(cache, "projects", project_cache_record(project))
    save_cache(cache)
    return render_many_results(
        "project",
        [
            {
                "requested_gid": project_gid,
                "status_code": item.get("status_code", 200),
                "result": item.get("body", {}),
            }
            for project_gid, item in zip(project_gids, items)
        ],
        args.compact,
    )


def command_project_tasks(args: argparse.Namespace) -> Any:
    token = get_token(args)
    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/projects/{args.project_gid}/tasks",
        query=build_task_list_query(args),
    )
    response = maybe_paginate(token, response, args.paginate, args.limit_pages)
    print_json(response, args.compact)
    return response


def command_sections(args: argparse.Namespace) -> Any:
    token = get_token(args)
    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/projects/{args.project_gid}/sections",
        query={"opt_fields": args.opt_fields or section_opt_fields()},
    )
    print_json(response, args.compact)
    return response


def command_section(args: argparse.Namespace) -> Any:
    token = get_token(args)
    section_gids = parse_gid_args(getattr(args, "section_gids", None) or getattr(args, "section_gid", None))
    opt_fields = args.opt_fields or section_opt_fields("gid,name,project.name")
    actions = [
        {
            "method": "get",
            "relative_path": f"/sections/{section_gid}",
            "options": {"fields": field_list(opt_fields)},
        }
        for section_gid in section_gids
    ]
    items = batch_actions_request_chunked(token, actions)
    return render_many_results(
        "section",
        [
            {
                "requested_gid": section_gid,
                "status_code": item.get("status_code", 200),
                "result": item.get("body", {}),
            }
            for section_gid, item in zip(section_gids, items)
        ],
        args.compact,
    )


def command_section_tasks(args: argparse.Namespace) -> Any:
    token = get_token(args)
    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/sections/{args.section_gid}/tasks",
        query=build_task_list_query(args),
    )
    response = maybe_paginate(token, response, args.paginate, args.limit_pages)
    print_json(response, args.compact)
    return response


def command_create_section(args: argparse.Namespace) -> Any:
    token = get_token(args)
    response = api_request(
        token=token,
        method="POST",
        path_or_url=f"/projects/{args.project_gid}/sections",
        json_body={"data": {"name": args.name}},
    )
    print_json(response, args.compact)
    return response


def command_update_section(args: argparse.Namespace) -> Any:
    token = get_token(args)
    response = api_request(
        token=token,
        method="PUT",
        path_or_url=f"/sections/{args.section_gid}",
        json_body={"data": {"name": args.name}},
    )
    print_json(response, args.compact)
    return response


def command_close_out_sections(args: argparse.Namespace) -> Any:
    token = get_token(args)
    sections_response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/projects/{args.project_gid}/sections",
        query={"opt_fields": section_opt_fields("gid,name,project.gid,project.name")},
    )
    project_sections = sections_response.get("data", [])
    if not isinstance(project_sections, list):
        raise SystemExit("Failed to load project sections.")

    source_sections: list[dict[str, Any]] = []
    seen_source_gids: set[str] = set()
    for identifier in args.section:
        section = find_section_record(project_sections, identifier)
        section_gid = str(section.get("gid") or "").strip()
        if not section_gid or section_gid in seen_source_gids:
            continue
        source_sections.append(section)
        seen_source_gids.add(section_gid)

    destination_section = None
    if args.move_to:
        destination_section = find_section_record(project_sections, args.move_to)
        destination_gid = str(destination_section.get("gid") or "").strip()
        if destination_gid in seen_source_gids:
            raise SystemExit("Destination section must be different from every source section.")

    project_name = ""
    if source_sections:
        project_name = str(((source_sections[0].get("project") or {}).get("name")) or "")

    section_results: list[dict[str, Any]] = []
    total_moved = 0
    total_deleted = 0
    for source_section in source_sections:
        source_gid = str(source_section.get("gid") or "").strip()
        source_name = str(source_section.get("name") or source_gid)
        tasks = fetch_all_section_tasks(
            token,
            source_gid,
            opt_fields=section_close_out_task_opt_fields(),
            limit_pages=args.limit_pages,
        )
        selected_tasks = select_section_tasks(tasks, args.completed_mode)
        selected_task_gids = {
            str(task.get("gid") or "").strip()
            for task in selected_tasks
            if str(task.get("gid") or "").strip()
        }
        remaining_tasks = [
            task
            for task in tasks
            if str(task.get("gid") or "").strip() not in selected_task_gids
        ]
        completed_count = sum(1 for task in tasks if bool(task.get("completed")))
        incomplete_count = len(tasks) - completed_count

        result: dict[str, Any] = {
            "source_section": {
                "gid": source_gid,
                "name": source_name,
            },
            "destination_section": (
                {
                    "gid": str(destination_section.get("gid") or ""),
                    "name": str(destination_section.get("name") or ""),
                }
                if destination_section
                else None
            ),
            "task_count": len(tasks),
            "completed_count": completed_count,
            "incomplete_count": incomplete_count,
            "selected_task_count": len(selected_tasks),
            "remaining_task_count_after_selected_moves": len(remaining_tasks),
            "completed_mode": args.completed_mode,
            "apply": bool(args.apply),
            "sample_tasks": [
                {
                    "gid": str(task.get("gid") or ""),
                    "name": task.get("name"),
                    "completed": bool(task.get("completed")),
                }
                for task in tasks[:5]
            ],
            "selected_sample_tasks": [
                {
                    "gid": str(task.get("gid") or ""),
                    "name": task.get("name"),
                    "completed": bool(task.get("completed")),
                }
                for task in selected_tasks[:5]
            ],
            "planned_actions": {
                "move_selected_tasks": bool(destination_section and selected_tasks),
                "delete_after_move": len(remaining_tasks) == 0,
            },
        }

        moved_tasks: list[dict[str, Any]] = []
        delete_attempted = False
        deleted = False
        blocked_reason = None

        if args.apply:
            if destination_section:
                destination_gid = str(destination_section.get("gid") or "").strip()
                for task in selected_tasks:
                    task_gid = str(task.get("gid") or "").strip()
                    if not task_gid:
                        continue
                    api_request(
                        token=token,
                        method="POST",
                        path_or_url=f"/tasks/{task_gid}/addProject",
                        json_body={
                            "data": {
                                "project": args.project_gid,
                                "section": destination_gid,
                            }
                        },
                    )
                    moved_tasks.append(
                        {
                            "gid": task_gid,
                            "name": task.get("name"),
                            "completed": bool(task.get("completed")),
                            "permalink_url": task.get("permalink_url"),
                        }
                    )
                total_moved += len(moved_tasks)

            remaining_probe = api_request(
                token=token,
                method="GET",
                path_or_url=f"/sections/{source_gid}/tasks",
                query={"opt_fields": "gid,name,completed", "limit": "1"},
            )
            remaining_data = remaining_probe.get("data", [])
            has_remaining_tasks = isinstance(remaining_data, list) and bool(remaining_data)
            delete_attempted = True
            if has_remaining_tasks:
                if not destination_section and tasks:
                    blocked_reason = (
                        "Section still has tasks and no destination section was provided."
                    )
                elif remaining_tasks:
                    blocked_reason = (
                        "Section still has tasks after moving the selected subset."
                    )
                else:
                    blocked_reason = "Section still has tasks after the move request."
            else:
                api_request(
                    token=token,
                    method="DELETE",
                    path_or_url=f"/sections/{source_gid}",
                )
                deleted = True
                total_deleted += 1

        result["applied_actions"] = {
            "moved_task_count": len(moved_tasks),
            "moved_tasks": moved_tasks,
            "delete_attempted": delete_attempted,
            "deleted": deleted,
            "blocked_reason": blocked_reason,
        }
        section_results.append(result)

    payload = {
        "project_gid": args.project_gid,
        "project_name": project_name,
        "section_count": len(section_results),
        "completed_mode": args.completed_mode,
        "apply": bool(args.apply),
        "moved_task_count": total_moved,
        "deleted_section_count": total_deleted,
        "sections": section_results,
    }
    print_json(payload, args.compact)
    return payload


def command_task_stories(args: argparse.Namespace) -> Any:
    token = get_token(args)
    task_gids = parse_gid_args(getattr(args, "task_gids", None) or getattr(args, "task_gid", None))
    opt_fields = args.opt_fields or story_opt_fields()
    if args.paginate:
        entries = []
        for task_gid in task_gids:
            response = api_request(
                token=token,
                method="GET",
                path_or_url=f"/tasks/{task_gid}/stories",
                query={"opt_fields": opt_fields},
            )
            response = maybe_paginate(token, response, True, args.limit_pages)
            entries.append({"requested_gid": task_gid, "status_code": 200, "result": response})
        return render_many_results("task-stories", entries, args.compact)

    actions = [
        {
            "method": "get",
            "relative_path": f"/tasks/{task_gid}/stories",
            "options": {"fields": field_list(opt_fields)},
        }
        for task_gid in task_gids
    ]
    items = batch_actions_request_chunked(token, actions)
    return render_many_results(
        "task-stories",
        [
            {
                "requested_gid": task_gid,
                "status_code": item.get("status_code", 200),
                "result": item.get("body", {}),
            }
            for task_gid, item in zip(task_gids, items)
        ],
        args.compact,
    )


def command_task_comments(args: argparse.Namespace) -> Any:
    token = get_token(args)
    task_gids = parse_gid_args(getattr(args, "task_gids", None) or getattr(args, "task_gid", None))
    opt_fields = args.opt_fields or story_opt_fields()
    entries = []
    if args.paginate:
        story_responses = []
        for task_gid in task_gids:
            response = api_request(
                token=token,
                method="GET",
                path_or_url=f"/tasks/{task_gid}/stories",
                query={"opt_fields": opt_fields},
            )
            story_responses.append(maybe_paginate(token, response, True, args.limit_pages))
    else:
        actions = [
            {
                "method": "get",
                "relative_path": f"/tasks/{task_gid}/stories",
                "options": {"fields": field_list(opt_fields)},
            }
            for task_gid in task_gids
        ]
        story_responses = [item.get("body", {}) for item in batch_actions_request_chunked(token, actions)]

    for task_gid, response in zip(task_gids, story_responses):
        comments = [
            story
            for story in response.get("data", [])
            if story.get("type") == "comment" or story.get("resource_subtype") == "comment_added"
        ]
        entries.append(
            {
                "requested_gid": task_gid,
                "status_code": 200,
                "result": {
                    "data": comments,
                    "comment_count": len(comments),
                    "task_gid": task_gid,
                },
            }
        )
    return render_many_results("task-comments", entries, args.compact)


def command_task_projects(args: argparse.Namespace) -> Any:
    token = get_token(args)
    task_gids = parse_gid_args(getattr(args, "task_gids", None) or getattr(args, "task_gid", None))
    opt_fields = args.opt_fields or "gid,name"
    actions = [
        {
            "method": "get",
            "relative_path": f"/tasks/{task_gid}/projects",
            "options": {"fields": field_list(opt_fields)},
        }
        for task_gid in task_gids
    ]
    items = batch_actions_request_chunked(token, actions)
    return render_many_results(
        "task-projects",
        [
            {
                "requested_gid": task_gid,
                "status_code": item.get("status_code", 200),
                "result": item.get("body", {}),
            }
            for task_gid, item in zip(task_gids, items)
        ],
        args.compact,
    )


def build_task_status_payload(
    task: dict[str, Any],
    *,
    project_filter: str | None,
    include_task_position: bool,
    section_cache: dict[str, tuple[list[dict[str, Any]], dict[str, int]]],
    task_positions_by_section: dict[str, dict[str, int]],
) -> dict[str, Any]:
    memberships = task.get("memberships") or []
    membership_summaries: list[dict[str, Any]] = []

    for membership in memberships:
        project = membership.get("project") or {}
        section = membership.get("section") or {}
        project_gid = project.get("gid")
        if project_filter and project_gid != project_filter:
            continue

        sections, order_map = section_cache.get(project_gid, ([], {})) if project_gid else ([], {})
        section_gid = section.get("gid")
        task_position = None
        if include_task_position and section_gid:
            task_position = (task_positions_by_section.get(section_gid) or {}).get(str(task.get("gid") or ""))

        membership_summaries.append(
            {
                "project_gid": project_gid,
                "project_name": project.get("name"),
                "section_gid": section_gid,
                "section_name": section.get("name"),
                "section_position": order_map.get(section_gid),
                "section_count": len(sections),
                "task_position_in_section": task_position,
            }
        )

    return {
        "task_gid": task.get("gid"),
        "name": task.get("name"),
        "completed": task.get("completed"),
        "completed_at": task.get("completed_at"),
        "permalink_url": task.get("permalink_url"),
        "assignee": task.get("assignee"),
        "due_on": task.get("due_on"),
        "due_at": task.get("due_at"),
        "custom_fields": task.get("custom_fields", []),
        "memberships": membership_summaries,
    }


def command_task_status(args: argparse.Namespace) -> Any:
    token = get_token(args)
    task_gids = parse_gid_args(getattr(args, "task_gids", None) or getattr(args, "task_gid", None))
    opt_fields = args.opt_fields or task_status_fields()
    project_filter = args.project

    actions = [
        {
            "method": "get",
            "relative_path": f"/tasks/{task_gid}",
            "options": {"fields": field_list(opt_fields)},
        }
        for task_gid in task_gids
    ]
    items = batch_actions_request_chunked(token, actions)
    tasks = [(item.get("body") or {}).get("data", {}) for item in items]

    project_gids: list[str] = []
    for task in tasks:
        for membership in task.get("memberships") or []:
            project = membership.get("project") or {}
            project_gid = str(project.get("gid") or "").strip()
            if not project_gid or (project_filter and project_gid != project_filter) or project_gid in project_gids:
                continue
            project_gids.append(project_gid)

    section_cache = {project_gid: section_order_map(token, project_gid) for project_gid in project_gids}

    task_positions_by_section: dict[str, dict[str, int]] = {}
    if args.include_task_position:
        section_gids: list[str] = []
        for task in tasks:
            for membership in task.get("memberships") or []:
                project = membership.get("project") or {}
                if project_filter and project.get("gid") != project_filter:
                    continue
                section_gid = str(((membership.get("section") or {}).get("gid")) or "").strip()
                if section_gid and section_gid not in section_gids:
                    section_gids.append(section_gid)
        task_positions_by_section = section_task_positions_map(token, section_gids)

    return render_many_results(
        "task-status",
        [
            {
                "requested_gid": task_gid,
                "status_code": item.get("status_code", 200),
                "result": build_task_status_payload(
                    task,
                    project_filter=project_filter,
                    include_task_position=bool(args.include_task_position),
                    section_cache=section_cache,
                    task_positions_by_section=task_positions_by_section,
                ),
            }
            for task_gid, item, task in zip(task_gids, items, tasks)
        ],
        args.compact,
    )


def compute_board_context(
    section_payloads: list[dict[str, Any]], now: datetime
) -> dict[str, Any]:
    """Compute workflow-relevant stats from board section/task data.

    Pure function: takes already-fetched section payloads (each with a
    ``tasks`` list) and returns aggregated stats the AI layer uses to
    recommend workflow optimisations.
    """

    all_tasks: list[dict[str, Any]] = []
    section_summaries: list[dict[str, Any]] = []
    for sp in section_payloads:
        tasks = sp.get("tasks", [])
        all_tasks.extend(tasks)
        completed = sum(1 for t in tasks if t.get("completed"))
        section_summaries.append({
            "name": sp.get("name"),
            "gid": sp.get("gid"),
            "total": len(tasks),
            "completed": completed,
            "incomplete": len(tasks) - completed,
        })

    total = len(all_tasks)
    completed_total = sum(1 for t in all_tasks if t.get("completed"))

    # Compute pct_of_project for each section
    for ss in section_summaries:
        ss["pct_of_project"] = round(ss["total"] / total * 100, 1) if total else 0.0

    # Custom field coverage
    field_counts: dict[str, dict[str, Any]] = {}
    for task in all_tasks:
        for cf in task.get("custom_fields", []):
            gid = cf.get("gid", "")
            if gid not in field_counts:
                field_counts[gid] = {
                    "name": cf.get("name"),
                    "gid": gid,
                    "type": cf.get("resource_subtype", cf.get("type", "")),
                    "filled_count": 0,
                    "total_applicable": 0,
                }
            field_counts[gid]["total_applicable"] += 1
            if cf.get("display_value"):
                field_counts[gid]["filled_count"] += 1

    field_coverage = []
    for fc in field_counts.values():
        ta = fc["total_applicable"]
        fc["coverage_pct"] = round(fc["filled_count"] / ta * 100, 1) if ta else 0.0
        field_coverage.append(fc)

    # Date coverage
    with_due = sum(1 for t in all_tasks if t.get("due_on") or t.get("due_at"))
    overdue = 0
    for t in all_tasks:
        if t.get("completed"):
            continue
        due = t.get("due_on") or (t.get("due_at") or "")[:10]
        if due:
            try:
                if datetime.strptime(due, "%Y-%m-%d").replace(tzinfo=timezone.utc) < now:
                    overdue += 1
            except ValueError:
                pass

    # Assignee distribution
    assigned = 0
    unassigned = 0
    by_assignee: dict[str, dict[str, Any]] = {}
    for t in all_tasks:
        assignee = t.get("assignee")
        if assignee and assignee.get("name"):
            assigned += 1
            key = assignee.get("gid", assignee["name"])
            if key not in by_assignee:
                by_assignee[key] = {"name": assignee["name"], "gid": assignee.get("gid", ""), "count": 0}
            by_assignee[key]["count"] += 1
        else:
            unassigned += 1

    # Staleness
    buckets = {"within_7d": 0, "8_to_14d": 0, "15_to_30d": 0, "over_30d": 0}
    oldest_stale: dict[str, Any] | None = None
    for t in all_tasks:
        mod = t.get("modified_at")
        if not mod:
            continue
        try:
            mod_dt = datetime.fromisoformat(mod.replace("Z", "+00:00"))
            days = (now - mod_dt).days
        except (ValueError, TypeError):
            continue
        if days <= 7:
            buckets["within_7d"] += 1
        elif days <= 14:
            buckets["8_to_14d"] += 1
        elif days <= 30:
            buckets["15_to_30d"] += 1
        else:
            buckets["over_30d"] += 1
            if oldest_stale is None or days > oldest_stale["days_stale"]:
                oldest_stale = {"name": t.get("name", ""), "gid": t.get("gid", ""), "days_stale": days}

    return {
        "project_summary": {
            "total_tasks": total,
            "completed_tasks": completed_total,
            "incomplete_tasks": total - completed_total,
            "sections": section_summaries,
        },
        "custom_field_coverage": {"fields": field_coverage},
        "date_coverage": {
            "with_due_date": with_due,
            "without_due_date": total - with_due,
            "coverage_pct": round(with_due / total * 100, 1) if total else 0.0,
            "overdue": overdue,
        },
        "assignee_distribution": {
            "assigned": assigned,
            "unassigned": unassigned,
            "by_assignee": sorted(by_assignee.values(), key=lambda x: x["count"], reverse=True),
        },
        "staleness": {
            "modified_within_7d": buckets["within_7d"],
            "modified_8_to_14d": buckets["8_to_14d"],
            "modified_15_to_30d": buckets["15_to_30d"],
            "modified_over_30d": buckets["over_30d"],
            "oldest_stale_task": oldest_stale,
        },
    }


def command_project_board(args: argparse.Namespace) -> Any:
    token = get_token(args)
    sections, order_map = section_order_map(token, args.project_gid)
    section_payloads: list[dict[str, Any]] = []

    context_mode = getattr(args, "context", False)
    default_fields = (
        "gid,name,completed,completed_at,assignee.gid,assignee.name,due_on,due_at,"
        "modified_at,custom_fields.gid,custom_fields.name,custom_fields.resource_subtype,"
        "custom_fields.display_value"
        if context_mode
        else "gid,name,completed,completed_at,assignee.name,due_on,due_at,"
        "custom_fields.name,custom_fields.display_value"
    )

    for section in sections:
        section_gid = section.get("gid")
        tasks_response = api_request(
            token=token,
            method="GET",
            path_or_url=f"/sections/{section_gid}/tasks",
            query={
                "opt_fields": args.opt_fields or default_fields
            },
        )
        tasks = []
        for index, task in enumerate(tasks_response.get("data", []), start=1):
            task_copy = dict(task)
            task_copy["task_position_in_section"] = index
            tasks.append(task_copy)

        section_name = section.get("name")
        section_payloads.append(
            {
                "gid": section_gid,
                "name": section_name,
                "section_position": order_map.get(section_gid),
                "task_count": len(tasks),
                "tasks": tasks,
            }
        )

    payload: dict[str, Any] = {
        "project_gid": args.project_gid,
        "sections": section_payloads,
    }

    if context_mode:
        now = datetime.now(timezone.utc)
        payload["context"] = compute_board_context(section_payloads, now)

    print_json(payload, args.compact)
    return payload


def command_trigger_rule(args: argparse.Namespace) -> Any:
    token = get_token(args)
    action_data: dict[str, str] = {}
    for pair in getattr(args, "action_data", None) or []:
        if "=" not in pair:
            raise SystemExit(f"Invalid --action-data format: {pair!r}  (expected key=value)")
        key, value = pair.split("=", 1)
        action_data[key] = value

    body: dict[str, Any] = {"data": {"resource": args.task_gid}}
    if action_data:
        body["data"]["action_data"] = action_data

    result = api_request(
        token=token,
        method="POST",
        path_or_url=f"/rule_triggers/{args.trigger_identifier}/run",
        json_body=body,
    )
    print_json(result, args.compact)
    return result


def build_task_bundle_payload(
    token: str,
    *,
    task_gid: str,
    project_gid: str | None,
    task_opt_fields: str | None,
    story_opt_fields_value: str | None,
    attachment_opt_fields: str | None,
) -> dict[str, Any]:
    actions = [
        {
            "method": "get",
            "relative_path": f"/tasks/{task_gid}",
            "options": {
                "fields": field_list(
                    task_opt_fields
                    or "gid,name,notes,html_notes,resource_subtype,completed,completed_at,assignee.gid,assignee.name,"
                    "due_on,due_at,permalink_url,parent.gid,parent.name,memberships.project.gid,memberships.project.name,"
                    "memberships.section.gid,memberships.section.name,custom_fields.gid,custom_fields.name,"
                    "custom_fields.resource_subtype,custom_fields.display_value,custom_fields.enum_value.name,"
                    "tags.gid,tags.name,tags.color"
                )
            },
        },
        {
            "method": "get",
            "relative_path": f"/tasks/{task_gid}/stories",
            "options": {"fields": field_list(story_opt_fields_value or story_opt_fields())},
        },
        {
            "method": "get",
            "relative_path": f"/tasks/{task_gid}/attachments",
            "options": {
                "fields": field_list(
                    attachment_opt_fields
                    or "gid,name,resource_subtype,download_url,permanent_url,view_url,host,parent_type,parent.name"
                )
            },
        },
    ]
    if project_gid:
        actions.append(
            {
                "method": "get",
                "relative_path": f"/projects/{project_gid}/sections",
                "options": {"fields": field_list(section_opt_fields())},
            }
        )
        actions.append(
            {
                "method": "get",
                "relative_path": f"/projects/{project_gid}/custom_field_settings",
                "options": {"fields": field_list(custom_field_setting_opt_fields())},
            }
        )

    batch_response = batch_actions_request(token, actions)
    task = batch_body_at(batch_response, 0).get("data", {})
    stories = batch_body_at(batch_response, 1).get("data", [])
    attachments = batch_body_at(batch_response, 2).get("data", [])
    sections = batch_body_at(batch_response, 3).get("data", []) if project_gid else []
    project_custom_field_settings = batch_body_at(batch_response, 4).get("data", []) if project_gid else []
    section_order = section_order_from_sections(sections)
    comments = comment_stories_only(stories)

    memberships = []
    for membership in task.get("memberships", []):
        project = membership.get("project") or {}
        if project_gid and project.get("gid") != project_gid:
            continue
        section = membership.get("section") or {}
        section_gid = section.get("gid")
        memberships.append(
            {
                "project_gid": project.get("gid"),
                "project_name": project.get("name"),
                "section_gid": section_gid,
                "section_name": section.get("name"),
                "section_position": section_order.get(section_gid),
            }
        )

    image_urls = []
    for attachment in attachments:
        for key in ("download_url", "view_url", "permanent_url"):
            value = attachment.get(key)
            if isinstance(value, str) and value not in image_urls:
                image_urls.append(value)
    for comment in comments:
        for url in extract_image_urls_from_html(comment.get("html_text")):
            if url not in image_urls:
                image_urls.append(url)
    if task.get("html_notes"):
        for url in extract_image_urls_from_html(task.get("html_notes")):
            if url not in image_urls:
                image_urls.append(url)

    payload = {
        "task": task,
        "workflow_context": {
            "project_gid": project_gid,
            "memberships": memberships,
            "section_order": [
                {"gid": section.get("gid"), "name": section.get("name"), "section_position": section_order.get(section.get("gid"))}
                for section in sections
            ],
        },
        "comments": {
            "data": comments,
            "comment_count": len(comments),
        },
        "attachments": attachments,
        "project_custom_field_settings": project_custom_field_settings,
        "image_urls": image_urls,
        "asana_permalink": task.get("permalink_url"),
    }
    return payload


def command_task_bundle(args: argparse.Namespace) -> Any:
    token = get_token(args)
    task_gids = parse_gid_args(getattr(args, "task_gids", None) or getattr(args, "task_gid", None))
    return render_many_results(
        "task-bundle",
        [
            {
                "requested_gid": task_gid,
                "status_code": 200,
                "result": build_task_bundle_payload(
                    token,
                    task_gid=task_gid,
                    project_gid=args.project_gid,
                    task_opt_fields=args.task_opt_fields,
                    story_opt_fields_value=args.story_opt_fields,
                    attachment_opt_fields=args.attachment_opt_fields,
                ),
            }
            for task_gid in task_gids
        ],
        args.compact,
    )


def command_add_task_project(args: argparse.Namespace) -> Any:
    task_position_args(args)
    payload: dict[str, Any] = {"project": args.project_gid}
    if args.section:
        payload["section"] = args.section
    if args.insert_before is not None:
        payload["insert_before"] = nullable_arg(args.insert_before)
    if args.insert_after is not None:
        payload["insert_after"] = nullable_arg(args.insert_after)
    return post_task_relationship(args, f"/tasks/{args.task_gid}/addProject", payload)


def command_remove_task_project(args: argparse.Namespace) -> Any:
    return post_task_relationship(
        args,
        f"/tasks/{args.task_gid}/removeProject",
        {"project": args.project_gid},
    )


def command_add_task_followers(args: argparse.Namespace) -> Any:
    context = load_context()
    cache = load_cache()
    return post_task_relationship(
        args,
        f"/tasks/{args.task_gid}/addFollowers",
        {"followers": resolve_many_user_identifiers(args.followers, context, cache)},
    )


def command_remove_task_followers(args: argparse.Namespace) -> Any:
    context = load_context()
    cache = load_cache()
    return post_task_relationship(
        args,
        f"/tasks/{args.task_gid}/removeFollowers",
        {"followers": resolve_many_user_identifiers(args.followers, context, cache)},
    )


def command_task_tags(args: argparse.Namespace) -> Any:
    token = get_token(args)
    cache = load_cache()
    task_gids = parse_gid_args(getattr(args, "task_gids", None) or getattr(args, "task_gid", None))
    opt_fields = args.opt_fields or tag_opt_fields("gid,name,color")
    actions = [
        {
            "method": "get",
            "relative_path": f"/tasks/{task_gid}/tags",
            "options": {"fields": field_list(opt_fields)},
        }
        for task_gid in task_gids
    ]
    items = batch_actions_request_chunked(token, actions)
    for item in items:
        body = item.get("body", {})
        tags = body.get("data", []) if isinstance(body, dict) else []
        if isinstance(tags, list):
            cache_records(cache, "tags", [tag_cache_record(tag) for tag in tags if isinstance(tag, dict)])
    save_cache(cache)
    return render_many_results(
        "task-tags",
        [
            {
                "requested_gid": task_gid,
                "status_code": item.get("status_code", 200),
                "result": item.get("body", {}),
            }
            for task_gid, item in zip(task_gids, items)
        ],
        args.compact,
    )


def command_tags(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    workspace = workspace_default(args, context, cache)
    if not workspace:
        raise SystemExit("No workspace GID provided and no default workspace in asana-context.json")
    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/workspaces/{workspace}/tags",
        query={"opt_fields": args.opt_fields or tag_opt_fields("gid,name,color")},
    )
    response = maybe_paginate(token, response, args.paginate, args.limit_pages)
    tags = response.get("data", [])
    if isinstance(tags, list):
        cache_records(cache, "tags", [tag_cache_record(item, workspace_gid=workspace) for item in tags if isinstance(item, dict)])
        save_cache(cache)
    print_json(response, args.compact)
    return response


def command_workspace_custom_fields(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    workspace = workspace_default(args, context, cache)
    if not workspace:
        raise SystemExit("No workspace GID provided and no default workspace in asana-context.json")
    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/workspaces/{workspace}/custom_fields",
        query={"opt_fields": args.opt_fields or "gid,name,resource_subtype,enum_options.name,enabled"},
    )
    response = maybe_paginate(token, response, args.paginate, args.limit_pages)
    print_json(response, args.compact)
    return response


def command_team_custom_fields(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    team = team_default(args, context, cache)
    if not team:
        raise SystemExit("No team GID provided and no default team in asana-context.json")
    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/teams/{team}/custom_field_settings",
        query={"opt_fields": args.opt_fields or custom_field_setting_opt_fields()},
    )
    response = maybe_paginate(token, response, args.paginate, args.limit_pages)
    print_json(response, args.compact)
    return response


def command_project_custom_fields(args: argparse.Namespace) -> Any:
    token = get_token(args)
    project_gids = parse_gid_args(getattr(args, "project_gids", None) or getattr(args, "project_gid", None))
    opt_fields = args.opt_fields or custom_field_setting_opt_fields()
    actions = [
        {
            "method": "get",
            "relative_path": f"/projects/{project_gid}/custom_field_settings",
            "options": {"fields": field_list(opt_fields)},
        }
        for project_gid in project_gids
    ]
    items = batch_actions_request_chunked(token, actions)
    return render_many_results(
        "project-custom-fields",
        [
            {
                "requested_gid": project_gid,
                "status_code": item.get("status_code", 200),
                "result": item.get("body", {}),
            }
            for project_gid, item in zip(project_gids, items)
        ],
        args.compact,
    )


def command_task_custom_fields(args: argparse.Namespace) -> Any:
    token = get_token(args)
    task_gids = parse_gid_args(getattr(args, "task_gids", None) or getattr(args, "task_gid", None))
    opt_fields = (
        args.opt_fields
        or "gid,name,custom_fields.gid,custom_fields.name,custom_fields.resource_subtype,"
        "custom_fields.display_value,custom_fields.enum_value.name"
    )
    actions = [
        {
            "method": "get",
            "relative_path": f"/tasks/{task_gid}",
            "options": {"fields": field_list(opt_fields)},
        }
        for task_gid in task_gids
    ]
    items = batch_actions_request_chunked(token, actions)
    entries = []
    for task_gid, item in zip(task_gids, items):
        task = (item.get("body") or {}).get("data", {})
        entries.append(
            {
                "requested_gid": task_gid,
                "status_code": item.get("status_code", 200),
                "result": {
                    "task_gid": task.get("gid"),
                    "name": task.get("name"),
                    "custom_fields": task.get("custom_fields", []),
                },
            }
        )
    return render_many_results("task-custom-fields", entries, args.compact)


def command_create_custom_field(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    workspace = workspace_default(args, context, cache)
    if not workspace:
        raise SystemExit("No workspace GID provided and no default workspace in asana-context.json")
    payload: dict[str, Any] = {
        "workspace": workspace,
        "name": args.name,
        "resource_subtype": args.resource_subtype,
    }
    if args.description:
        payload["description"] = args.description
    if args.precision is not None:
        payload["precision"] = args.precision
    if args.enum_option:
        payload["enum_options"] = [{"name": name} for name in args.enum_option]
    response = api_request(
        token=token,
        method="POST",
        path_or_url="/custom_fields",
        json_body={"data": payload},
    )
    print_json(response, args.compact)
    return response


def command_batch(args: argparse.Namespace) -> Any:
    token = get_token(args)
    if bool(args.actions) == bool(args.actions_file):
        raise SystemExit("Pass exactly one of --actions or --actions-file")
    actions_payload = args.actions
    if args.actions_file:
        actions_payload = Path(args.actions_file).read_text()
    actions = json.loads(actions_payload)
    if not isinstance(actions, list):
        raise SystemExit("Batch actions must be a JSON array")
    response = api_request(
        token=token,
        method="POST",
        path_or_url="/batch",
        json_body={"data": {"actions": actions}},
    )
    print_json(response, args.compact)
    return response


def command_create_tag(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    workspace = workspace_default(args, context, cache)
    if not workspace:
        raise SystemExit("No workspace GID provided and no default workspace in asana-context.json")
    payload: dict[str, Any] = {"workspace": workspace, "name": args.name}
    if args.color:
        payload["color"] = args.color
    if args.notes:
        payload["notes"] = args.notes
    response = api_request(
        token=token,
        method="POST",
        path_or_url="/tags",
        json_body={"data": payload},
    )
    tag = response.get("data", {})
    if isinstance(tag, dict):
        cache_record(cache, "tags", tag_cache_record(tag, workspace_gid=workspace))
        save_cache(cache)
    print_json(response, args.compact)
    return response


def command_add_task_tag(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    workspace = workspace_default(args, context, cache)
    tag = resolve_tag_identifier(
        args.tag_gid,
        token=token,
        cache=cache,
        workspace_gid=workspace,
    )
    return post_task_relationship(
        args,
        f"/tasks/{args.task_gid}/addTag",
        {"tag": str(tag.get("gid"))},
    )


def command_remove_task_tag(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    workspace = workspace_default(args, context, cache)
    tag = resolve_tag_identifier(
        args.tag_gid,
        token=token,
        cache=cache,
        workspace_gid=workspace,
    )
    return post_task_relationship(
        args,
        f"/tasks/{args.task_gid}/removeTag",
        {"tag": str(tag.get("gid"))},
    )


def command_set_task_sorting_tag(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    workspace = workspace_default(args, context, cache)
    sorting_labels = normalize_sorting_tag_labels(args.sorting_label)
    sorter_records = [
        resolve_tag_identifier(label, token=token, cache=cache, workspace_gid=workspace)
        for label in sorting_labels
    ]
    sorter_by_gid = {
        str(record.get("gid")): record
        for record in sorter_records
        if str(record.get("gid") or "").strip()
    }
    desired_tag = resolve_tag_identifier(
        args.sorting_tag,
        token=token,
        cache=cache,
        workspace_gid=workspace,
    )
    desired_tag_gid = str(desired_tag.get("gid") or "").strip()
    if desired_tag_gid not in sorter_by_gid:
        allowed_names = ", ".join(record.get("name", record.get("gid")) for record in sorter_by_gid.values())
        raise SystemExit(
            f"Sorting tag '{args.sorting_tag}' is not in the allowed sorter set: {allowed_names}"
        )

    current_tags = get_task_tags(token, args.task_gid)
    current_by_gid = {
        str(tag.get("gid")): tag
        for tag in current_tags
        if isinstance(tag, dict) and str(tag.get("gid") or "").strip()
    }
    current_sorter_tags = [
        tag
        for tag in current_tags
        if isinstance(tag, dict) and str(tag.get("gid") or "").strip() in sorter_by_gid
    ]
    removed_sorting_tags = [
        tag
        for tag in current_sorter_tags
        if str(tag.get("gid") or "").strip() != desired_tag_gid
    ]
    add_needed = desired_tag_gid not in current_by_gid

    if removed_sorting_tags or add_needed:
        for tag in removed_sorting_tags:
            api_request(
                token=token,
                method="POST",
                path_or_url=f"/tasks/{args.task_gid}/removeTag",
                json_body={"data": {"tag": str(tag.get("gid"))}},
            )
        if add_needed:
            api_request(
                token=token,
                method="POST",
                path_or_url=f"/tasks/{args.task_gid}/addTag",
                json_body={"data": {"tag": desired_tag_gid}},
            )

    resulting_tags: list[dict[str, Any]] = []
    desired_tag_present = False
    for tag in current_tags:
        if not isinstance(tag, dict):
            continue
        tag_gid = str(tag.get("gid") or "").strip()
        if not tag_gid:
            continue
        if tag_gid == desired_tag_gid:
            desired_tag_present = True
            resulting_tags.append(tag)
            continue
        if tag_gid in sorter_by_gid:
            continue
        resulting_tags.append(tag)
    if not desired_tag_present:
        resulting_tags.append(
            current_by_gid.get(desired_tag_gid)
            or {"gid": desired_tag_gid, "name": desired_tag.get("name"), "color": desired_tag.get("color")}
        )

    payload = {
        "command": "set-task-sorting-tag",
        "task_gid": args.task_gid,
        "sorting_tag": {
            "gid": desired_tag_gid,
            "name": desired_tag.get("name"),
            "color": desired_tag.get("color"),
        },
        "status": "updated" if removed_sorting_tags or add_needed else "unchanged",
        "added_sorting_tag": add_needed,
        "removed_sorting_tags": [
            {"gid": tag.get("gid"), "name": tag.get("name"), "color": tag.get("color")}
            for tag in removed_sorting_tags
        ],
        "preserved_tags": [
            {"gid": tag.get("gid"), "name": tag.get("name"), "color": tag.get("color")}
            for tag in current_tags
            if isinstance(tag, dict) and str(tag.get("gid") or "").strip() not in sorter_by_gid
        ],
        "resulting_tags": [
            {"gid": tag.get("gid"), "name": tag.get("name"), "color": tag.get("color")}
            for tag in resulting_tags
        ],
    }
    print_json(payload, args.compact)
    return payload


def command_add_task_dependencies(args: argparse.Namespace) -> Any:
    return post_task_relationship(
        args,
        f"/tasks/{args.task_gid}/addDependencies",
        {"dependencies": parse_many_gid(args.dependencies)},
    )


def command_remove_task_dependencies(args: argparse.Namespace) -> Any:
    return post_task_relationship(
        args,
        f"/tasks/{args.task_gid}/removeDependencies",
        {"dependencies": parse_many_gid(args.dependencies)},
    )


def command_search_tasks(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    workspace = workspace_default(args, context, cache)
    if not workspace:
        raise SystemExit("No workspace GID provided and no default workspace in asana-context.json")

    query: dict[str, str] = {
        "text": args.text,
        "opt_fields": args.opt_fields
        or "gid,name,completed,assignee.name,due_on,projects.name,permalink_url",
    }
    optional_values = {
        "projects.any": args.project,
        "assignee.any": resolve_user_identifier(args.assignee, context, cache),
        "completed": str(args.completed).lower() if args.completed is not None else None,
    }
    for key, value in optional_values.items():
        if value is not None:
            query[key] = value

    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/workspaces/{workspace}/tasks/search",
        query=query,
    )
    response = maybe_paginate(token, response, args.paginate, args.limit_pages)
    tasks = response.get("data", [])
    if isinstance(tasks, list):
        assignees = [
            user_cache_record(task["assignee"])
            for task in tasks
            if isinstance(task, dict) and isinstance(task.get("assignee"), dict)
        ]
        if assignees:
            cache_records(cache, "users", assignees)
            save_cache(cache)
    print_json(response, args.compact)
    return response


def fetch_parent_task_context_map(token: str, parent_gids: list[str]) -> dict[str, dict[str, Any]]:
    unique_parent_gids: list[str] = []
    seen: set[str] = set()
    for parent_gid in parent_gids:
        if parent_gid and parent_gid not in seen:
            seen.add(parent_gid)
            unique_parent_gids.append(parent_gid)

    parent_context_map: dict[str, dict[str, Any]] = {}
    for start in range(0, len(unique_parent_gids), 10):
        chunk = unique_parent_gids[start : start + 10]
        actions = [
            {
                "method": "get",
                "relative_path": f"/tasks/{parent_gid}",
                "options": {"fields": field_list(parent_task_context_opt_fields())},
            }
            for parent_gid in chunk
        ]
        batch_response = batch_actions_request(token, actions)
        for index, parent_gid in enumerate(chunk):
            parent = batch_body_at(batch_response, index).get("data", {})
            if parent:
                parent_context_map[parent_gid] = parent
    return parent_context_map


def fetch_task_activity_context_map(
    token: str,
    task_gids: list[str],
    *,
    include_comments: bool,
    comment_limit: int,
    include_attachments: bool,
) -> dict[str, dict[str, Any]]:
    activity_map: dict[str, dict[str, Any]] = {
        task_gid: {}
        for task_gid in task_gids
        if task_gid
    }
    if not include_comments and not include_attachments:
        return activity_map

    actions: list[tuple[str, str, dict[str, Any]]] = []
    for task_gid in task_gids:
        if not task_gid:
            continue
        if include_comments:
            actions.append(
                (
                    task_gid,
                    "comments",
                    {
                        "method": "get",
                        "relative_path": f"/tasks/{task_gid}/stories",
                        "options": {"fields": field_list(story_opt_fields())},
                    },
                )
            )
        if include_attachments:
            actions.append(
                (
                    task_gid,
                    "attachments",
                    {
                        "method": "get",
                        "relative_path": f"/tasks/{task_gid}/attachments",
                        "options": {"fields": field_list(attachment_opt_fields())},
                    },
                )
            )

    max_batch_actions = 10
    for start in range(0, len(actions), max_batch_actions):
        chunk = actions[start : start + max_batch_actions]
        batch_response = batch_actions_request(
            token,
            [action for _, _, action in chunk],
        )
        for index, (task_gid, kind, _) in enumerate(chunk):
            body = batch_body_at(batch_response, index).get("data", [])
            if not isinstance(body, list):
                continue
            task_activity = activity_map.setdefault(task_gid, {})
            if kind == "comments":
                comments = recent_comment_stories(body, comment_limit)
                task_activity["recent_comments"] = comments
                task_activity["comment_count"] = len(comment_stories_only(body))
                existing_image_urls = task_activity.setdefault("image_urls", [])
                for comment in comments:
                    for url in extract_image_urls_from_html(comment.get("html_text")):
                        if url not in existing_image_urls:
                            existing_image_urls.append(url)
            elif kind == "attachments":
                task_activity["attachments"] = body
                task_activity["attachment_count"] = len(body)
                existing_image_urls = task_activity.setdefault("image_urls", [])
                for attachment in body:
                    for key in ("download_url", "view_url", "permanent_url"):
                        value = attachment.get(key) if isinstance(attachment, dict) else None
                        if isinstance(value, str) and value not in existing_image_urls:
                            existing_image_urls.append(value)

    return activity_map


def command_project_assigned_tasks(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    workspace = workspace_default(args, context, cache)
    if not workspace:
        raise SystemExit("No workspace GID provided and no default workspace in asana-context.json")

    assignee = resolve_user_identifier(args.assignee or context.get("user_gid") or "me", context, cache)

    query: dict[str, str] = {
        "projects.any": args.project_gid,
        "assignee.any": assignee,
        "opt_fields": args.opt_fields or project_assigned_task_opt_fields(),
    }
    if args.completed is not None:
        query["completed"] = str(args.completed).lower()

    response = api_request(
        token=token,
        method="GET",
        path_or_url=f"/workspaces/{workspace}/tasks/search",
        query=query,
    )
    response = maybe_paginate(token, response, args.paginate, args.limit_pages)

    tasks = response.get("data", [])
    if not isinstance(tasks, list):
        print_json(response, args.compact)
        return response

    parent_context_map = fetch_parent_task_context_map(
        token,
        [
            str(task.get("parent", {}).get("gid"))
            for task in tasks
            if isinstance(task, dict) and isinstance(task.get("parent"), dict) and task["parent"].get("gid")
        ],
    )
    sections, section_order = section_order_map(token, args.project_gid)
    activity_context_map = fetch_task_activity_context_map(
        token,
        [
            str(task.get("gid"))
            for task in tasks
            if isinstance(task, dict) and task.get("gid")
        ],
        include_comments=args.include_comments,
        comment_limit=args.comment_limit,
        include_attachments=args.include_attachments,
    )

    section_positions: dict[str, dict[str, int]] = {}
    if args.include_task_position:
        relevant_section_gids: list[str] = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            parent = task.get("parent")
            parent_gid = parent.get("gid") if isinstance(parent, dict) else None
            parent_context = parent_context_map.get(str(parent_gid)) if parent_gid else None
            memberships = task.get("memberships")
            effective_memberships = memberships if memberships else parent_context.get("memberships", []) if parent_context else []
            for membership in effective_memberships:
                if not isinstance(membership, dict):
                    continue
                section = membership.get("section") or {}
                section_gid = section.get("gid")
                if section_gid:
                    relevant_section_gids.append(str(section_gid))
        section_positions = section_task_positions_map(token, relevant_section_gids)

    enriched_tasks: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict):
            enriched_tasks.append(task)
            continue

        task_payload = dict(task)
        parent = task_payload.get("parent")
        parent_gid = parent.get("gid") if isinstance(parent, dict) else None
        parent_context = parent_context_map.get(str(parent_gid)) if parent_gid else None
        memberships = task_payload.get("memberships")
        effective_memberships = memberships if memberships else parent_context.get("memberships", []) if parent_context else []
        effective_membership_summaries: list[dict[str, Any]] = []
        task_gid = str(task_payload.get("gid") or "")
        for membership in effective_memberships:
            if not isinstance(membership, dict):
                continue
            project = membership.get("project") or {}
            section = membership.get("section") or {}
            section_gid = str(section.get("gid") or "")
            effective_membership_summaries.append(
                {
                    "project_gid": project.get("gid"),
                    "project_name": project.get("name"),
                    "section_gid": section.get("gid"),
                    "section_name": section.get("name"),
                    "section_position": section_order.get(section_gid) if section_gid else None,
                    "task_position_in_section": section_positions.get(section_gid, {}).get(task_gid)
                    if section_gid
                    else None,
                }
            )

        task_payload["is_subtask"] = bool(parent_gid)
        task_payload["effective_memberships"] = effective_memberships
        task_payload["effective_membership_summaries"] = effective_membership_summaries
        if parent_context:
            task_payload["parent_context"] = parent_context
        activity_context = activity_context_map.get(task_gid)
        if activity_context:
            task_payload.update(activity_context)

        enriched_tasks.append(task_payload)

    payload = dict(response)
    payload["data"] = enriched_tasks
    payload["project_gid"] = args.project_gid
    payload["workspace_gid"] = workspace
    payload["assignee"] = assignee
    payload["includes_subtasks"] = True
    payload["workflow_context"] = {
        "section_order": [
            {
                "gid": section.get("gid"),
                "name": section.get("name"),
                "section_position": section_order.get(section.get("gid")),
            }
            for section in sections
        ],
    }
    payload["context_includes"] = {
        "section_order": True,
        "task_position_in_section": bool(args.include_task_position),
        "recent_comments": bool(args.include_comments),
        "attachments": bool(args.include_attachments),
    }
    payload["subtask_count"] = sum(1 for task in enriched_tasks if isinstance(task, dict) and task.get("is_subtask"))
    assignees = [
        user_cache_record(task["assignee"])
        for task in enriched_tasks
        if isinstance(task, dict) and isinstance(task.get("assignee"), dict)
    ]
    if assignees:
        cache_records(cache, "users", assignees)
        save_cache(cache)
    print_json(payload, args.compact)
    return payload


def build_task_payload(
    args: argparse.Namespace,
    context: dict[str, Any],
    cache: dict[str, Any],
    is_create: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for attr in ("name", "notes", "due_on", "due_at"):
        value = getattr(args, attr, None)
        if value:
            payload[attr] = value

    html_notes = getattr(args, "html_notes", None)
    if html_notes:
        payload["html_notes"] = normalize_ai_authored_rich_text(html_notes) or ""

    assignee = resolve_user_identifier(getattr(args, "assignee", None), context, cache)
    if assignee:
        payload["assignee"] = assignee

    if getattr(args, "completed", None) is not None:
        payload["completed"] = args.completed

    project = getattr(args, "project", None)
    workspace = workspace_default(args, context, cache)
    parent = getattr(args, "parent", None)

    if is_create and project:
        payload["projects"] = [project]
    elif is_create and workspace:
        payload["workspace"] = workspace

    if parent:
        payload["parent"] = parent

    if args.custom_field:
        payload["custom_fields"] = parse_custom_fields(args.custom_field)

    return payload


def task_placement_requested(args: argparse.Namespace, *, project_only: bool) -> bool:
    insert_before, insert_after = task_position_args(args)
    return bool(
        getattr(args, "section", None)
        or insert_before is not None
        or insert_after is not None
        or (project_only and getattr(args, "project", None))
    )


def validate_position_anchor(
    token: str,
    *,
    project_gid: str | None,
    section_gid: str | None,
    anchor_gid: str | None,
) -> None:
    if anchor_gid is None:
        return

    sentinel_gids = {gid for gid in (project_gid, section_gid) if gid}
    if anchor_gid in sentinel_gids:
        return

    if section_gid:
        path = f"/sections/{section_gid}/tasks"
        scope_label = f"section {section_gid}"
    elif project_gid:
        path = f"/projects/{project_gid}/tasks"
        scope_label = f"project {project_gid}"
    else:
        raise SystemExit("Pass --project or --section when using --insert-before or --insert-after.")

    response = api_request(
        token=token,
        method="GET",
        path_or_url=path,
        query={"limit": "100", "opt_fields": "gid"},
    )
    response = maybe_paginate(token, response, True, 0)
    tasks = response.get("data", [])
    if not any(isinstance(task, dict) and task.get("gid") == anchor_gid for task in tasks):
        raise SystemExit(f"Position anchor task {anchor_gid} is not in {scope_label}.")


def apply_task_placement(
    token: str,
    args: argparse.Namespace,
    *,
    task_gid: str,
    project_only: bool,
) -> Any | None:
    insert_before_arg, insert_after_arg = task_position_args(args)
    project_gid = getattr(args, "project", None)
    section_gid = getattr(args, "section", None)
    if not task_placement_requested(args, project_only=project_only):
        return None

    insert_before = nullable_arg(insert_before_arg) if insert_before_arg is not None else None
    insert_after = nullable_arg(insert_after_arg) if insert_after_arg is not None else None
    anchor_gid = insert_before if insert_before_arg is not None else insert_after
    validate_position_anchor(token, project_gid=project_gid, section_gid=section_gid, anchor_gid=anchor_gid)

    if section_gid:
        path = f"/sections/{section_gid}/addTask"
        payload: dict[str, Any] = {"task": task_gid}
    elif project_gid:
        path = f"/tasks/{task_gid}/addProject"
        payload = {"project": project_gid}
    else:
        raise SystemExit("Pass --project or --section when using task placement options.")

    if insert_before_arg is not None:
        payload["insert_before"] = insert_before
    if insert_after_arg is not None:
        payload["insert_after"] = insert_after

    return api_request(
        token=token,
        method="POST",
        path_or_url=path,
        json_body={"data": payload},
    )


def parse_custom_fields(items: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Expected custom field assignment <gid>=<value>, got: {item}")
        gid, value = item.split("=", 1)
        parsed[gid] = value
    return parsed


def command_create_task(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    task_position_args(args)
    payload = build_task_payload(args, context, cache, is_create=True)
    response = api_request(
        token=token,
        method="POST",
        path_or_url="/tasks",
        query={"opt_fields": task_write_opt_fields()},
        json_body={"data": payload},
    )
    task = response.get("data", {})
    if isinstance(task, dict) and isinstance(task.get("assignee"), dict):
        cache_record(cache, "users", user_cache_record(task["assignee"]))
        save_cache(cache)
    if isinstance(task, dict) and task.get("gid"):
        placement_response = apply_task_placement(token, args, task_gid=str(task["gid"]), project_only=False)
    else:
        placement_response = None
    enriched = response_with_review_links(response)
    if placement_response is not None and isinstance(enriched, dict):
        enriched["placement"] = placement_response
    print_json(enriched, args.compact)
    return enriched


def command_update_task(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    task_position_args(args)
    payload = build_task_payload(args, context, cache, is_create=False)
    placement_requested = task_placement_requested(args, project_only=True)
    if payload:
        response = api_request(
            token=token,
            method="PUT",
            path_or_url=f"/tasks/{args.task_gid}",
            query={"opt_fields": task_write_opt_fields()},
            json_body={"data": payload},
        )
    elif placement_requested:
        response = api_request(
            token=token,
            method="GET",
            path_or_url=f"/tasks/{args.task_gid}",
            query={"opt_fields": task_write_opt_fields()},
        )
    else:
        response = api_request(
            token=token,
            method="PUT",
            path_or_url=f"/tasks/{args.task_gid}",
            query={"opt_fields": task_write_opt_fields()},
            json_body={"data": payload},
        )
    task = response.get("data", {})
    if isinstance(task, dict) and isinstance(task.get("assignee"), dict):
        cache_record(cache, "users", user_cache_record(task["assignee"]))
        save_cache(cache)
    placement_response = (
        apply_task_placement(token, args, task_gid=args.task_gid, project_only=True) if placement_requested else None
    )
    enriched = response_with_review_links(response)
    if placement_response is not None and isinstance(enriched, dict):
        enriched["placement"] = placement_response
    print_json(enriched, args.compact)
    return enriched


def command_comment_task(args: argparse.Namespace) -> Any:
    token = get_token(args)
    payload = comment_payload_from_args(args)
    response = api_request(
        token=token,
        method="POST",
        path_or_url=f"/tasks/{args.task_gid}/stories",
        query={"opt_fields": story_write_opt_fields()},
        json_body={"data": payload},
    )
    enriched = response_with_review_links(response)
    print_json(enriched, args.compact)
    return enriched


def command_update_story(args: argparse.Namespace) -> Any:
    token = get_token(args)
    payload = comment_payload_from_args(args)
    response = api_request(
        token=token,
        method="PUT",
        path_or_url=f"/stories/{args.story_gid}",
        query={"opt_fields": story_write_opt_fields()},
        json_body={"data": payload},
    )
    enriched = response_with_review_links(response)
    print_json(enriched, args.compact)
    return enriched


def command_create_project(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    team = team_default(args, context, cache)
    payload = {"name": args.name}
    if team:
        payload["team"] = team
    elif workspace_default(args, context, cache):
        payload["workspace"] = workspace_default(args, context, cache)
    if args.notes:
        payload["notes"] = args.notes

    response = api_request(
        token=token,
        method="POST",
        path_or_url="/projects",
        json_body={"data": payload},
    )
    project = response.get("data", {})
    if isinstance(project, dict):
        cache_record(cache, "projects", project_cache_record(project, team_gid=team))
        save_cache(cache)
    print_json(response, args.compact)
    return response


def command_update_project(args: argparse.Namespace) -> Any:
    token = get_token(args)
    cache = load_cache()
    payload = {"name": args.name} if args.name else {}
    if args.notes:
        payload["notes"] = args.notes
    if args.archived is not None:
        payload["archived"] = args.archived
    response = api_request(
        token=token,
        method="PUT",
        path_or_url=f"/projects/{args.project_gid}",
        json_body={"data": payload},
    )
    project = response.get("data", {})
    if isinstance(project, dict):
        cache_record(cache, "projects", project_cache_record(project))
        save_cache(cache)
    print_json(response, args.compact)
    return response


def command_show_context(args: argparse.Namespace) -> Any:
    response = load_context()
    token = get_token(args)
    cache = load_cache()
    workspace_gid = str(response.get("workspace_gid") or "") or infer_workspace_gid_from_payload({"data": response}, cache)
    enriched = attach_skill_advertising(
        args=args,
        payload=response if isinstance(response, dict) else {"data": response},
        token=token,
        workspace_gid=workspace_gid or None,
        cache=cache,
    )
    print_json(enriched, args.compact)
    return enriched


def command_show_cache(args: argparse.Namespace) -> Any:
    response = load_cache()
    token = get_token(args)
    workspace_gid = infer_workspace_gid_from_payload(response, response)
    enriched = attach_skill_advertising(
        args=args,
        payload=response,
        token=token,
        workspace_gid=workspace_gid,
        cache=response,
    )
    print_json(enriched, args.compact)
    return enriched


def build_inbox_cleanup_review_payload(args: argparse.Namespace) -> Any:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    workspace = workspace_default(args, context, cache)
    if not workspace:
        raise SystemExit("No workspace GID provided and no default workspace in asana-context.json")

    user_task_list = my_tasks_project(token, workspace)
    user_task_list_gid = str(user_task_list.get("gid") or "").strip()
    if not user_task_list_gid:
        raise SystemExit("Unable to resolve My Tasks for the selected workspace")

    all_tasks = my_tasks_tasks(
        token,
        user_task_list_gid,
        paginate=not args.no_paginate,
        limit_pages=args.limit_pages,
    )
    source_section_names = [name.strip() for name in args.source_section if name.strip()]
    review_section_names = set(INBOX_CLEANUP_REVIEW_SECTIONS.values())

    filtered_tasks: list[dict[str, Any]] = []
    skipped_tasks: list[dict[str, Any]] = []
    for task in all_tasks:
        if not isinstance(task, dict):
            continue
        assignee_section = task.get("assignee_section") or {}
        current_section_name = str(assignee_section.get("name") or "").strip()
        if current_section_name in review_section_names and not args.all_open:
            skipped_tasks.append(
                {
                    "task_gid": task.get("gid"),
                    "name": task.get("name"),
                    "current_section": current_section_name,
                    "reason": "already_in_review_section",
                }
            )
            continue

        if not args.all_open and current_section_name not in source_section_names:
            skipped_tasks.append(
                {
                    "task_gid": task.get("gid"),
                    "name": task.get("name"),
                    "current_section": current_section_name,
                    "reason": "outside_source_sections",
                }
            )
            continue
        filtered_tasks.append(task)

    if args.max_tasks > 0:
        filtered_tasks = filtered_tasks[: args.max_tasks]

    task_contexts = fetch_task_review_context(
        token,
        [str(task.get("gid")) for task in filtered_tasks if task.get("gid")],
    )
    existing_sections = my_tasks_sections(token, user_task_list_gid)
    sections_by_name = ensure_review_sections(
        token,
        user_task_list_gid=user_task_list_gid,
        existing_sections=existing_sections,
        apply=args.apply,
    )
    created_section_names = [
        name
        for name in INBOX_CLEANUP_REVIEW_SECTIONS.values()
        if name not in {str(section.get("name")) for section in existing_sections if isinstance(section, dict)}
        and name in sections_by_name
    ]

    task_results: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {
        key: 0 for key in INBOX_CLEANUP_REVIEW_SECTIONS
    }
    now = datetime.now(timezone.utc)

    for task in filtered_tasks:
        task_gid = str(task.get("gid") or "").strip()
        if not task_gid:
            continue
        task_context = task_contexts.get(task_gid) or {"task": task, "stories": []}
        classification = classify_inbox_cleanup_task(task_context, now)
        category_key = classification["category_key"]
        category_counts[category_key] += 1
        target_section_name = classification["category_label"]
        target_section = sections_by_name.get(target_section_name) or {}
        target_section_gid = str(target_section.get("gid") or "").strip()
        current_section_name = classification["current_my_tasks_section"]

        move_result: dict[str, Any] | None = None
        comment_result: dict[str, Any] | None = None
        comment_was_posted = False

        if args.apply and target_section_gid and current_section_name != target_section_name:
            move_response = api_request(
                token=token,
                method="PUT",
                path_or_url=f"/tasks/{task_gid}",
                query={"opt_fields": "gid,name,assignee_section.gid,assignee_section.name"},
                json_body={"data": {"assignee_section": target_section_gid}},
            )
            move_result = move_response.get("data", {})

        if (
            args.apply
            and not args.skip_ready_comments
            and category_key == "ready_to_close"
            and not comment_already_mentions_inbox_cleanup(task_context.get("stories", []), target_section_name)
        ):
            comment_response = api_request(
                token=token,
                method="POST",
                path_or_url=f"/tasks/{task_gid}/stories",
                query={"opt_fields": story_write_opt_fields()},
                json_body={
                    "data": {
                        "html_text": inbox_cleanup_comment_html(
                            category_label=target_section_name,
                            evidence_lines=classification["reasons"][:4],
                        )
                    }
                },
            )
            comment_result = response_with_review_links(comment_response)
            comment_was_posted = True

        manager_comment_result: dict[str, Any] | None = None
        manager_comment_posted = False
        manager_plan = classification["manager_plan"]
        manager_comment_allowed = bool(classification["manager_comment_allowed"])
        manager_comment_block_reason = classification["shared_for_manager_comments_reason"]
        if not manager_comment_block_reason and not classification["substantive_manager_context"]:
            manager_comment_block_reason = "Task does not have enough context yet for a useful AI PM comment."
        if not manager_comment_block_reason and classification["work_type"] == "admin":
            manager_comment_block_reason = "Admin/personal reminder tasks are excluded from AI PM comments by default."
        should_post_manager_comment = False
        if args.apply and args.comment_research_todos and classification["work_type"] == "research":
            should_post_manager_comment = True
        elif args.apply and args.manager_comments and category_key in {
            "needs_next_action",
            "needs_verification",
            "waiting_on_others",
        }:
            should_post_manager_comment = True

        if (
            should_post_manager_comment
            and manager_comment_allowed
            and not comment_already_mentions_manager_plan(task_context.get("stories", []), target_section_name)
        ):
            manager_comment_response = api_request(
                token=token,
                method="POST",
                path_or_url=f"/tasks/{task_gid}/stories",
                query={"opt_fields": story_write_opt_fields()},
                json_body={
                    "data": {
                        "html_text": manager_plan_comment_html(
                            category_label=target_section_name,
                            work_type=classification["work_type"],
                            task_read=manager_plan["task_read"],
                            classification_basis=manager_plan["classification_basis"],
                            next_action=manager_plan["next_action"],
                            todo_label=manager_plan["todo_label"],
                            todo_items=manager_plan["todo_items"],
                            ask_user=manager_plan["ask_user"],
                            ai_help_summary=manager_plan["ai_help_summary"],
                            execution_prompt=manager_plan.get("execution_prompt"),
                        )
                    }
                },
            )
            manager_comment_result = response_with_review_links(manager_comment_response)
            manager_comment_posted = True

        task_results.append(
            {
                "task_gid": task_gid,
                "name": (task_context.get("task") or {}).get("name") or task.get("name"),
                "permalink_url": (task_context.get("task") or {}).get("permalink_url") or task.get("permalink_url"),
                "current_section": current_section_name,
                "target_section": target_section_name,
                "category_key": category_key,
                "work_type": classification["work_type"],
                "reasons": classification["reasons"],
                "linked_prs": classification["linked_prs"],
                "active_ai_action": classification["active_ai_action"],
                "manager_plan": manager_plan,
                "task_read": manager_plan["task_read"],
                "classification_basis": manager_plan["classification_basis"],
                "ask_user": manager_plan["ask_user"],
                "ai_help_now": manager_plan["ai_help_now"],
                "ai_help_summary": manager_plan["ai_help_summary"],
                "manager_comment_allowed": manager_comment_allowed,
                "manager_comment_block_reason": manager_comment_block_reason,
                "move_applied": bool(move_result),
                "comment_posted": comment_was_posted,
                "comment_review_url": comment_result.get("review_url") if isinstance(comment_result, dict) else None,
                "manager_comment_posted": manager_comment_posted,
                "manager_comment_review_url": manager_comment_result.get("review_url") if isinstance(manager_comment_result, dict) else None,
            }
        )

    payload = {
        "mode": "apply" if args.apply else "dry_run",
        "workspace_gid": workspace,
        "my_tasks": {
            "gid": user_task_list_gid,
            "name": user_task_list.get("name"),
        },
        "source_sections": source_section_names,
        "all_open": bool(args.all_open),
        "created_review_sections": created_section_names,
        "review_sections": sections_by_name,
        "counts": {
            "all_open_tasks_in_my_tasks": len(all_tasks),
            "tasks_considered": len(filtered_tasks),
            "tasks_skipped": len(skipped_tasks),
            "by_category": category_counts,
            "by_work_type": {},
        },
        "tasks": task_results,
        "skipped_tasks": skipped_tasks,
    }
    work_type_counts: dict[str, int] = {}
    execution_candidates = 0
    active_ai_action_counts: dict[str, int] = {}
    for task_result in task_results:
        work_type = str(task_result.get("work_type") or "unknown")
        work_type_counts[work_type] = work_type_counts.get(work_type, 0) + 1
        manager_plan = task_result.get("manager_plan") or {}
        if isinstance(manager_plan, dict) and manager_plan.get("execution_candidate"):
            execution_candidates += 1
        active_ai = task_result.get("active_ai_action") or {}
        if isinstance(active_ai, dict):
            action_name = str(active_ai.get("action") or "unknown")
            active_ai_action_counts[action_name] = active_ai_action_counts.get(action_name, 0) + 1
    payload["counts"]["by_work_type"] = work_type_counts
    payload["counts"]["execution_candidates"] = execution_candidates
    payload["counts"]["by_active_ai_action"] = active_ai_action_counts
    enriched = attach_skill_advertising(
        args=args,
        payload=payload,
        token=token,
        workspace_gid=workspace,
        cache=cache,
        include_first_run=True,
        refresh_summary=True,
    )
    return enriched


def slugify_category(value: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")
    return token or "category"


def write_json_file(path_value: str | None, payload: Any) -> str | None:
    target = str(path_value or "").strip()
    if not target:
        return None
    path = Path(target).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return str(path)


def inbox_cleanup_seed_categories() -> list[dict[str, str]]:
    return [dict(seed) for seed in INBOX_CLEANUP_CATEGORY_SEEDS]


def inbox_cleanup_snapshot_task(task_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_gid": task_result.get("task_gid"),
        "name": task_result.get("name"),
        "permalink_url": task_result.get("permalink_url"),
        "current_section": task_result.get("current_section"),
        "project_state": daily_briefing_project_state(task_result),
        "due_date": daily_briefing_due_date(task_result),
        "reasons": task_result.get("reasons", []) or [],
        "linked_prs": task_result.get("linked_prs", []) or [],
        "task_read_hint": task_result.get("task_read"),
        "classification_basis_hint": task_result.get("classification_basis"),
        "suggested_next_action_hint": ((task_result.get("manager_plan") or {}).get("next_action")),
        "ask_user_hint": task_result.get("ask_user"),
        "ai_help_summary_hint": task_result.get("ai_help_summary"),
        "active_ai_action_hint": ((task_result.get("active_ai_action") or {}).get("action")),
        "legacy_category_hint": task_result.get("category_key"),
        "legacy_target_section_hint": task_result.get("target_section"),
    }


def build_inbox_cleanup_plan_template(snapshot_payload: dict[str, Any]) -> dict[str, Any]:
    task_templates = []
    for task in snapshot_payload.get("tasks", []) or []:
        if not isinstance(task, dict):
            continue
        task_templates.append(
            {
                "task_gid": task.get("task_gid"),
                "name": task.get("name"),
                "decision": "ask_user",
                "category_slug": "",
                "target_section_name": "",
                "confidence": "low",
                "why": "",
                "question": task.get("ask_user_hint") or "",
                "notes": "",
            }
        )
    return {
        "workflow": INBOX_CLEANUP_PLAN_WORKFLOW,
        "version": INBOX_CLEANUP_PLAN_VERSION,
        "generated_at": now_iso(),
        "my_tasks_gid": ((snapshot_payload.get("my_tasks") or {}).get("gid")),
        "source": {
            "workflow": INBOX_CLEANUP_SNAPSHOT_WORKFLOW,
            "generated_at": snapshot_payload.get("generated_at"),
            "all_open": snapshot_payload.get("all_open"),
        },
        "categories": [],
        "category_seed_suggestions": inbox_cleanup_seed_categories(),
        "tasks": task_templates,
    }


def build_inbox_cleanup_snapshot_payload(args: argparse.Namespace) -> dict[str, Any]:
    review_args = argparse.Namespace(
        workspace=getattr(args, "workspace", None),
        source_section=list(getattr(args, "source_section", list(DEFAULT_INBOX_CLEANUP_SOURCE_SECTIONS))),
        all_open=bool(getattr(args, "all_open", False)),
        apply=False,
        skip_ready_comments=True,
        manager_comments=False,
        comment_research_todos=False,
        max_tasks=getattr(args, "max_tasks", 0),
        no_paginate=getattr(args, "no_paginate", False),
        limit_pages=getattr(args, "limit_pages", 0),
        compact=True,
        token=getattr(args, "token", None),
        command="inbox-cleanup",
    )
    review_payload = build_inbox_cleanup_review_payload(review_args)
    snapshot_payload = {
        "workflow": INBOX_CLEANUP_SNAPSHOT_WORKFLOW,
        "version": INBOX_CLEANUP_PLAN_VERSION,
        "generated_at": now_iso(),
        "workspace_gid": review_payload.get("workspace_gid"),
        "my_tasks": review_payload.get("my_tasks"),
        "source_sections": review_payload.get("source_sections"),
        "all_open": review_payload.get("all_open"),
        "current_section_counts": (((review_payload.get("skill_advertising") or {}).get("my_tasks") or {}).get("section_counts") or {}),
        "existing_sections": sorted(
            name
            for name in ((review_payload.get("review_sections") or {}).keys())
            if isinstance(name, str)
        ),
        "starter_category_seeds": inbox_cleanup_seed_categories(),
        "tasks": [
            inbox_cleanup_snapshot_task(task_result)
            for task_result in (review_payload.get("tasks") or [])
            if isinstance(task_result, dict)
        ],
        "legacy_review_hints": {
            "counts": review_payload.get("counts", {}) or {},
        },
        "instructions": {
            "summary": "Use this snapshot to create an AI-gated cleanup plan. The AI should define categories first, then assign tasks, and mark ambiguous items as ask_user instead of auto-bucketing them.",
            "required_task_fields": [
                "decision",
                "category_slug",
                "target_section_name",
                "confidence",
                "why",
                "question",
            ],
            "decision_values": [
                "bucket",
                "ask_user",
                "leave_as_is",
            ],
        },
    }
    snapshot_payload["plan_template"] = build_inbox_cleanup_plan_template(snapshot_payload)
    snapshot_path = write_json_file(getattr(args, "snapshot_file", None), snapshot_payload)
    plan_template_path = write_json_file(getattr(args, "plan_template_file", None), snapshot_payload["plan_template"])
    if snapshot_path:
        snapshot_payload["snapshot_file"] = snapshot_path
    if plan_template_path:
        snapshot_payload["plan_template_file"] = plan_template_path
    return snapshot_payload


def load_json_file(path_value: str) -> dict[str, Any]:
    path = Path(str(path_value)).expanduser()
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise SystemExit(f"Plan file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Plan file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected a JSON object in {path}")
    return payload


def resolve_inbox_cleanup_plan_categories(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    categories_by_slug: dict[str, dict[str, Any]] = {}
    for category in plan.get("categories", []) or []:
        if not isinstance(category, dict):
            continue
        slug = slugify_category(category.get("slug") or category.get("name"))
        categories_by_slug[slug] = dict(category)
    return categories_by_slug


def build_inbox_cleanup_payload(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "legacy_auto", False):
        return build_inbox_cleanup_review_payload(args)
    if getattr(args, "manager_comments", False) or getattr(args, "comment_research_todos", False) or getattr(args, "skip_ready_comments", False):
        raise SystemExit(
            "The AI-gated inbox-cleanup workflow no longer supports Python-authored cleanup comments. Generate a plan first, then let the AI decide whether comments are needed."
        )
    if getattr(args, "apply", False):
        if not getattr(args, "plan_file", None):
            raise SystemExit(
                "inbox-cleanup --apply now requires --plan-file. First run inbox-cleanup to emit a snapshot, let the AI write a plan JSON, then apply that plan."
            )
        return build_inbox_cleanup_plan_payload(args)
    if getattr(args, "plan_file", None):
        return build_inbox_cleanup_plan_payload(args)
    return build_inbox_cleanup_snapshot_payload(args)


def build_inbox_cleanup_plan_payload(args: argparse.Namespace) -> dict[str, Any]:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    workspace = workspace_default(args, context, cache)
    if not workspace:
        raise SystemExit("No workspace GID provided and no default workspace in asana-context.json")

    plan = load_json_file(args.plan_file)
    if str(plan.get("workflow") or "") != INBOX_CLEANUP_PLAN_WORKFLOW:
        raise SystemExit(
            f"Unsupported plan workflow in {args.plan_file}. Expected `{INBOX_CLEANUP_PLAN_WORKFLOW}`."
        )
    categories_by_slug = resolve_inbox_cleanup_plan_categories(plan)

    user_task_list = my_tasks_project(token, workspace)
    user_task_list_gid = str(user_task_list.get("gid") or "").strip()
    if not user_task_list_gid:
        raise SystemExit("Unable to resolve My Tasks for the selected workspace")

    all_tasks = my_tasks_tasks(
        token,
        user_task_list_gid,
        paginate=not args.no_paginate,
        limit_pages=args.limit_pages,
    )
    tasks_by_gid = {
        str(task.get("gid")): task
        for task in all_tasks
        if isinstance(task, dict) and task.get("gid")
    }
    existing_sections = my_tasks_sections(token, user_task_list_gid)
    sections_by_name = {
        str(section.get("name")): dict(section)
        for section in existing_sections
        if isinstance(section, dict) and section.get("name")
    }

    created_sections: list[str] = []
    results: list[dict[str, Any]] = []
    user_questions: list[dict[str, Any]] = []

    for entry in plan.get("tasks", []) or []:
        if not isinstance(entry, dict):
            continue
        task_gid = str(entry.get("task_gid") or "").strip()
        if not task_gid:
            continue
        task = tasks_by_gid.get(task_gid)
        if not task:
            results.append(
                {
                    "task_gid": task_gid,
                    "status": "missing_task",
                    "name": entry.get("name"),
                    "why": "Task is not currently open in My Tasks.",
                }
            )
            continue

        decision = str(entry.get("decision") or "ask_user").strip().casefold()
        confidence = str(entry.get("confidence") or "").strip().casefold()
        current_section = str(((task.get("assignee_section") or {}).get("name")) or "").strip()
        question = str(entry.get("question") or "").strip()
        why = str(entry.get("why") or "").strip()

        if decision in {"ask_user", "needs_user_input", "question"}:
            user_questions.append(
                {
                    "task_gid": task_gid,
                    "name": task.get("name"),
                    "current_section": current_section,
                    "question": question,
                    "why": why,
                }
            )
            results.append(
                {
                    "task_gid": task_gid,
                    "name": task.get("name"),
                    "status": "ask_user",
                    "current_section": current_section,
                    "question": question,
                    "why": why,
                }
            )
            continue
        if decision in {"leave", "leave_as_is", "skip"}:
            results.append(
                {
                    "task_gid": task_gid,
                    "name": task.get("name"),
                    "status": "left_as_is",
                    "current_section": current_section,
                    "why": why,
                }
            )
            continue

        category_slug = slugify_category(entry.get("category_slug") or "")
        category = categories_by_slug.get(category_slug, {})
        target_section_name = normalize_whitespace(
            entry.get("target_section_name")
            or category.get("section_name")
            or category.get("suggested_section_name")
            or category.get("name")
        )
        if not target_section_name:
            user_questions.append(
                {
                    "task_gid": task_gid,
                    "name": task.get("name"),
                    "current_section": current_section,
                    "question": question or "No target section was provided for this task.",
                    "why": why or f"Category `{category_slug}` is missing a target section.",
                }
            )
            results.append(
                {
                    "task_gid": task_gid,
                    "name": task.get("name"),
                    "status": "missing_target_section",
                    "current_section": current_section,
                    "category_slug": category_slug,
                    "why": why,
                }
            )
            continue

        if confidence == "low" and not getattr(args, "include_low_confidence", False):
            user_questions.append(
                {
                    "task_gid": task_gid,
                    "name": task.get("name"),
                    "current_section": current_section,
                    "question": question or "Low-confidence task: confirm the bucket before moving it.",
                    "why": why,
                }
            )
            results.append(
                {
                    "task_gid": task_gid,
                    "name": task.get("name"),
                    "status": "low_confidence_requires_user",
                    "current_section": current_section,
                    "target_section": target_section_name,
                    "confidence": confidence,
                    "why": why,
                }
            )
            continue

        target_section = sections_by_name.get(target_section_name)
        if not target_section and getattr(args, "apply", False):
            created = api_request(
                token=token,
                method="POST",
                path_or_url=f"/projects/{user_task_list_gid}/sections",
                json_body={"data": {"name": target_section_name}},
            ).get("data", {})
            if isinstance(created, dict) and created.get("name"):
                sections_by_name[str(created["name"])] = created
                target_section = created
                created_sections.append(str(created["name"]))

        moved = False
        if getattr(args, "apply", False) and target_section_name != current_section:
            target_section_gid = str((target_section or {}).get("gid") or "").strip()
            if not target_section_gid:
                raise SystemExit(f"Unable to resolve or create target section `{target_section_name}` for task {task_gid}")
            api_request(
                token=token,
                method="PUT",
                path_or_url=f"/tasks/{task_gid}",
                query={"opt_fields": "gid,name,assignee_section.gid,assignee_section.name"},
                json_body={"data": {"assignee_section": target_section_gid}},
            )
            moved = True

        results.append(
            {
                "task_gid": task_gid,
                "name": task.get("name"),
                "status": "moved" if moved else "planned",
                "current_section": current_section,
                "target_section": target_section_name,
                "category_slug": category_slug,
                "confidence": confidence,
                "why": why,
                "question": question,
            }
        )

    payload = {
        "workflow": INBOX_CLEANUP_PLAN_WORKFLOW,
        "version": INBOX_CLEANUP_PLAN_VERSION,
        "mode": "apply" if getattr(args, "apply", False) else "preview_plan",
        "generated_at": now_iso(),
        "workspace_gid": workspace,
        "my_tasks": {
            "gid": user_task_list_gid,
            "name": user_task_list.get("name"),
        },
        "created_sections": created_sections,
        "counts": {
            "tasks_in_plan": len(plan.get("tasks", []) or []),
            "tasks_known_open": len(tasks_by_gid),
            "moved": sum(1 for item in results if item.get("status") == "moved"),
            "planned_moves": sum(1 for item in results if item.get("status") in {"moved", "planned"}),
            "ask_user": sum(1 for item in results if item.get("status") == "ask_user"),
            "left_as_is": sum(1 for item in results if item.get("status") == "left_as_is"),
            "low_confidence_requires_user": sum(1 for item in results if item.get("status") == "low_confidence_requires_user"),
            "missing_target_section": sum(1 for item in results if item.get("status") == "missing_target_section"),
            "missing_task": sum(1 for item in results if item.get("status") == "missing_task"),
        },
        "results": results,
        "user_questions": user_questions,
    }
    return payload


def command_inbox_cleanup(args: argparse.Namespace) -> Any:
    enriched = build_inbox_cleanup_payload(args)
    print_json(enriched, args.compact)
    return enriched


def daily_briefing_project_state(task_result: dict[str, Any]) -> str:
    for reason in task_result.get("reasons", []) or []:
        if isinstance(reason, str) and reason.startswith("Project state: "):
            return reason.removeprefix("Project state: ").strip()
    return ""


def daily_briefing_due_date(task_result: dict[str, Any]) -> str | None:
    for reason in task_result.get("reasons", []) or []:
        if isinstance(reason, str) and reason.startswith("Due date: "):
            return reason.removeprefix("Due date: ").strip()
    return None


def daily_briefing_primary_pr(task_result: dict[str, Any]) -> str | None:
    linked_prs = task_result.get("linked_prs", []) or []
    if not linked_prs:
        return None
    first = linked_prs[0]
    pr_number = str(first.get("pr_number") or "").strip()
    if not pr_number:
        return None
    return f"PR #{pr_number}"


def daily_briefing_done_like(task_result: dict[str, Any]) -> bool:
    return bool(DAILY_BRIEFING_DONE_LIKE_PATTERN.search(daily_briefing_project_state(task_result)))


def daily_briefing_is_background_noise(task_result: dict[str, Any]) -> bool:
    if str(task_result.get("work_type") or "") == "admin":
        return True
    return bool(DAILY_BRIEFING_ADMIN_NOISE_PATTERN.search(str(task_result.get("name") or "")))


def daily_briefing_item(task_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_gid": task_result.get("task_gid"),
        "name": task_result.get("name"),
        "url": task_result.get("permalink_url"),
        "current_section": task_result.get("current_section"),
        "target_section": task_result.get("target_section"),
        "project_state": daily_briefing_project_state(task_result),
        "work_type": task_result.get("work_type"),
        "due_date": daily_briefing_due_date(task_result),
        "pr": daily_briefing_primary_pr(task_result),
        "linked_prs": task_result.get("linked_prs", []) or [],
        "task_read": task_result.get("task_read"),
        "next_action": ((task_result.get("manager_plan") or {}).get("next_action")),
        "active_ai_action": task_result.get("active_ai_action") or {},
        "task_result": task_result,
    }


def daily_briefing_bucket_score(bucket_name: str, task_result: dict[str, Any]) -> int:
    score = 0
    current_section = str(task_result.get("current_section") or "")
    project_state = daily_briefing_project_state(task_result)
    task_name = str(task_result.get("name") or "")
    reasons_blob = " ".join(str(reason) for reason in (task_result.get("reasons") or []))

    if daily_briefing_primary_pr(task_result):
        score += 4
    if "Recently assigned" in current_section:
        score += 3
    if daily_briefing_due_date(task_result):
        score += 2
    if (
        DAILY_BRIEFING_URGENT_PATTERN.search(project_state)
        or DAILY_BRIEFING_URGENT_PATTERN.search(task_name)
        or DAILY_BRIEFING_URGENT_PATTERN.search(reasons_blob)
    ):
        score += 2
    if bucket_name == "release_watch" and daily_briefing_done_like(task_result):
        score += 3
    if bucket_name == "needs_follow_up" and "Waiting" in current_section:
        score += 2
    if bucket_name == "ready_to_close" and "Likely Ready To Close" in current_section:
        score += 2
    return score


def daily_briefing_bucket_key(task_result: dict[str, Any]) -> str:
    action = str(((task_result.get("active_ai_action") or {}).get("action")) or "no_ai_action")

    if daily_briefing_is_background_noise(task_result):
        return "background"
    if action == "ask_to_close":
        return "ready_to_close"
    if action == "ask_to_follow_up":
        return "needs_follow_up"
    if action == "ask_to_execute_now" and daily_briefing_done_like(task_result):
        return "release_watch"
    if action == "ask_to_verify":
        return "needs_verification"
    if action == "ask_to_execute_now" and not daily_briefing_done_like(task_result):
        return "execute_now"
    return "background"


def daily_briefing_action_summary(bucket_name: str, item: dict[str, Any]) -> str:
    if item.get("task_read"):
        return str(item["task_read"])
    if bucket_name == "execute_now":
        if item.get("pr"):
            return "Real execution signal detected; this looks like active code work."
        return "This looks actionable now and worth pulling into an active work session."
    if bucket_name == "release_watch":
        return "Implementation appears done on our side; keep this visible for ship tracking and close-out."
    if bucket_name == "needs_verification":
        return "This looks like QA, confirmation, or post-ship validation work."
    if bucket_name == "needs_follow_up":
        return "This looks blocked on another person or team and needs one concrete follow-up."
    if bucket_name == "ready_to_close":
        return "Recent context suggests this may be ready for manual close-out."
    return "Keep this visible, but it should not drive the day by default."


def daily_briefing_bucket_seeds() -> list[dict[str, Any]]:
    return [dict(seed) for seed in DAILY_BRIEFING_BUCKET_SEEDS]


def daily_briefing_snapshot_task(task_context: dict[str, Any]) -> dict[str, Any]:
    task = (task_context.get("task") or {}) if isinstance(task_context, dict) else {}
    stories = (task_context.get("stories") or []) if isinstance(task_context, dict) else []
    notes = strip_html_to_text(task.get("html_notes") or task.get("notes"))
    recent_comment_excerpts = [
        shorten_text(story_text(story), limit=180)
        for story in recent_comments({"stories": stories}, limit=3)
        if shorten_text(story_text(story), limit=180)
    ]
    combined_text = " ".join(
        part
        for part in [str(task.get("name") or ""), notes, *recent_comment_excerpts]
        if part
    )
    linked_prs = extract_github_pr_links(combined_text)
    follower_count = len([item for item in (task.get("followers") or []) if isinstance(item, dict)])
    collaborator_count = len([item for item in (task.get("collaborators") or []) if isinstance(item, dict)])
    return {
        "task_gid": task.get("gid"),
        "name": task.get("name"),
        "url": task.get("permalink_url"),
        "current_section": str(((task.get("assignee_section") or {}).get("name")) or "").strip(),
        "due_date": short_date(task.get("due_on") or task.get("due_at")),
        "completed": bool(task.get("completed")),
        "project_memberships": task_membership_labels(task),
        "project_names": task_project_names(task),
        "linked_prs": linked_prs,
        "primary_pr": primary_pr_label(linked_prs),
        "notes_excerpt": shorten_text(notes, limit=280),
        "recent_comment_excerpts": recent_comment_excerpts,
        "follower_count": follower_count,
        "collaborator_count": collaborator_count,
        "assignee": ((task.get("assignee") or {}).get("name")),
        "raw_signals": {
            "has_due_date": bool(task.get("due_on") or task.get("due_at")),
            "has_pr": bool(linked_prs),
            "in_recently_assigned": str(((task.get("assignee_section") or {}).get("name")) or "").strip() == "Recently assigned",
            "has_notes": bool(notes),
            "has_recent_comments": bool(recent_comment_excerpts),
        },
    }


def build_daily_briefing_plan_template(snapshot_payload: dict[str, Any]) -> dict[str, Any]:
    task_templates = []
    for task in snapshot_payload.get("tasks", []) or []:
        if not isinstance(task, dict):
            continue
        task_templates.append(
            {
                "task_gid": task.get("task_gid"),
                "name": task.get("name"),
                "decision": "omit",
                "bucket_slug": "",
                "confidence": "low",
                "why": "",
                "next_action": "",
                "question": "",
                "notes": "",
            }
        )
    return {
        "workflow": DAILY_BRIEFING_PLAN_WORKFLOW,
        "version": DAILY_BRIEFING_PLAN_VERSION,
        "generated_at": now_iso(),
        "my_tasks_gid": ((snapshot_payload.get("my_tasks") or {}).get("gid")),
        "source": {
            "workflow": DAILY_BRIEFING_SNAPSHOT_WORKFLOW,
            "generated_at": snapshot_payload.get("generated_at"),
        },
        "overview": "",
        "focus": "",
        "final_markdown": "",
        "categories": daily_briefing_bucket_seeds(),
        "tasks": task_templates,
    }


def build_daily_briefing_snapshot_payload(args: argparse.Namespace) -> dict[str, Any]:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    workspace = workspace_default(args, context, cache)
    if not workspace:
        raise SystemExit("No workspace GID provided and no default workspace in asana-context.json")

    user_task_list = my_tasks_project(token, workspace)
    user_task_list_gid = str(user_task_list.get("gid") or "").strip()
    if not user_task_list_gid:
        raise SystemExit("Unable to resolve My Tasks for the selected workspace")

    all_tasks = my_tasks_tasks(
        token,
        user_task_list_gid,
        paginate=not args.no_paginate,
        limit_pages=args.limit_pages,
    )
    filtered_tasks = [task for task in all_tasks if isinstance(task, dict)]
    if args.max_tasks > 0:
        filtered_tasks = filtered_tasks[: args.max_tasks]

    task_contexts = fetch_task_review_context(
        token,
        [str(task.get("gid")) for task in filtered_tasks if task.get("gid")],
    )
    snapshot_tasks = []
    for task in filtered_tasks:
        task_gid = str(task.get("gid") or "").strip()
        if not task_gid:
            continue
        snapshot_tasks.append(daily_briefing_snapshot_task(task_contexts.get(task_gid) or {"task": task, "stories": []}))

    payload = {
        "workflow": DAILY_BRIEFING_SNAPSHOT_WORKFLOW,
        "version": DAILY_BRIEFING_PLAN_VERSION,
        "generated_at": now_iso(),
        "workspace_gid": workspace,
        "my_tasks": {
            "gid": user_task_list_gid,
            "name": user_task_list.get("name"),
        },
        "open_task_count": len(all_tasks),
        "tasks_considered": len(snapshot_tasks),
        "current_section_counts": my_tasks_summary(
            token,
            workspace_gid=workspace,
            cache=cache,
            refresh=True,
        ).get("section_counts", {}),
        "starter_buckets": daily_briefing_bucket_seeds(),
        "tasks": snapshot_tasks,
        "instructions": {
            "summary": "Use this snapshot to decide which tasks are actually actionable today. The agent should auto-author the daily briefing plan from this data, render it for the user, and only surface ask_user items when ambiguity materially changes the day plan.",
            "required_task_fields": [
                "decision",
                "bucket_slug",
                "confidence",
                "why",
                "next_action",
                "question",
            ],
            "required_plan_fields": [
                "overview",
                "focus",
                "final_markdown",
            ],
            "decision_values": [
                "highlight",
                "ask_user",
                "omit",
            ],
        },
    }
    payload["plan_template"] = build_daily_briefing_plan_template(payload)
    snapshot_path = write_json_file(getattr(args, "snapshot_file", None), payload)
    plan_template_path = write_json_file(getattr(args, "plan_template_file", None), payload["plan_template"])
    if snapshot_path:
        payload["snapshot_file"] = snapshot_path
    if plan_template_path:
        payload["plan_template_file"] = plan_template_path
    return payload


def resolve_daily_briefing_plan_categories(plan: dict[str, Any]) -> list[dict[str, Any]]:
    categories_by_slug: dict[str, dict[str, Any]] = {}
    for seed in daily_briefing_bucket_seeds():
        slug = slugify_category(seed.get("slug") or seed.get("name"))
        entry = dict(seed)
        entry["slug"] = slug
        categories_by_slug[slug] = entry
    for index, category in enumerate(plan.get("categories", []) or [], start=1):
        if not isinstance(category, dict):
            continue
        slug = slugify_category(category.get("slug") or category.get("name"))
        merged = dict(categories_by_slug.get(slug) or {})
        merged.update(category)
        merged["slug"] = slug
        if "display_order" not in merged:
            merged["display_order"] = len(categories_by_slug) + index
        categories_by_slug[slug] = merged
    return sorted(
        categories_by_slug.values(),
        key=lambda item: (
            int(item.get("display_order") or 999),
            str(item.get("name") or item.get("slug") or "").casefold(),
        ),
    )


def build_daily_briefing_review_payload(args: argparse.Namespace) -> dict[str, Any]:
    cleanup_args = argparse.Namespace(
        workspace=getattr(args, "workspace", None),
        source_section=list(DEFAULT_INBOX_CLEANUP_SOURCE_SECTIONS),
        all_open=True,
        apply=False,
        skip_ready_comments=True,
        manager_comments=False,
        comment_research_todos=False,
        max_tasks=getattr(args, "max_tasks", 0),
        no_paginate=getattr(args, "no_paginate", False),
        limit_pages=getattr(args, "limit_pages", 0),
        compact=True,
        token=getattr(args, "token", None),
        command="daily-briefing",
    )
    cleanup_payload = build_inbox_cleanup_review_payload(cleanup_args)
    task_results = cleanup_payload.get("tasks", []) or []
    bucketed: dict[str, list[dict[str, Any]]] = {
        "execute_now": [],
        "release_watch": [],
        "needs_verification": [],
        "needs_follow_up": [],
        "ready_to_close": [],
        "background": [],
    }
    for task_result in task_results:
        if not isinstance(task_result, dict):
            continue
        bucket_key = daily_briefing_bucket_key(task_result)
        bucketed[bucket_key].append(daily_briefing_item(task_result))

    for bucket_name, entries in bucketed.items():
        entries.sort(
            key=lambda entry: (
                -daily_briefing_bucket_score(bucket_name, entry["task_result"]),
                str(entry.get("name") or "").casefold(),
            )
        )
        for entry in entries:
            entry.pop("task_result", None)

    summary = {
        "open_task_count": int(((cleanup_payload.get("counts") or {}).get("all_open_tasks_in_my_tasks")) or len(task_results)),
        "execute_now_count": len(bucketed["execute_now"]),
        "release_watch_count": len(bucketed["release_watch"]),
        "needs_verification_count": len(bucketed["needs_verification"]),
        "needs_follow_up_count": len(bucketed["needs_follow_up"]),
        "ready_to_close_count": len(bucketed["ready_to_close"]),
        "background_count": len(bucketed["background"]),
    }
    payload = {
        "briefing_date": datetime.now().strftime("%B %d, %Y"),
        "generated_at": now_iso(),
        "summary": summary,
        "buckets": bucketed,
        "source": {
            "command": "python3 scripts/asana_api.py inbox-cleanup --all-open",
            "workspace_gid": cleanup_payload.get("workspace_gid"),
            "my_tasks": cleanup_payload.get("my_tasks"),
        },
    }
    return payload


def build_daily_briefing_plan_payload(args: argparse.Namespace) -> dict[str, Any]:
    token = get_token(args)
    context = load_context()
    cache = load_cache()
    workspace = workspace_default(args, context, cache)
    if not workspace:
        raise SystemExit("No workspace GID provided and no default workspace in asana-context.json")

    plan = load_json_file(args.plan_file)
    if str(plan.get("workflow") or "") != DAILY_BRIEFING_PLAN_WORKFLOW:
        raise SystemExit(
            f"Unsupported plan workflow in {args.plan_file}. Expected `{DAILY_BRIEFING_PLAN_WORKFLOW}`."
        )
    final_markdown = str(plan.get("final_markdown") or "").strip()
    if not final_markdown:
        raise SystemExit(
            f"Daily briefing plan in {args.plan_file} must include a non-empty `final_markdown` field authored by the AI."
        )

    user_task_list = my_tasks_project(token, workspace)
    user_task_list_gid = str(user_task_list.get("gid") or "").strip()
    if not user_task_list_gid:
        raise SystemExit("Unable to resolve My Tasks for the selected workspace")

    all_tasks = my_tasks_tasks(
        token,
        user_task_list_gid,
        paginate=not args.no_paginate,
        limit_pages=args.limit_pages,
    )
    tasks_by_gid = {
        str(task.get("gid")): task
        for task in all_tasks
        if isinstance(task, dict) and task.get("gid")
    }
    planned_task_gids = [
        str(entry.get("task_gid") or "").strip()
        for entry in (plan.get("tasks") or [])
        if isinstance(entry, dict) and entry.get("task_gid")
    ]
    task_contexts = fetch_task_review_context(token, [gid for gid in planned_task_gids if gid in tasks_by_gid])
    task_cards = {
        gid: daily_briefing_snapshot_task(task_contexts.get(gid) or {"task": tasks_by_gid.get(gid) or {}, "stories": []})
        for gid in planned_task_gids
        if gid in tasks_by_gid
    }

    categories = resolve_daily_briefing_plan_categories(plan)
    categories_by_slug = {
        slugify_category(category.get("slug") or category.get("name")): category
        for category in categories
        if isinstance(category, dict)
    }
    buckets: dict[str, list[dict[str, Any]]] = {
        slugify_category(category.get("slug") or category.get("name")): []
        for category in categories
        if isinstance(category, dict)
    }
    results: list[dict[str, Any]] = []
    user_questions: list[dict[str, Any]] = []

    for entry in plan.get("tasks", []) or []:
        if not isinstance(entry, dict):
            continue
        task_gid = str(entry.get("task_gid") or "").strip()
        if not task_gid:
            continue
        task_card = task_cards.get(task_gid)
        if not task_card:
            results.append(
                {
                    "task_gid": task_gid,
                    "name": entry.get("name"),
                    "status": "missing_task",
                    "why": "Task is not currently open in My Tasks.",
                }
            )
            continue

        decision = str(entry.get("decision") or "omit").strip().casefold()
        confidence = str(entry.get("confidence") or "").strip().casefold()
        why = normalize_whitespace(entry.get("why"))
        question = normalize_whitespace(entry.get("question"))
        next_action = normalize_whitespace(entry.get("next_action"))

        if decision in {"ask_user", "question", "needs_user_input"}:
            question_entry = dict(task_card)
            question_entry.update(
                {
                    "why": why,
                    "question": question,
                    "next_action": next_action,
                    "confidence": confidence,
                }
            )
            user_questions.append(question_entry)
            results.append(
                {
                    "task_gid": task_gid,
                    "name": task_card.get("name"),
                    "status": "ask_user",
                    "why": why,
                    "question": question,
                }
            )
            continue

        if decision in {"omit", "leave_as_is", "skip", "background"}:
            results.append(
                {
                    "task_gid": task_gid,
                    "name": task_card.get("name"),
                    "status": "omitted",
                    "why": why,
                }
            )
            continue

        bucket_slug = slugify_category(entry.get("bucket_slug") or "")
        category = categories_by_slug.get(bucket_slug)
        if decision in {"highlight", "include", "bucket"} and not category:
            user_questions.append(
                {
                    **task_card,
                    "why": why or f"Bucket `{bucket_slug}` is missing from the plan categories.",
                    "question": question or "Which briefing bucket should this task belong in?",
                    "next_action": next_action,
                    "confidence": confidence,
                }
            )
            results.append(
                {
                    "task_gid": task_gid,
                    "name": task_card.get("name"),
                    "status": "missing_bucket",
                    "bucket_slug": bucket_slug,
                    "why": why,
                }
            )
            continue

        if bucket_slug not in buckets:
            buckets[bucket_slug] = []
        highlighted = dict(task_card)
        highlighted.update(
            {
                "why": why,
                "next_action": next_action,
                "question": question,
                "confidence": confidence,
                "bucket_slug": bucket_slug,
                "notes": normalize_whitespace(entry.get("notes")),
            }
        )
        buckets[bucket_slug].append(highlighted)
        results.append(
            {
                "task_gid": task_gid,
                "name": task_card.get("name"),
                "status": "highlighted",
                "bucket_slug": bucket_slug,
                "why": why,
            }
        )

    summary = {
        "open_task_count": len(all_tasks),
        "tasks_in_plan": len(plan.get("tasks", []) or []),
        "highlighted_count": sum(1 for item in results if item.get("status") == "highlighted"),
        "ask_user_count": sum(1 for item in results if item.get("status") == "ask_user"),
        "omitted_count": sum(1 for item in results if item.get("status") == "omitted"),
        "missing_task_count": sum(1 for item in results if item.get("status") == "missing_task"),
        "missing_bucket_count": sum(1 for item in results if item.get("status") == "missing_bucket"),
    }
    payload = {
        "workflow": DAILY_BRIEFING_PLAN_WORKFLOW,
        "version": DAILY_BRIEFING_PLAN_VERSION,
        "mode": "render_plan",
        "briefing_date": datetime.now().strftime("%B %d, %Y"),
        "generated_at": now_iso(),
        "overview": normalize_whitespace(plan.get("overview"))
        or "AI-selected daily briefing over the current My Tasks list.",
        "focus": normalize_whitespace(plan.get("focus")),
        "summary": summary,
        "categories": categories,
        "buckets": buckets,
        "user_questions": user_questions,
        "results": results,
        "final_markdown": final_markdown,
        "source": {
            "command": "python3 scripts/asana_api.py daily-briefing",
            "workspace_gid": workspace,
            "my_tasks": {
                "gid": user_task_list_gid,
                "name": user_task_list.get("name"),
            },
        },
    }
    return payload


def build_daily_briefing(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "legacy_auto", False):
        return build_daily_briefing_review_payload(args)
    if getattr(args, "plan_file", None):
        return build_daily_briefing_plan_payload(args)
    return build_daily_briefing_snapshot_payload(args)


def command_daily_briefing(args: argparse.Namespace) -> Any:
    if getattr(args, "markdown", False) and not getattr(args, "plan_file", None) and not getattr(args, "legacy_auto", False):
        raise SystemExit(
            "daily-briefing --markdown now requires --plan-file. First run daily-briefing to emit a snapshot, let the AI write a plan JSON, then render that plan."
        )
    payload = build_daily_briefing(args)
    if getattr(args, "markdown", False):
        final_markdown = str(payload.get("final_markdown") or "").strip()
        if not final_markdown:
            raise SystemExit(
                "daily-briefing --markdown requires the AI-authored plan JSON to include `final_markdown`."
            )
        print(final_markdown)
    else:
        print_json(payload, args.compact)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local Asana API helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    request_parser = subparsers.add_parser("request", help="Run a generic Asana API request")
    request_parser.add_argument("method", help="HTTP method, e.g. GET/POST/PUT/DELETE")
    request_parser.add_argument("path", help="Relative /path or full URL")
    request_parser.add_argument("--query", action="append", help="Query param as key=value")
    request_parser.add_argument("--opt-fields", help="Asana opt_fields value")
    request_parser.add_argument("--opt-expand", help="Asana opt_expand value")
    request_parser.add_argument("--data", help="JSON payload string")
    request_parser.add_argument("--data-file", help="Path to JSON payload file")
    request_parser.add_argument(
        "--no-wrap-data",
        action="store_true",
        help="Do not auto-wrap JSON payloads in {\"data\": ...}",
    )
    request_parser.add_argument("--form", action="append", help="Multipart form field as key=value")
    request_parser.add_argument("--file", action="append", help="Multipart file field as field=/abs/path")
    request_parser.add_argument("--paginate", action="store_true", help="Follow Asana next_page links")
    request_parser.add_argument("--limit-pages", type=int, default=0, help="Stop pagination after N pages")
    add_common_output_flags(request_parser)
    request_parser.set_defaults(func=command_request)

    whoami_parser = subparsers.add_parser("whoami", help="Show the authenticated user")
    add_common_output_flags(whoami_parser)
    whoami_parser.set_defaults(func=command_whoami)

    workspaces_parser = subparsers.add_parser("workspaces", help="List workspaces for the authenticated user")
    add_common_output_flags(workspaces_parser)
    workspaces_parser.set_defaults(func=command_workspaces)

    teams_parser = subparsers.add_parser("teams", help="List teams in a workspace")
    teams_parser.add_argument("--workspace", help="Workspace GID override")
    add_common_output_flags(teams_parser)
    teams_parser.set_defaults(func=command_teams)

    users_parser = subparsers.add_parser("users", help="List users in a workspace")
    users_parser.add_argument("--workspace", help="Workspace GID override")
    users_parser.add_argument("--opt-fields", help="Override result fields")
    users_parser.add_argument("--paginate", action="store_true", help="Follow Asana next_page links")
    users_parser.add_argument("--limit-pages", type=int, default=0, help="Stop pagination after N pages")
    add_common_output_flags(users_parser)
    users_parser.set_defaults(func=command_users)

    projects_parser = subparsers.add_parser("projects", help="List projects for a team")
    projects_parser.add_argument("--team", help="Team GID or exact team name from asana-context.json")
    add_common_output_flags(projects_parser)
    projects_parser.set_defaults(func=command_projects)

    task_parser = subparsers.add_parser("task", help="Inspect one or more tasks")
    task_parser.add_argument("task_gids", nargs="+", help="Task gids; comma-separated values allowed")
    task_parser.add_argument("--opt-fields", help="Override task fields")
    add_common_output_flags(task_parser)
    task_parser.set_defaults(func=command_task)

    story_parser = subparsers.add_parser("story", help="Inspect one or more stories/comments by gid")
    story_parser.add_argument("story_gids", nargs="+", help="Story gids; comma-separated values allowed")
    story_parser.add_argument("--opt-fields", help="Override story fields")
    add_common_output_flags(story_parser)
    story_parser.set_defaults(func=command_story)

    task_bundle_parser = subparsers.add_parser(
        "task-bundle",
        help="Fetch one or more tasks with comments, attachments, and optional project workflow context",
    )
    task_bundle_parser.add_argument("task_gids", nargs="+", help="Task gids; comma-separated values allowed")
    task_bundle_parser.add_argument(
        "--project-gid",
        help="Project GID to include section order and project custom field settings",
    )
    task_bundle_parser.add_argument("--task-opt-fields", help="Override task fields")
    task_bundle_parser.add_argument("--story-opt-fields", help="Override story fields")
    task_bundle_parser.add_argument("--attachment-opt-fields", help="Override attachment fields")
    add_common_output_flags(task_bundle_parser)
    task_bundle_parser.set_defaults(func=command_task_bundle)

    task_status_parser = subparsers.add_parser("task-status", help="Summarize completion and board-column status for one or more tasks")
    task_status_parser.add_argument("task_gids", nargs="+", help="Task gids; comma-separated values allowed")
    task_status_parser.add_argument("--project", help="Limit membership analysis to one project GID")
    task_status_parser.add_argument("--opt-fields", help="Override task fields")
    task_status_parser.add_argument(
        "--include-task-position",
        action="store_true",
        help="Also look up the task's position inside its current section",
    )
    add_common_output_flags(task_status_parser)
    task_status_parser.set_defaults(func=command_task_status)

    project_parser = subparsers.add_parser("project", help="Inspect one or more projects")
    project_parser.add_argument("project_gids", nargs="+", help="Project gids; comma-separated values allowed")
    project_parser.add_argument("--opt-fields", help="Override project fields")
    add_common_output_flags(project_parser)
    project_parser.set_defaults(func=command_project)

    board_parser = subparsers.add_parser("board", help="Return project sections in order with tasks in each section")
    board_parser.add_argument("project_gid")
    board_parser.add_argument("--opt-fields", help="Override per-task fields in board output")
    board_parser.add_argument("--context", action="store_true", help="Include workflow context stats (field coverage, staleness, assignee distribution)")
    add_common_output_flags(board_parser)
    board_parser.set_defaults(func=command_project_board)

    project_tasks_parser = subparsers.add_parser("project-tasks", help="List tasks in a project")
    project_tasks_parser.add_argument("project_gid")
    project_tasks_parser.add_argument("--opt-fields", help="Override task fields")
    project_tasks_parser.add_argument("--paginate", action="store_true", help="Follow Asana next_page links")
    project_tasks_parser.add_argument("--limit-pages", type=int, default=0, help="Stop pagination after N pages")
    add_common_output_flags(project_tasks_parser)
    project_tasks_parser.set_defaults(func=command_project_tasks)

    project_assigned_parser = subparsers.add_parser(
        "project-assigned-tasks",
        help="List assigned work in a project, including matching subtasks with parent section context",
    )
    project_assigned_parser.add_argument("project_gid")
    project_assigned_parser.add_argument("--workspace", help="Workspace GID override")
    project_assigned_parser.add_argument(
        "--assignee",
        help="Assignee filter, defaults to the current user from asana-context.json; accepts gid, exact cached name, or exact cached email",
    )
    project_assigned_parser.add_argument("--completed", type=parse_bool, help="Filter by completion")
    project_assigned_parser.add_argument("--opt-fields", help="Override result fields")
    project_assigned_parser.add_argument(
        "--include-task-position",
        action="store_true",
        help="Also look up each task's position inside its effective board section",
    )
    project_assigned_parser.add_argument(
        "--include-comments",
        action="store_true",
        help="Include recent comment context for each task",
    )
    project_assigned_parser.add_argument(
        "--comment-limit",
        type=int,
        default=3,
        help="Maximum number of recent comments to keep per task when --include-comments is set",
    )
    project_assigned_parser.add_argument(
        "--include-attachments",
        action="store_true",
        help="Include task attachments and derived image URLs for each task",
    )
    project_assigned_parser.add_argument("--paginate", action="store_true", help="Follow Asana next_page links")
    project_assigned_parser.add_argument("--limit-pages", type=int, default=0, help="Stop pagination after N pages")
    add_common_output_flags(project_assigned_parser)
    project_assigned_parser.set_defaults(func=command_project_assigned_tasks)

    sections_parser = subparsers.add_parser("sections", help="List sections in a project")
    sections_parser.add_argument("project_gid")
    sections_parser.add_argument("--opt-fields", help="Override section fields")
    add_common_output_flags(sections_parser)
    sections_parser.set_defaults(func=command_sections)

    section_parser = subparsers.add_parser("section", help="Inspect one or more sections")
    section_parser.add_argument("section_gids", nargs="+", help="Section gids; comma-separated values allowed")
    section_parser.add_argument("--opt-fields", help="Override section fields")
    add_common_output_flags(section_parser)
    section_parser.set_defaults(func=command_section)

    section_tasks_parser = subparsers.add_parser("section-tasks", help="List tasks in a section")
    section_tasks_parser.add_argument("section_gid")
    section_tasks_parser.add_argument("--opt-fields", help="Override task fields")
    section_tasks_parser.add_argument("--paginate", action="store_true", help="Follow Asana next_page links")
    section_tasks_parser.add_argument("--limit-pages", type=int, default=0, help="Stop pagination after N pages")
    add_common_output_flags(section_tasks_parser)
    section_tasks_parser.set_defaults(func=command_section_tasks)

    create_section_parser = subparsers.add_parser("create-section", help="Create a section in a project")
    create_section_parser.add_argument("project_gid")
    create_section_parser.add_argument("--name", required=True)
    add_common_output_flags(create_section_parser)
    create_section_parser.set_defaults(func=command_create_section)

    update_section_parser = subparsers.add_parser("update-section", help="Rename a section")
    update_section_parser.add_argument("section_gid")
    update_section_parser.add_argument("--name", required=True)
    add_common_output_flags(update_section_parser)
    update_section_parser.set_defaults(func=command_update_section)

    close_out_sections_parser = subparsers.add_parser(
        "close-out-sections",
        help="Relocate tasks out of section(s) and delete the section(s) once empty",
    )
    close_out_sections_parser.add_argument("project_gid")
    close_out_sections_parser.add_argument(
        "--section",
        action="append",
        required=True,
        help="Source section gid or exact name; pass multiple times for multiple sections",
    )
    close_out_sections_parser.add_argument(
        "--move-to",
        help="Destination section gid or exact name within the same project",
    )
    close_out_sections_parser.add_argument(
        "--completed-mode",
        choices=("all", "completed", "incomplete"),
        default="all",
        help="Which tasks to relocate before attempting deletion",
    )
    close_out_sections_parser.add_argument(
        "--limit-pages",
        type=int,
        default=0,
        help="Stop section task pagination after N pages while collecting tasks",
    )
    close_out_sections_parser.add_argument(
        "--apply",
        action="store_true",
        help="Move selected tasks and delete sections that end up empty",
    )
    add_common_output_flags(close_out_sections_parser)
    close_out_sections_parser.set_defaults(func=command_close_out_sections)

    search_parser = subparsers.add_parser("search-tasks", help="Search tasks within a workspace")
    search_parser.add_argument("--text", required=True, help="Search text")
    search_parser.add_argument("--workspace", help="Workspace GID override")
    search_parser.add_argument("--project", help="Project GID filter")
    search_parser.add_argument("--assignee", help="Assignee filter, e.g. me, user gid, exact cached user name, or exact cached email")
    search_parser.add_argument("--completed", type=parse_bool, help="Filter by completion")
    search_parser.add_argument("--opt-fields", help="Override result fields")
    search_parser.add_argument("--paginate", action="store_true", help="Follow Asana next_page links")
    search_parser.add_argument("--limit-pages", type=int, default=0, help="Stop pagination after N pages")
    add_common_output_flags(search_parser)
    search_parser.set_defaults(func=command_search_tasks)

    inbox_cleanup_parser = subparsers.add_parser(
        "inbox-cleanup",
        help="Generate an AI-gated My Tasks cleanup snapshot or apply an AI-authored cleanup plan",
    )
    inbox_cleanup_parser.add_argument("--workspace", help="Workspace GID override")
    inbox_cleanup_parser.add_argument(
        "--source-section",
        action="append",
        default=list(DEFAULT_INBOX_CLEANUP_SOURCE_SECTIONS),
        help="My Tasks section name to triage from; defaults to Recently assigned",
    )
    inbox_cleanup_parser.add_argument(
        "--all-open",
        action="store_true",
        help="Ignore source-section filtering and include all open tasks in My Tasks",
    )
    inbox_cleanup_parser.add_argument(
        "--snapshot-file",
        help="Write the AI-gating snapshot JSON to this file in addition to stdout",
    )
    inbox_cleanup_parser.add_argument(
        "--plan-template-file",
        help="Write an editable cleanup plan template JSON to this file",
    )
    inbox_cleanup_parser.add_argument(
        "--plan-file",
        help="Path to an AI-authored cleanup plan JSON to preview or apply",
    )
    inbox_cleanup_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the provided AI-authored plan file by moving tasks into the requested sections",
    )
    inbox_cleanup_parser.add_argument(
        "--include-low-confidence",
        action="store_true",
        help="When applying a plan, also move tasks marked low confidence instead of leaving them for user review",
    )
    inbox_cleanup_parser.add_argument(
        "--legacy-auto",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    inbox_cleanup_parser.add_argument(
        "--skip-ready-comments",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    inbox_cleanup_parser.add_argument(
        "--manager-comments",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    inbox_cleanup_parser.add_argument(
        "--comment-research-todos",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    inbox_cleanup_parser.add_argument(
        "--max-tasks",
        type=int,
        default=0,
        help="Limit how many filtered tasks to process after section filtering",
    )
    inbox_cleanup_parser.add_argument(
        "--no-paginate",
        action="store_true",
        help="Do not follow My Tasks next_page links",
    )
    inbox_cleanup_parser.add_argument(
        "--limit-pages",
        type=int,
        default=0,
        help="Stop My Tasks pagination after N pages",
    )
    add_common_output_flags(inbox_cleanup_parser)
    inbox_cleanup_parser.set_defaults(func=command_inbox_cleanup)

    daily_briefing_parser = subparsers.add_parser(
        "daily-briefing",
        help="Generate an AI-gated daily briefing snapshot or render an AI-authored morning briefing plan",
    )
    daily_briefing_parser.add_argument("--workspace", help="Workspace GID override")
    daily_briefing_parser.add_argument(
        "--max-tasks",
        type=int,
        default=0,
        help="Limit how many My Tasks items to include in the briefing snapshot",
    )
    daily_briefing_parser.add_argument(
        "--no-paginate",
        action="store_true",
        help="Do not follow My Tasks next_page links",
    )
    daily_briefing_parser.add_argument(
        "--limit-pages",
        type=int,
        default=0,
        help="Stop My Tasks pagination after N pages",
    )
    daily_briefing_parser.add_argument(
        "--snapshot-file",
        help="Write the daily briefing snapshot JSON to this file in addition to stdout",
    )
    daily_briefing_parser.add_argument(
        "--plan-template-file",
        help="Write an editable daily briefing plan template JSON to this file",
    )
    daily_briefing_parser.add_argument(
        "--plan-file",
        help="Path to an AI-authored daily briefing plan JSON to render",
    )
    daily_briefing_parser.add_argument(
        "--markdown",
        action="store_true",
        help="Print the rendered markdown briefing from the provided AI-authored plan instead of JSON",
    )
    daily_briefing_parser.add_argument(
        "--legacy-auto",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    add_common_output_flags(daily_briefing_parser)
    daily_briefing_parser.set_defaults(func=command_daily_briefing)

    create_task_parser = subparsers.add_parser("create-task", help="Create a task")
    create_task_parser.add_argument("--name", required=True)
    create_task_parser.add_argument("--workspace", help="Workspace GID override")
    create_task_parser.add_argument("--project", help="Project GID")
    create_task_parser.add_argument("--section", help="Section GID for placement")
    create_task_parser.add_argument("--parent", help="Parent task GID")
    create_task_parser.add_argument("--assignee", help="Assignee gid, me, exact cached user name, or exact cached email")
    create_task_parser.add_argument("--notes")
    create_task_parser.add_argument("--html-notes")
    create_task_parser.add_argument("--due-on")
    create_task_parser.add_argument("--due-at")
    create_task_parser.add_argument("--custom-field", action="append", default=[], help="Custom field as gid=value")
    create_task_parser.add_argument("--insert-before", help="Anchor task GID, section/project sentinel GID, or literal null")
    create_task_parser.add_argument("--insert-after", help="Anchor task GID, section/project sentinel GID, or literal null")
    add_common_output_flags(create_task_parser)
    create_task_parser.set_defaults(func=command_create_task)

    update_task_parser = subparsers.add_parser("update-task", help="Update a task")
    update_task_parser.add_argument("task_gid")
    update_task_parser.add_argument("--name")
    update_task_parser.add_argument("--project", help="Project GID for placement")
    update_task_parser.add_argument("--section", help="Section GID for placement")
    update_task_parser.add_argument("--assignee", help="Assignee gid, me, exact cached user name, or exact cached email")
    update_task_parser.add_argument("--notes")
    update_task_parser.add_argument("--html-notes")
    update_task_parser.add_argument("--due-on")
    update_task_parser.add_argument("--due-at")
    update_task_parser.add_argument("--completed", type=parse_bool)
    update_task_parser.add_argument("--custom-field", action="append", default=[], help="Custom field as gid=value")
    update_task_parser.add_argument("--insert-before", help="Anchor task GID, section/project sentinel GID, or literal null")
    update_task_parser.add_argument("--insert-after", help="Anchor task GID, section/project sentinel GID, or literal null")
    add_common_output_flags(update_task_parser)
    update_task_parser.set_defaults(func=command_update_task)

    comment_parser = subparsers.add_parser("comment-task", help="Create a task story/comment")
    comment_parser.add_argument("task_gid")
    comment_parser.add_argument("--text", help="Plain-text comment body")
    comment_parser.add_argument("--text-file", help="Read plain-text comment body from file")
    comment_parser.add_argument("--html-text", help="Rich-text HTML comment body")
    comment_parser.add_argument("--html-text-file", help="Read rich-text HTML comment body from file")
    add_common_output_flags(comment_parser)
    comment_parser.set_defaults(func=command_comment_task)

    update_story_parser = subparsers.add_parser("update-story", help="Update an existing story/comment")
    update_story_parser.add_argument("story_gid")
    update_story_parser.add_argument("--text", help="Plain-text comment body")
    update_story_parser.add_argument("--text-file", help="Read plain-text comment body from file")
    update_story_parser.add_argument("--html-text", help="Rich-text HTML comment body")
    update_story_parser.add_argument("--html-text-file", help="Read rich-text HTML comment body from file")
    add_common_output_flags(update_story_parser)
    update_story_parser.set_defaults(func=command_update_story)

    task_stories_parser = subparsers.add_parser("task-stories", help="List stories/comments on one or more tasks")
    task_stories_parser.add_argument("task_gids", nargs="+", help="Task gids; comma-separated values allowed")
    task_stories_parser.add_argument("--opt-fields", help="Override story fields")
    task_stories_parser.add_argument("--paginate", action="store_true", help="Follow Asana next_page links")
    task_stories_parser.add_argument("--limit-pages", type=int, default=0, help="Stop pagination after N pages")
    add_common_output_flags(task_stories_parser)
    task_stories_parser.set_defaults(func=command_task_stories)

    task_comments_parser = subparsers.add_parser("task-comments", help="List only comment stories on one or more tasks, including text and html_text")
    task_comments_parser.add_argument("task_gids", nargs="+", help="Task gids; comma-separated values allowed")
    task_comments_parser.add_argument("--opt-fields", help="Override story fields")
    task_comments_parser.add_argument("--paginate", action="store_true", help="Follow Asana next_page links")
    task_comments_parser.add_argument("--limit-pages", type=int, default=0, help="Stop pagination after N pages")
    add_common_output_flags(task_comments_parser)
    task_comments_parser.set_defaults(func=command_task_comments)

    task_projects_parser = subparsers.add_parser("task-projects", help="List projects one or more tasks belong to")
    task_projects_parser.add_argument("task_gids", nargs="+", help="Task gids; comma-separated values allowed")
    task_projects_parser.add_argument("--opt-fields", help="Override project fields")
    add_common_output_flags(task_projects_parser)
    task_projects_parser.set_defaults(func=command_task_projects)

    add_task_project_parser = subparsers.add_parser("add-task-project", help="Add or move a task within a project/section")
    add_task_project_parser.add_argument("task_gid")
    add_task_project_parser.add_argument("project_gid")
    add_task_project_parser.add_argument("--section", help="Section GID for placement")
    add_task_project_parser.add_argument("--insert-before", help="Anchor task GID or literal null")
    add_task_project_parser.add_argument("--insert-after", help="Anchor task GID or literal null")
    add_common_output_flags(add_task_project_parser)
    add_task_project_parser.set_defaults(func=command_add_task_project)

    remove_task_project_parser = subparsers.add_parser("remove-task-project", help="Remove a task from a project")
    remove_task_project_parser.add_argument("task_gid")
    remove_task_project_parser.add_argument("project_gid")
    add_common_output_flags(remove_task_project_parser)
    remove_task_project_parser.set_defaults(func=command_remove_task_project)

    add_task_followers_parser = subparsers.add_parser("add-task-followers", help="Add followers to a task")
    add_task_followers_parser.add_argument("task_gid")
    add_task_followers_parser.add_argument("followers", nargs="+", help="Follower gids, me, exact cached user names/emails; comma-separated values allowed")
    add_common_output_flags(add_task_followers_parser)
    add_task_followers_parser.set_defaults(func=command_add_task_followers)

    remove_task_followers_parser = subparsers.add_parser("remove-task-followers", help="Remove followers from a task")
    remove_task_followers_parser.add_argument("task_gid")
    remove_task_followers_parser.add_argument("followers", nargs="+", help="Follower gids, me, exact cached user names/emails; comma-separated values allowed")
    add_common_output_flags(remove_task_followers_parser)
    remove_task_followers_parser.set_defaults(func=command_remove_task_followers)

    tags_parser = subparsers.add_parser("tags", help="List tags in a workspace")
    tags_parser.add_argument("--workspace", help="Workspace GID override")
    tags_parser.add_argument("--opt-fields", help="Override tag fields")
    tags_parser.add_argument("--paginate", action="store_true", help="Follow Asana next_page links")
    tags_parser.add_argument("--limit-pages", type=int, default=0, help="Stop pagination after N pages")
    add_common_output_flags(tags_parser)
    tags_parser.set_defaults(func=command_tags)

    create_tag_parser = subparsers.add_parser("create-tag", help="Create a tag")
    create_tag_parser.add_argument("--name", required=True)
    create_tag_parser.add_argument("--workspace", help="Workspace GID override")
    create_tag_parser.add_argument("--color", help="Tag color")
    create_tag_parser.add_argument("--notes")
    add_common_output_flags(create_tag_parser)
    create_tag_parser.set_defaults(func=command_create_tag)

    workspace_custom_fields_parser = subparsers.add_parser("workspace-custom-fields", help="List custom fields in a workspace")
    workspace_custom_fields_parser.add_argument("--workspace", help="Workspace GID override")
    workspace_custom_fields_parser.add_argument("--opt-fields", help="Override field list")
    workspace_custom_fields_parser.add_argument("--paginate", action="store_true", help="Follow Asana next_page links")
    workspace_custom_fields_parser.add_argument("--limit-pages", type=int, default=0, help="Stop pagination after N pages")
    add_common_output_flags(workspace_custom_fields_parser)
    workspace_custom_fields_parser.set_defaults(func=command_workspace_custom_fields)

    team_custom_fields_parser = subparsers.add_parser("team-custom-fields", help="List team custom field settings")
    team_custom_fields_parser.add_argument("--team", help="Team GID or exact team name from asana-context.json")
    team_custom_fields_parser.add_argument("--opt-fields", help="Override field list")
    team_custom_fields_parser.add_argument("--paginate", action="store_true", help="Follow Asana next_page links")
    team_custom_fields_parser.add_argument("--limit-pages", type=int, default=0, help="Stop pagination after N pages")
    add_common_output_flags(team_custom_fields_parser)
    team_custom_fields_parser.set_defaults(func=command_team_custom_fields)

    project_custom_fields_parser = subparsers.add_parser("project-custom-fields", help="List custom field settings on one or more projects")
    project_custom_fields_parser.add_argument("project_gids", nargs="+", help="Project gids; comma-separated values allowed")
    project_custom_fields_parser.add_argument("--opt-fields", help="Override field list")
    add_common_output_flags(project_custom_fields_parser)
    project_custom_fields_parser.set_defaults(func=command_project_custom_fields)

    task_custom_fields_parser = subparsers.add_parser("task-custom-fields", help="List custom fields on one or more tasks")
    task_custom_fields_parser.add_argument("task_gids", nargs="+", help="Task gids; comma-separated values allowed")
    task_custom_fields_parser.add_argument("--opt-fields", help="Override field list")
    add_common_output_flags(task_custom_fields_parser)
    task_custom_fields_parser.set_defaults(func=command_task_custom_fields)

    create_custom_field_parser = subparsers.add_parser("create-custom-field", help="Create a workspace custom field")
    create_custom_field_parser.add_argument("--name", required=True)
    create_custom_field_parser.add_argument("--workspace", help="Workspace GID override")
    create_custom_field_parser.add_argument(
        "--resource-subtype",
        required=True,
        help="Asana custom field subtype, e.g. text, number, enum, multi_enum",
    )
    create_custom_field_parser.add_argument("--description")
    create_custom_field_parser.add_argument("--precision", type=int)
    create_custom_field_parser.add_argument("--enum-option", action="append", help="Enum option name; repeat for multiple values")
    add_common_output_flags(create_custom_field_parser)
    create_custom_field_parser.set_defaults(func=command_create_custom_field)

    task_tags_parser = subparsers.add_parser("task-tags", help="List tags on one or more tasks")
    task_tags_parser.add_argument("task_gids", nargs="+", help="Task gids; comma-separated values allowed")
    task_tags_parser.add_argument("--opt-fields", help="Override tag fields")
    add_common_output_flags(task_tags_parser)
    task_tags_parser.set_defaults(func=command_task_tags)

    add_task_tag_parser = subparsers.add_parser("add-task-tag", help="Add a tag to a task")
    add_task_tag_parser.add_argument("task_gid")
    add_task_tag_parser.add_argument("tag_gid", help="Tag gid or exact tag name")
    add_task_tag_parser.add_argument("--workspace", help="Workspace GID override for tag-name resolution")
    add_common_output_flags(add_task_tag_parser)
    add_task_tag_parser.set_defaults(func=command_add_task_tag)

    remove_task_tag_parser = subparsers.add_parser("remove-task-tag", help="Remove a tag from a task")
    remove_task_tag_parser.add_argument("task_gid")
    remove_task_tag_parser.add_argument("tag_gid", help="Tag gid or exact tag name")
    remove_task_tag_parser.add_argument("--workspace", help="Workspace GID override for tag-name resolution")
    add_common_output_flags(remove_task_tag_parser)
    remove_task_tag_parser.set_defaults(func=command_remove_task_tag)

    set_task_sorting_tag_parser = subparsers.add_parser(
        "set-task-sorting-tag",
        help="Set exactly one sorting tag on a task while preserving unrelated tags",
    )
    set_task_sorting_tag_parser.add_argument("task_gid")
    set_task_sorting_tag_parser.add_argument("sorting_tag", help="Desired sorting tag gid or exact tag name")
    set_task_sorting_tag_parser.add_argument("--workspace", help="Workspace GID override for tag-name resolution")
    set_task_sorting_tag_parser.add_argument(
        "--sorting-label",
        action="append",
        help="Allowed sorting tag name; repeat to override the default sorter set",
    )
    add_common_output_flags(set_task_sorting_tag_parser)
    set_task_sorting_tag_parser.set_defaults(func=command_set_task_sorting_tag)

    add_task_dependencies_parser = subparsers.add_parser("add-task-dependencies", help="Add dependencies to a task")
    add_task_dependencies_parser.add_argument("task_gid")
    add_task_dependencies_parser.add_argument("dependencies", nargs="+", help="Dependency task gids; comma-separated values allowed")
    add_common_output_flags(add_task_dependencies_parser)
    add_task_dependencies_parser.set_defaults(func=command_add_task_dependencies)

    remove_task_dependencies_parser = subparsers.add_parser("remove-task-dependencies", help="Remove dependencies from a task")
    remove_task_dependencies_parser.add_argument("task_gid")
    remove_task_dependencies_parser.add_argument("dependencies", nargs="+", help="Dependency task gids; comma-separated values allowed")
    add_common_output_flags(remove_task_dependencies_parser)
    remove_task_dependencies_parser.set_defaults(func=command_remove_task_dependencies)

    batch_parser = subparsers.add_parser("batch", help="Run an Asana batch request from a JSON array of actions")
    batch_parser.add_argument("--actions", help="Inline JSON array of batch actions")
    batch_parser.add_argument("--actions-file", help="Path to a JSON file containing a batch actions array")
    add_common_output_flags(batch_parser)
    batch_parser.set_defaults(func=command_batch)

    create_project_parser = subparsers.add_parser("create-project", help="Create a project")
    create_project_parser.add_argument("--name", required=True)
    create_project_parser.add_argument("--team", help="Team GID or exact team name from asana-context.json")
    create_project_parser.add_argument("--workspace", help="Workspace GID override")
    create_project_parser.add_argument("--notes")
    add_common_output_flags(create_project_parser)
    create_project_parser.set_defaults(func=command_create_project)

    update_project_parser = subparsers.add_parser("update-project", help="Update a project")
    update_project_parser.add_argument("project_gid")
    update_project_parser.add_argument("--name")
    update_project_parser.add_argument("--notes")
    update_project_parser.add_argument("--archived", type=parse_bool)
    add_common_output_flags(update_project_parser)
    update_project_parser.set_defaults(func=command_update_project)

    context_parser = subparsers.add_parser("show-context", help="Print local workspace/team defaults")
    add_common_output_flags(context_parser)
    context_parser.set_defaults(func=command_show_context)

    cache_parser = subparsers.add_parser("show-cache", help="Print the local Asana entity cache")
    add_common_output_flags(cache_parser)
    cache_parser.set_defaults(func=command_show_cache)

    trigger_rule_parser = subparsers.add_parser(
        "trigger-rule",
        help="Trigger an existing Asana rule that has a 'web request received' trigger",
    )
    trigger_rule_parser.add_argument("trigger_identifier", help="Trigger identifier from the Asana rule's incoming web request URL")
    trigger_rule_parser.add_argument("--task", dest="task_gid", required=True, help="Task GID where rule actions will be performed")
    trigger_rule_parser.add_argument("--action-data", action="append", metavar="KEY=VALUE", help="Custom key=value data available as dynamic variables in rule actions (repeatable)")
    add_common_output_flags(trigger_rule_parser)
    trigger_rule_parser.set_defaults(func=command_trigger_rule)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
