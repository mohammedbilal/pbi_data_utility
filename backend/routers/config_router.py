from fastapi import APIRouter
from typing import Any, Dict

from config_manager import load_environments, save_environments

router = APIRouter()


@router.get("")
async def get_config() -> Dict[str, Any]:
    return load_environments()


@router.put("")
async def save_config(data: Dict[str, Any]) -> Dict[str, str]:
    save_environments(data)
    return {"status": "saved"}
