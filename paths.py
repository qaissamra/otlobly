"""Where Otlobly keeps its WRITABLE data.

Locally this is just the project folder, so nothing changes for the local
dashboard. In a hosted deploy, set OTLOBLY_DATA_DIR to a PERSISTENT disk
(e.g. a Render disk mounted at /var/data) so the SQLite DB, the purchase/trash
stores, the activity log, and uploaded ID/PO images survive restarts and
redeploys. Static config (config.json) stays in the code folder and ships in
the repo — it's read, not written, at runtime.
"""
import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ENV = os.environ.get("OTLOBLY_DATA_DIR")
DATA_DIR = Path(_ENV).resolve() if _ENV else _HERE
if _ENV:                                  # ensure the mounted disk dir exists
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def data_path(name):
    """Absolute path to a writable data file/dir under DATA_DIR."""
    return DATA_DIR / name
