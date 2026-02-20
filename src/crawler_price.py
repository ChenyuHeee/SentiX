from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List


def _seed(symbol_id: str) -> int:
    h = hashlib.sha256(symbol_id.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def fetch_kline(cfg: Dict[str, Any], symbol: Dict[str, Any], end_date: str, days: int) -> List[Dict[str, Any]]:
    provider = (cfg.get("price", {}) or {}).get("provider", "mock")
    if provider != "mock":
        provider = "mock"

    rng = random.Random(_seed(symbol["id"]))
    end = datetime.strptime(end_date, "%Y-%m-%d")
    start = end - timedelta(days=days * 2)

    d = start
    trading_days: List[datetime] = []
    while d <= end:
        if d.weekday() < 5:
            trading_days.append(d)
        d += timedelta(days=1)
    trading_days = trading_days[-days:]

    price = 100.0 + rng.random() * 50
    out: List[Dict[str, Any]] = []
    for dt in trading_days:
        drift = rng.uniform(-1.2, 1.2)
        vol = rng.uniform(0.4, 1.8)
        o = price
        c = max(1.0, price + drift)
        h = max(o, c) + rng.random() * vol
        l = max(0.5, min(o, c) - rng.random() * vol)
        v = int(rng.uniform(8000, 45000))
        oi = int(rng.uniform(40000, 160000))
        out.append(
            {
                "date": dt.strftime("%Y-%m-%d"),
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
                "volume": v,
                "open_interest": oi,
            }
        )
        price = c
    return out
