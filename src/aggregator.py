from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .utils import correlation, ensure_dir, read_json, sentiment_band, write_json


def _compute_daily_sentiment(analyzed_items: List[Dict[str, Any]]) -> Tuple[float, Dict[str, int]]:
    if not analyzed_items:
        return 0.0, {"bull": 0, "bear": 0, "neutral": 0}
    bull = sum(float(it.get("confidence", 0.0)) for it in analyzed_items if it.get("sentiment") == "bull")
    bear = sum(float(it.get("confidence", 0.0)) for it in analyzed_items if it.get("sentiment") == "bear")
    total = len(analyzed_items)
    idx = (bull - bear) / float(total)
    counts = {
        "bull": sum(1 for it in analyzed_items if it.get("sentiment") == "bull"),
        "bear": sum(1 for it in analyzed_items if it.get("sentiment") == "bear"),
        "neutral": sum(1 for it in analyzed_items if it.get("sentiment") == "neutral"),
    }
    return float(max(-1.0, min(1.0, idx))), counts


def upsert_symbol_day(
    *,
    data_dir: Path,
    symbol: Dict[str, Any],
    date: str,
    kline: List[Dict[str, Any]],
    analyzed_news: List[Dict[str, Any]],
    tz_label: str,
) -> Dict[str, Any]:
    symbol_dir = data_dir / "symbols" / symbol["id"]
    ensure_dir(symbol_dir / "days")

    sentiment_index, sentiment_counts = _compute_daily_sentiment(analyzed_news)
    today_bar = next((x for x in reversed(kline) if x.get("date") == date), kline[-1] if kline else None)
    if not today_bar:
        raise RuntimeError("missing kline")
    prev_bar = kline[-2] if len(kline) >= 2 else None
    pct = 0.0
    if prev_bar and float(prev_bar["close"]) != 0.0:
        pct = (float(today_bar["close"]) - float(prev_bar["close"])) / float(prev_bar["close"]) * 100.0

    day_payload = {
        "symbol": {"id": symbol["id"], "name": symbol["name"]},
        "date": date,
        "updated_at": tz_label,
        "sentiment": {
            "index": sentiment_index,
            "band": sentiment_band(sentiment_index),
            "counts": sentiment_counts,
            "news_total": len(analyzed_news),
        },
        "price": {
            **{k: today_bar[k] for k in ["open", "high", "low", "close", "volume", "open_interest", "date"]},
            "pct_change": round(pct, 2),
        },
        "news": analyzed_news,
        "kline": kline,
    }
    write_json(symbol_dir / "days" / f"{date}.json", day_payload)

    history_path = symbol_dir / "history.json"
    history = read_json(history_path, default={"symbol": {"id": symbol["id"], "name": symbol["name"]}, "days": []})
    days = {d["date"]: d for d in (history.get("days", []) or [])}
    days[date] = {
        "date": date,
        "sentiment": sentiment_index,
        "open": float(today_bar["open"]),
        "high": float(today_bar["high"]),
        "low": float(today_bar["low"]),
        "close": float(today_bar["close"]),
        "volume": int(today_bar["volume"]),
        "open_interest": int(today_bar["open_interest"]),
        "pct_change": round(pct, 2),
    }
    history["days"] = sorted(days.values(), key=lambda x: x["date"])
    write_json(history_path, history)

    # exports
    export_dir = data_dir / "exports"
    ensure_dir(export_dir)
    export_path = export_dir / f"{symbol['id']}.csv"
    with open(export_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["date", "sentiment", "close", "volume", "open_interest", "pct_change"],
        )
        w.writeheader()
        for row in history["days"]:
            w.writerow(
                {
                    "date": row["date"],
                    "sentiment": row["sentiment"],
                    "close": row["close"],
                    "volume": row["volume"],
                    "open_interest": row["open_interest"],
                    "pct_change": row["pct_change"],
                }
            )
    return day_payload


def write_latest(data_dir: Path, date: str, tz_label: str, symbols: List[Dict[str, Any]]) -> Dict[str, Any]:
    latest = {
        "date": date,
        "updated_at": tz_label,
        "symbols": [],
    }
    for sym in symbols:
        sym_id = sym["id"]
        day_date = date
        stale = False

        day_path = data_dir / "symbols" / sym_id / "days" / f"{date}.json"
        day = read_json(day_path, default=None)
        if not day:
            # Fail-soft: keep the latest available day
            hist = read_json(data_dir / "symbols" / sym_id / "history.json", default=None) or {}
            hist_days = hist.get("days", []) or []
            if hist_days:
                day_date = hist_days[-1]["date"]
                day = read_json(data_dir / "symbols" / sym_id / "days" / f"{day_date}.json", default=None)
                stale = True
        if not day:
            continue
        latest["symbols"].append(
            {
                "id": sym_id,
                "name": sym["name"],
                "sentiment_index": day["sentiment"]["index"],
                "sentiment_band": day["sentiment"]["band"],
                "pct_change": day["price"]["pct_change"],
                "close": day["price"]["close"],
                "updated_at": tz_label,
                "data_date": day_date,
                "is_stale": stale,
            }
        )
    write_json(data_dir / "latest.json", latest)
    return latest


def compute_corr20(history_days: List[Dict[str, Any]]) -> float:
    if len(history_days) < 22:
        return 0.0
    h = history_days[-21:]
    s = [float(x["sentiment"]) for x in h[:-1]]
    next_ret = []
    for i in range(len(h) - 1):
        c0 = float(h[i]["close"]) or 0.0
        c1 = float(h[i + 1]["close"]) or 0.0
        next_ret.append(0.0 if c0 == 0.0 else (c1 - c0) / c0)
    return correlation(s[-20:], next_ret[-20:])
