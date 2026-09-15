"""Shared SSE streaming and stop logic used by all tool routers."""
from __future__ import annotations

import asyncio
import json
import queue as queue_module
import threading
from typing import Any, Callable, Dict

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from run_manager import (
    clear_active_run, create_run, get_active_run, get_run,
    is_tool_running, mark_done, set_active_run, stop_run,
)
from config_manager import get_active_env, load_environments

_TIMEOUT_SENTINEL = object()


def start_tool_run(tool: str, params: Dict[str, Any], engine_fn: Callable) -> Dict[str, str]:
    if is_tool_running(tool):
        raise HTTPException(409, f"A {tool} run is already in progress. Stop it first.")

    envs = load_environments()
    env = get_active_env(envs)
    ref_dirs = envs.get("reference_dirs", {})

    if tool == "bonds":
        params.setdefault("ref_dir", ref_dirs.get("bonds", ""))
    elif tool == "loans":
        params.setdefault("ref_dir", ref_dirs.get("loans", ""))
    elif tool == "securitized":
        params.setdefault("ref_dir", ref_dirs.get("securitized", ""))
    elif tool == "munis":
        params.setdefault("ref_dir", ref_dirs.get("munis", ""))

    # Email config (recipient / save-copy dir) lives top-level in
    # environments.json and is injected server-side, so the frontend only sends
    # the per-run email_mode / email_format choices. See spec §17.
    if tool in ("bonds", "loans"):
        params.setdefault("email", envs.get("email", {}))

    # Expectation-capture config (store path, expected datasource, auto_capture)
    # lives in the top-level `compare` block, same as `email` above. The engine
    # also wants the environment's display name for the util_run record, which
    # the env dict itself does not carry. See spec §18.10.
    if tool == "bonds":
        params.setdefault("compare", envs.get("compare", {}))
        params.setdefault("env_name", envs.get("active", ""))

    run_id = create_run(tool)
    set_active_run(tool, run_id)
    run = get_run(run_id)

    def target() -> None:
        try:
            engine_fn(params, env, run["stop_event"], run["queue"])
        finally:
            run["queue"].put(None)
            mark_done(run_id)
            clear_active_run(tool)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    run["thread"] = t
    return {"run_id": run_id}


def stream_tool_run(run_id: str) -> StreamingResponse:
    run = get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    q = run["queue"]

    async def generate():
        loop = asyncio.get_event_loop()
        while True:
            def _get():
                try:
                    return q.get(block=True, timeout=0.5)
                except queue_module.Empty:
                    return _TIMEOUT_SENTINEL

            result = await loop.run_in_executor(None, _get)

            if result is _TIMEOUT_SENTINEL:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"
            elif result is None:
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                break
            else:
                yield f"data: {json.dumps(result)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def stop_tool_run(run_id: str) -> Dict[str, str]:
    if stop_run(run_id):
        return {"status": "stopping"}
    raise HTTPException(404, "Run not found")


def get_tool_status(run_id: str) -> Dict[str, str]:
    run = get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return {"status": run["status"]}
