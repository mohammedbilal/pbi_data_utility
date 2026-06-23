import queue
import threading
import uuid
from typing import Any, Dict, Optional

_runs: Dict[str, Dict[str, Any]] = {}
_active: Dict[str, str] = {}  # tool -> run_id


def create_run(tool: str) -> str:
    run_id = str(uuid.uuid4())
    _runs[run_id] = {
        "tool": tool,
        "queue": queue.Queue(),
        "stop_event": threading.Event(),
        "status": "running",
        "thread": None,
    }
    return run_id


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    return _runs.get(run_id)


def stop_run(run_id: str) -> bool:
    run = _runs.get(run_id)
    if run:
        run["stop_event"].set()
        run["status"] = "stopping"
        return True
    return False


def mark_done(run_id: str) -> None:
    if run_id in _runs:
        _runs[run_id]["status"] = "done"


def get_active_run(tool: str) -> Optional[str]:
    return _active.get(tool)


def set_active_run(tool: str, run_id: str) -> None:
    _active[tool] = run_id


def clear_active_run(tool: str) -> None:
    _active.pop(tool, None)


def is_tool_running(tool: str) -> bool:
    run_id = _active.get(tool)
    if not run_id:
        return False
    run = _runs.get(run_id)
    return run is not None and run["status"] == "running"
