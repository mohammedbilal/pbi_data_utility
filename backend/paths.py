"""Where the app keeps its mutable state.

Everything the app *writes* (settings, run history, the expectation store)
lives under one directory so a container can mount it and keep it across
rebuilds. The default is the backend folder itself, which is exactly where
those files have always lived — set ``PBI_STATE_DIR`` only to move them
(``docker-compose.yml`` points it at a bind-mounted ``/app/data``).
"""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).parent
STATE_DIR = Path(os.environ.get("PBI_STATE_DIR") or BACKEND_DIR)
STATE_DIR.mkdir(parents=True, exist_ok=True)
