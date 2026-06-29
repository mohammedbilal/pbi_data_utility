from fastapi import APIRouter
from typing import Any, Dict

from routers._shared import start_tool_run, stream_tool_run, stop_tool_run, get_tool_status
from engines.tig_engine import run_tig_orders

router = APIRouter()


@router.post("/run")
async def start(params: Dict[str, Any]):
    return start_tool_run("tig_orders", params, run_tig_orders)


@router.get("/stream/{run_id}")
async def stream(run_id: str):
    return stream_tool_run(run_id)


@router.post("/stop/{run_id}")
async def stop(run_id: str):
    return stop_tool_run(run_id)


@router.get("/status/{run_id}")
async def status(run_id: str):
    return get_tool_status(run_id)
