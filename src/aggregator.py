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
    extras: Dict[str, Any] | None = None,
    agents: Dict[str, Any] | None = None,
    plans: Dict[str, Any] | None = None,
    tz_label: str,
) -> Dict[str, Any]:
    symbol_dir = data_dir / "symbols" / symbol["id"]
    ensure_dir(symbol_dir / "days")

    sentiment_index, sentiment_counts = _compute_daily_sentiment(analyzed_news)
    kline_dates = {x.get("date") for x in (kline or []) if x.get("date")}
    is_trading_day = bool(kline) and (date in kline_dates)
    today_bar: Dict[str, Any] | None = None
    price_asof = ""
    is_price_stale = False
    pct: float | None = None

    if kline:
        today_bar = next((x for x in reversed(kline) if x.get("date") == date), kline[-1])
        price_asof = str(today_bar.get("date") or "")
        is_price_stale = (not is_trading_day) and bool(price_asof) and (price_asof != date)
        if is_price_stale:
            logging.info(
                "Market closed on %s for %s; keep news but use price asof %s",
                date,
                symbol.get("id"),
                price_asof,
            )

        prev_bar = kline[-2] if len(kline) >= 2 else None
        if price_asof:
            # Find the previous trading bar for pct_change.
            for i in range(len(kline) - 1, 0, -1):
                if kline[i].get("date") == price_asof:
                    prev_bar = kline[i - 1]
                    break
        if prev_bar and float(prev_bar.get("close") or 0.0) != 0.0:
            pct = (float(today_bar.get("close") or 0.0) - float(prev_bar.get("close") or 0.0)) / float(prev_bar.get("close") or 1.0) * 100.0

    day_payload = {
        "symbol": {"id": symbol["id"], "name": symbol["name"]},
        "date": date,
        "updated_at": tz_label,
        "is_stale": bool(is_price_stale),
        "agents": agents,
        "plans": plans,
        "sentiment": {
            "index": sentiment_index,
            "band": sentiment_band(sentiment_index),
            "counts": sentiment_counts,
            "news_total": len(analyzed_news),
        },
        "price": (
            {
                "status": "ok",
                **{k: (today_bar or {}).get(k) for k in ["open", "high", "low", "close", "volume", "open_interest", "date"]},
                "is_stale": bool(is_price_stale),
                "pct_change": None if pct is None else round(float(pct), 2),
            }
            if kline
            else {
                "status": "unavailable",
                "reason": "missing kline",
                "is_stale": False,
                "pct_change": None,
                "date": "",
                "open": None,
                "high": None,
                "low": None,
                "close": None,
                "volume": None,
                "open_interest": None,
            }
        ),
        "news": analyzed_news,
        "kline": kline or [],
        "extras": extras or {"status": "missing", "asof": date, "modules": {}},
    }
    # Always write the requested date so non-trading days can still show news.
    write_json(symbol_dir / "days" / f"{date}.json", day_payload)

    history_path = symbol_dir / "history.json"
    history = read_json(history_path, default={"symbol": {"id": symbol["id"], "name": symbol["name"]}, "days": []})
    existing_days = history.get("days", []) or []
    # Cleanup: drop previously written non-trading dates within the fetched
    # kline window. Keep older history outside the current window.
    if kline_dates:
        min_kline_date = min(kline_dates)
        existing_days = [
            d
            for d in existing_days
            if (str(d.get("date") or "") < min_kline_date) or (d.get("date") in kline_dates)
        ]
    days = {d["date"]: d for d in existing_days}

    # Only update trading-day history/CSV. Non-trading days keep their day.json
    # (news/sentiment) but do not add a new bar into history.
    if is_trading_day and today_bar is not None:
        days[date] = {
            "date": date,
            "sentiment": sentiment_index,
            "open": float(today_bar.get("open") or 0.0),
            "high": float(today_bar.get("high") or 0.0),
            "low": float(today_bar.get("low") or 0.0),
            "close": float(today_bar.get("close") or 0.0),
            "volume": int(today_bar.get("volume") or 0),
            "open_interest": int(today_bar.get("open_interest") or 0),
            "pct_change": 0.0 if pct is None else round(float(pct), 2),
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

        # If day exists but price is stale (market closed), expose it.
        try:
            stale = bool(stale or day.get("is_stale") or ((day.get("price") or {}).get("is_stale")))
        except Exception:
            pass

        price = day.get("price") or {}
        price_status = str(price.get("status") or "ok")
        close = price.get("close", None)
        pct_change = price.get("pct_change", None)
        latest["symbols"].append(
            {
                "id": sym_id,
                "name": sym["name"],
                "sentiment_index": day["sentiment"]["index"],
                "sentiment_band": day["sentiment"]["band"],
                "price_status": price_status,
                "pct_change": pct_change,
                "close": close,
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
