from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from .utils import ensure_dir, read_json, write_json


def update_fundamentals(
    *,
    data_dir: Path,
    symbol: Dict[str, Any],
    extras: Dict[str, Any] | None,
    tz_label: str,
    max_points: int = 240,
) -> None:
    """Persist a compact, front-end friendly fundamentals dataset.

    Output: data/symbols/<id>/fundamentals.json
    The file is intentionally small (bounded by max_points per series).
    """

    if not extras or not isinstance(extras, dict):
        return

    sym_id = str(symbol.get("id") or "").strip()
    sym_name = str(symbol.get("name") or "").strip()
    if not sym_id:
        return

    modules = extras.get("modules") or {}
    if not isinstance(modules, dict):
        modules = {}

    symbol_dir = data_dir / "symbols" / sym_id
    ensure_dir(symbol_dir)
    out_path = symbol_dir / "fundamentals.json"
    prev = read_json(out_path, default=None)
    if not isinstance(prev, dict):
        prev = {}

    def _take_series(obj: Any) -> List[Dict[str, Any]]:
        if isinstance(obj, dict) and isinstance(obj.get("series"), list):
            return [x for x in obj.get("series") if isinstance(x, dict)]
        return []

    def _iso_date(d: Any) -> str:
        if d is None:
            return ""
        s = str(d).strip()
        if not s:
            return ""
        s = s.split(" ")[0].replace("/", "-")
        if len(s) == 8 and s.isdigit():
            return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
        return s

    def _num(v: Any) -> float | None:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            x = float(v)
            if x != x:  # NaN
                return None
            return x
        s = str(v).strip().replace(",", "")
        if s in {"", "nan", "None"}:
            return None
        try:
            x = float(s)
            if x != x:
                return None
            return x
        except Exception:
            return None

    def _upsert_by_date(series: List[Dict[str, Any]], rec: Dict[str, Any]) -> List[Dict[str, Any]]:
        d = _iso_date(rec.get("date"))
        if not d:
            return series
        rec2 = {**rec, "date": d}
        merged: Dict[str, Dict[str, Any]] = {str(_iso_date(x.get("date"))): x for x in series if _iso_date(x.get("date"))}
        old = merged.get(d) or {}
        merged[d] = {**old, **rec2}
        out = sorted(merged.values(), key=lambda x: str(x.get("date") or ""))
        return out[-max_points:]

    def _summarize_last(series: List[Dict[str, Any]], *keys: str) -> str | None:
        if not series:
            return None
        last = series[-1]
        parts = []
        for k in keys:
            if k in last and last.get(k) is not None:
                parts.append(f"{k}={last.get(k)}")
        return " · ".join(parts) if parts else None

    # inventory: replace with latest available series from module (already a time series)
    inv_mod = modules.get("inventory") or {}
    inv_series: List[Dict[str, Any]] = []
    if isinstance(inv_mod, dict) and isinstance(inv_mod.get("items"), list):
        for it in inv_mod.get("items"):
            if not isinstance(it, dict):
                continue
            d = _iso_date(it.get("date"))
            if not d:
                continue
            inv_series.append(
                {
                    "date": d,
                    "inventory": _num(it.get("inventory")),
                    "change": _num(it.get("change")),
                }
            )
        inv_series = [x for x in inv_series if x.get("inventory") is not None]
        inv_series.sort(key=lambda x: x["date"])
        inv_series = inv_series[-max_points:]
    else:
        inv_series = _take_series((prev.get("inventory") if isinstance(prev, dict) else None) or {})

    # spot_basis: append daily
    basis_prev = (prev.get("spot_basis") or {}) if isinstance(prev, dict) else {}
    basis_series = _take_series(basis_prev)
    basis_mod = modules.get("spot_basis") or {}
    if isinstance(basis_mod, dict) and basis_mod.get("status") == "ok":
        items = basis_mod.get("items")
        if isinstance(items, list) and items:
            r0 = items[0] if isinstance(items[0], dict) else None
            if isinstance(r0, dict):
                basis_series = _upsert_by_date(
                    basis_series,
                    {
                        "date": r0.get("date"),
                        "spot_price": _num(r0.get("spot_price")),
                        "dom_basis": _num(r0.get("dom_basis")),
                        "dom_basis_rate": _num(r0.get("dom_basis_rate")),
                        "dom_contract": r0.get("dom_contract"),
                        "dom_contract_price": _num(r0.get("dom_contract_price")),
                    },
                )

    # roll_yield: append daily
    ry_prev = (prev.get("roll_yield") or {}) if isinstance(prev, dict) else {}
    ry_series = _take_series(ry_prev)
    ry_mod = modules.get("roll_yield") or {}
    if isinstance(ry_mod, dict) and ry_mod.get("status") == "ok":
        items = ry_mod.get("items")
        if isinstance(items, list) and items:
            r0 = items[0] if isinstance(items[0], dict) else None
            if isinstance(r0, dict):
                ry_series = _upsert_by_date(
                    ry_series,
                    {
                        "date": r0.get("date"),
                        "roll_yield": _num(r0.get("roll_yield") or r0.get("ry") or r0.get("展期收益率")),
                        "near_by": r0.get("near_by"),
                        "deferred": r0.get("deferred"),
                    },
                )

    # positions_rank: compute a compact daily summary + keep a small preview for latest day
    pos_prev = (prev.get("positions_rank") or {}) if isinstance(prev, dict) else {}
    pos_series = _take_series(pos_prev)
    pos_preview: List[Dict[str, Any]] = []

    pos_mod = modules.get("positions_rank") or {}
    if isinstance(pos_mod, dict) and pos_mod.get("status") == "ok":
        items = pos_mod.get("items")
        if isinstance(items, list) and items:
            # Keep a tiny preview (first 10 rows) for front-end inspection.
            for it in items[:10]:
                if isinstance(it, dict):
                    pos_preview.append(it)

            def _sum_keys(rows: List[Dict[str, Any]], keys: List[str]) -> float | None:
                total = 0.0
                found = False
                for r in rows:
                    for k in keys:
                        if k in r and r.get(k) is not None:
                            v = _num(r.get(k))
                            if v is None:
                                continue
                            total += float(v)
                            found = True
                            break
                return total if found else None

            rows = [x for x in items if isinstance(x, dict)]
            long_v = _sum_keys(rows, ["long", "多单", "多头", "多单持仓", "多头持仓", "多头持仓量", "多头持仓(手)"])
            short_v = _sum_keys(rows, ["short", "空单", "空头", "空单持仓", "空头持仓", "空头持仓量", "空头持仓(手)"])
            net_v = _sum_keys(rows, ["net", "净持仓", "净持仓量", "净持仓(手)"])
            vol_v = _sum_keys(rows, ["vol", "volume", "成交量", "成交", "成交量(手)"])
            if net_v is None and (long_v is not None) and (short_v is not None):
                net_v = float(long_v) - float(short_v)

            # date from params (prefer) else from extras.asof
            d0 = ""
            params = pos_mod.get("params")
            if isinstance(params, dict) and params.get("date"):
                d0 = _iso_date(params.get("date"))
            if not d0:
                d0 = _iso_date(extras.get("asof"))
            if d0:
                pos_series = _upsert_by_date(
                    pos_series,
                    {
                        "date": d0,
                        "long": None if long_v is None else round(float(long_v), 2),
                        "short": None if short_v is None else round(float(short_v), 2),
                        "net": None if net_v is None else round(float(net_v), 2),
                        "volume": None if vol_v is None else round(float(vol_v), 2),
                        "rows": len(rows),
                    },
                )

    # Assemble output
    out = {
        "symbol": {"id": sym_id, "name": sym_name},
        "updated_at": tz_label,
        "asof": str(extras.get("asof") or ""),
        "inventory": {
            "status": str((inv_mod.get("status") if isinstance(inv_mod, dict) else "") or ("ok" if inv_series else "unavailable")),
            "hint": (inv_mod.get("hint") if isinstance(inv_mod, dict) else None),
            "series": inv_series,
            "summary": _summarize_last(inv_series, "inventory", "change"),
        },
        "spot_basis": {
            "status": str((basis_mod.get("status") if isinstance(basis_mod, dict) else "") or ("ok" if basis_series else "unavailable")),
            "hint": (basis_mod.get("hint") if isinstance(basis_mod, dict) else None),
            "series": basis_series,
            "summary": _summarize_last(basis_series, "spot_price", "dom_basis", "dom_basis_rate"),
        },
        "roll_yield": {
            "status": str((ry_mod.get("status") if isinstance(ry_mod, dict) else "") or ("ok" if ry_series else "unavailable")),
            "hint": (ry_mod.get("hint") if isinstance(ry_mod, dict) else None),
            "series": ry_series,
            "summary": _summarize_last(ry_series, "roll_yield", "near_by", "deferred"),
        },
        "positions_rank": {
            "status": str((pos_mod.get("status") if isinstance(pos_mod, dict) else "") or ("ok" if pos_series else "unavailable")),
            "hint": (pos_mod.get("hint") if isinstance(pos_mod, dict) else None),
            "series": pos_series,
            "latest_preview": pos_preview,
            "summary": _summarize_last(pos_series, "net", "long", "short", "volume"),
        },
    }

    try:
        write_json(out_path, out)
    except Exception as e:
        logging.info("write fundamentals failed for %s: %s", sym_id, e)
