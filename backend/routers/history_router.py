from fastapi import APIRouter
from typing import Any, Dict, List

from history_manager import add_run, list_runs

router = APIRouter()


@router.get("")
async def get_history() -> List[Dict[str, Any]]:
    return list_runs()


@router.post("")
async def post_history(record: Dict[str, Any]) -> Dict[str, Any]:
    return add_run(record)
