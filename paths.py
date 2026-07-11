"""Where Otlobly keeps its WRITABLE data.

Locally this is just the project folder, so nothing changes for the local
dashboard. In a hosted deploy, set OTLOBLY_DATA_DIR to a PERSISTENT disk
(e.g. a Render disk mounted at /var/data) so the SQLite DB, the purchase/trash
stores, the activity log, and uploaded ID/PO images survive restarts and
redeploys. Static config (config.json) stays in the code folder and ships in
the repo — it's read, not written, at runtime.
"""
import json
import os
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ENV = os.environ.get("OTLOBLY_DATA_DIR")
DATA_DIR = Path(_ENV).resolve() if _ENV else _HERE
if _ENV:                                  # ensure the mounted disk dir exists
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def data_path(name):
    """Absolute path to a writable data file/dir under DATA_DIR."""
    return DATA_DIR / name


def write_json_atomic(path, obj, *, indent=2):
    """Write `obj` as JSON to `path` crash-safely: serialise to a temp file in the
    SAME directory, then os.replace() over the target. os.replace is atomic on
    POSIX, so a crash (or a second worker) can never leave a half-written,
    corrupt store — readers see either the old file or the new one, never a
    truncated one. (Same tmp + os.replace pattern as meta.py / tracking.py.)"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())          # durable before the swap
        os.replace(tmp, path)             # atomic rename over the target
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
