"""Resolve the KB schema and entity paths.

Engine ships scout/kb/schema.yaml as a default; users may override
by placing their own schema at $SCOUT_DATA_DIR/knowledge-base/ontology/schema.yaml.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from importlib.resources import as_file, files
from pathlib import Path

from scout import paths

# Process-lifetime cache of the materialized packaged schema (see
# _packaged_schema_path). None until first resolved.
_CACHED_PACKAGED_SCHEMA: Path | None = None


def _packaged_schema_path() -> Path:
    """Materialize the packaged ``scout/kb/schema.yaml`` to a stable path.

    ``importlib.resources.as_file`` only guarantees the extracted file exists
    *inside* the ``with`` block. For a zipped/wheel install the temp file is
    removed on context exit, so returning ``Path(p)`` from inside the block
    yields a path that's already deleted by the time the caller opens it (#39).

    Read the bytes while the resource is live, copy them to a process-lifetime
    temp file (cleaned up at interpreter exit), and return that. For a
    filesystem (editable) install the copy is a few-KB no-cost safety net.
    The result is cached so repeated calls reuse the same file.
    """
    global _CACHED_PACKAGED_SCHEMA
    if _CACHED_PACKAGED_SCHEMA is not None and _CACHED_PACKAGED_SCHEMA.exists():
        return _CACHED_PACKAGED_SCHEMA

    resource = files("scout") / "kb" / "schema.yaml"
    with as_file(resource) as p:
        data = Path(p).read_bytes()  # read while the resource is guaranteed live

    tmp_dir = Path(tempfile.mkdtemp(prefix="scout-schema-"))
    atexit.register(shutil.rmtree, tmp_dir, ignore_errors=True)
    dest = tmp_dir / "schema.yaml"
    dest.write_bytes(data)
    _CACHED_PACKAGED_SCHEMA = dest
    return dest


def resolve_schema_path(data: Path | None = None) -> Path:
    """Return the path to the active KB schema.

    Precedence: user override at
    $SCOUT_DATA_DIR/knowledge-base/ontology/schema.yaml, else the
    packaged default in scout/kb/schema.yaml.

    The returned path is stable for the lifetime of the process — for the
    packaged default it points at a materialized copy, so it stays valid
    under a wheel install where the importlib.resources extraction would
    otherwise be torn down before the caller reads it (#39).
    """
    user = paths.kb_dir(data) / "ontology" / "schema.yaml"
    if user.exists():
        return user
    return _packaged_schema_path()
