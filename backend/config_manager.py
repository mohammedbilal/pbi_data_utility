import json
from pathlib import Path
from typing import Any, Dict

from paths import BACKEND_DIR as _BACKEND_DIR, STATE_DIR

# Settings are written at runtime (Settings tab), so they live in the state dir.
# environments.json itself is NOT in git — it holds credentials, and a tracked copy
# means every developer conflicts with every other one on the file they must edit to
# run anything. What is tracked is environments.example.json (no passwords, and a
# ready-made "Local" entry). First start seeds from whichever of these exists.
ENVIRONMENTS_PATH = STATE_DIR / "environments.json"
SEEDS = (_BACKEND_DIR / "environments.json",          # a local, untracked copy
         _BACKEND_DIR / "environments.example.json")  # the tracked template
BACKEND_DIR = str(_BACKEND_DIR)


def _resolve(data: Any) -> Any:
    """Recursively replace {BACKEND_DIR} placeholders with the real path."""
    if isinstance(data, str):
        return data.replace("{BACKEND_DIR}", BACKEND_DIR)
    if isinstance(data, dict):
        return {k: _resolve(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_resolve(v) for v in data]
    return data


def load_environments() -> Dict[str, Any]:
    if not ENVIRONMENTS_PATH.exists():
        seed = next((s for s in SEEDS if s.exists() and s != ENVIRONMENTS_PATH), None)
        if seed is None:
            return _resolve(_defaults())
        ENVIRONMENTS_PATH.write_bytes(seed.read_bytes())
    with ENVIRONMENTS_PATH.open("r", encoding="utf-8") as f:
        return _resolve(json.load(f))


def save_environments(data: Dict[str, Any]) -> None:
    with ENVIRONMENTS_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_active_env(data: Dict[str, Any]) -> Dict[str, Any]:
    active = data.get("active", "")
    return data.get("environments", {}).get(active, {})


def _defaults() -> Dict[str, Any]:
    return {
        "active": "QA",
        "environments": {
            "QA": {
                "host_name": "qa-trowe-pbi2.cddev.genesis.global",
                "verify_ssl": True,
                "credentials": {
                    "bonds_loans": {"username": "RahulK", "password": ""},
                    "interest": {"username": "TRPUser_QA", "password": ""},
                },
            }
        },
        "reference_dirs": {
            "bonds": "C:\\python\\LATEST_PBI_JSON\\reference",
            "loans": "C:\\python\\PBI_LOAN_JSON_PUBLISHER_V1\\reference_data",
        },
        "tool_defaults": {
            "bonds": {"single": 0, "multi": 1, "tranches_per_multi": [3, 8], "sleep_ms": 100},
            "loans": {"single": 3, "multi": 0, "delay": 15, "tranches_per_multi": [2, 3], "deal_query_wait": 6},
            "interest": {
                "tranche_name": "12dbb95e-6d83-47e5-a9d9-2d416fae07d7",
                "is_modelled_true": 9,
                "is_modelled_false": 5,
                "pair_closeness_pct": 2.0,
                "total_mismatch_pct": 0.0,
                "delay_seconds": 4,
                "strategy": False,
                "is_modelled_as_string": True,
                "sizing": {"min_piece": 10000, "increment_size": 100000},
                "quantity_ranges": {
                    "true_min": 100000,
                    "true_max": 1200000,
                    "false_min": 600000,
                    "false_max": 1600000,
                },
                "event_defaults": {
                    "MARKET_TYPE": "Market",
                    "ORDER_LIMIT": None,
                    "TRADE_DESK": None,
                    "ENTITY": "TRPA",
                    "COMMENT": "V15 Or vs St",
                    "REGISTRATION_TYPE": None,
                    "REGULATION_SUBCATEGORY": None,
                },
            },
        },
    }
