from __future__ import annotations

from datetime import datetime
from typing import Any, Dict


def fetch_extras(cfg: Dict[str, Any], symbol: Dict[str, Any], date: str) -> Dict[str, Any]:
    """Return placeholder payloads for future extensions.

    Keep the output schema stable so the frontend can render something
    without breaking when real data sources are added later.
    """

    _ = cfg
    _ = symbol

    # date: YYYY-MM-DD
    try:
        date_compact = datetime.strptime(date, "%Y-%m-%d").strftime("%Y%m%d")
    except Exception:
        date_compact = ""

    return {
        "status": "placeholder",
        "asof": date,
        "modules": {
            "inventory": {
                "status": "placeholder",
                "hint": "仓单/库存（计划：AKShare futures_inventory_em 等）",
                "items": [],
            },
            "spot_basis": {
                "status": "placeholder",
                "hint": "现货/基差（计划：AKShare futures_spot_price 等）",
                "items": [],
            },
            "roll_yield": {
                "status": "placeholder",
                "hint": "展期收益率（计划：AKShare get_roll_yield）",
                "items": [],
            },
            "positions_rank": {
                "status": "placeholder",
                "hint": "会员持仓/成交排名（计划：AKShare futures_hold_pos_sina 等）",
                "items": [],
                "params": {"date": date_compact},
            },
        },
    }
