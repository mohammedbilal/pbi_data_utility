import json

from fastapi import APIRouter, File, Form, UploadFile

from engines.csv_engine import run_csv_upload
from routers._shared import get_tool_status, start_tool_run, stop_tool_run, stream_tool_run

router = APIRouter()


@router.post("/run")
async def start(
    file: UploadFile = File(...),
    params_json: str = Form(...),
):
    params = json.loads(params_json)
    params["file_bytes"] = await file.read()
    params["file_name"] = file.filename
    return start_tool_run("csv_upload", params, run_csv_upload)


@router.get("/stream/{run_id}")
async def stream(run_id: str):
    return stream_tool_run(run_id)


@router.post("/stop/{run_id}")
async def stop(run_id: str):
    return stop_tool_run(run_id)


@router.get("/status/{run_id}")
async def status_check(run_id: str):
    return get_tool_status(run_id)
