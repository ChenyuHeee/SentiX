from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple


def fetch_extras(cfg: Dict[str, Any], symbol: Dict[str, Any], date: str) -> Dict[str, Any]:
    """Fetch extra datasets for a symbol.

    Data sources: AKShare (no token). Each module is isolated: failures do not
    break the pipeline.
    """

    _ = cfg
    variety = _infer_variety(symbol)
    if not variety:
        return {
            "status": "unavailable",
            "asof": date,
            "modules": {
                "inventory": _mod_unavailable("仓单/库存", "missing variety"),
                "spot_basis": _mod_unavailable("现货/基差", "missing variety"),
                "roll_yield": _mod_unavailable("展期收益率", "missing variety"),
                "positions_rank": _mod_unavailable("会员持仓/成交排名", "missing variety"),
            },
        }

    ak = _try_import_akshare()
    if ak is None:
        return {
            "status": "unavailable",
            "asof": date,
            "modules": {
                "inventory": _mod_unavailable("仓单/库存", "akshare not installed"),
                "spot_basis": _mod_unavailable("现货/基差", "akshare not installed"),
                "roll_yield": _mod_unavailable("展期收益率", "akshare not installed"),
                "positions_rank": _mod_unavailable("会员持仓/成交排名", "akshare not installed"),
            },
        }

    date_iso, date_compact = _resolve_asof_date(date)

    modules: Dict[str, Any] = {}

    # Inventory (Eastmoney) - often limited to certain varieties
    modules["inventory"] = _fetch_inventory(ak, variety=variety)

    # Spot & basis (needs trading date)
    modules["spot_basis"] = _fetch_spot_basis(ak, variety=variety, date_compact=date_compact)

    # Roll yield (needs trading date)
    modules["roll_yield"] = _fetch_roll_yield(ak, variety=variety, date_compact=date_compact)

    # Positions rank (needs trading date)
    modules["positions_rank"] = _fetch_positions_rank(ak, variety=variety, date_compact=date_compact)

    overall = "ok" if any(m.get("status") == "ok" for m in modules.values()) else "unavailable"
    return {"status": overall, "asof": date_iso, "modules": modules}


def _try_import_akshare():
    try:
        import akshare as ak  # type: ignore

        return ak
    except Exception as e:
        logging.info("AKShare not available: %s", e)
        return None


def _infer_variety(symbol: Dict[str, Any]) -> str:
    # Prefer explicit override if provided
    v = (symbol.get("variety") or "").strip()
    if v:
        return v.upper()

    ak_sym = (symbol.get("akshare_symbol") or "").strip().upper()
    if ak_sym and ak_sym.endswith("0"):
        return ak_sym[:-1]
    if ak_sym:
        return ak_sym
    return ""


def _resolve_asof_date(date_iso: str) -> Tuple[str, str]:
    # default: the provided date
    try:
        dt = datetime.strptime(date_iso, "%Y-%m-%d")
    except Exception:
        return date_iso, ""

    # If weekend, roll back to Friday
    while dt.weekday() >= 5:
        dt -= timedelta(days=1)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%Y%m%d")


def _date_candidates(date_compact: str, *, max_lookback_days: int = 7) -> List[str]:
    try:
        dt = datetime.strptime(date_compact, "%Y%m%d")
    except Exception:
        return [date_compact] if date_compact else []

    out = []
    for i in range(0, max_lookback_days + 1):
        out.append((dt - timedelta(days=i)).strftime("%Y%m%d"))
    return out


def _mod_unavailable(title: str, reason: str) -> Dict[str, Any]:
    return {"status": "unavailable", "hint": f"{title}（{reason}）", "items": []}


def _to_records(df: Any) -> List[Dict[str, Any]]:
    if df is None:
        return []
    try:
        return list(df.to_dict("records"))
    except Exception:
        return []


def _num(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s in {"", "nan", "None"}:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _fetch_inventory(ak: Any, *, variety: str) -> Dict[str, Any]:
    try:
        df = ak.futures_inventory_em(symbol=variety)
        recs = _to_records(df)
        items = []
        for r in recs[-60:]:
            d = r.get("日期") or r.get("date")
            inv = _num(r.get("库存") or r.get("inventory"))
            chg = _num(r.get("增减") or r.get("change"))
            if not d:
                continue
            items.append({"date": str(d).split(" ")[0].replace("/", "-"), "inventory": inv, "change": chg})
        summary = None
        if items:
            last = items[-1]
            summary = f"最新库存 {last.get('inventory')}，增减 {last.get('change')}"
        return {
            "status": "ok" if items else "empty",
            "hint": "仓单/库存（AKShare futures_inventory_em）",
            "summary": summary,
            "items": items,
        }
    except Exception as e:
        return {
            "status": "unavailable",
            "hint": f"仓单/库存（无数据或接口异常：{type(e).__name__}）",
            "items": [],
        }


def _fetch_spot_basis(ak: Any, *, variety: str, date_compact: str) -> Dict[str, Any]:
    if not date_compact:
        return _mod_unavailable("现货/基差", "missing date")
    last_exc: Exception | None = None
    for d in _date_candidates(date_compact):
        try:
            df = ak.futures_spot_price(d)
            recs = _to_records(df)
            target = None
            for r in recs:
                sym = (r.get("symbol") or r.get("品种") or "").strip().upper()
                if sym == variety:
                    target = r
                    break
            if not target:
                continue

            item = {
                "date": d,
                "symbol": variety,
                "spot_price": _num(target.get("spot_price") or target.get("现货价格")),
                "near_contract": target.get("near_contract") or target.get("最近交割合约"),
                "near_contract_price": _num(target.get("near_contract_price") or target.get("最近交割合约价格")),
                "dom_contract": target.get("dom_contract") or target.get("主力合约"),
                "dom_contract_price": _num(target.get("dom_contract_price") or target.get("主力合约价格")),
                "near_basis": _num(target.get("near_basis") or target.get("最近合约基差值")),
                "dom_basis": _num(target.get("dom_basis") or target.get("主力合约基差值")),
                "near_basis_rate": _num(target.get("near_basis_rate") or target.get("最近合约基差率")),
                "dom_basis_rate": _num(target.get("dom_basis_rate") or target.get("主力合约基差率")),
            }

            summary = None
            if item.get("spot_price") is not None:
                summary = f"现货 {item.get('spot_price')} · 主力基差 {item.get('dom_basis')}"

            return {
                "status": "ok",
                "hint": "现货/基差（AKShare futures_spot_price）",
                "summary": summary,
                "items": [item],
                "params": {"date": d},
            }
        except Exception as e:
            last_exc = e
            continue

    if last_exc is not None:
        return {
            "status": "unavailable",
            "hint": f"现货/基差（接口异常：{type(last_exc).__name__}）",
            "items": [],
            "params": {"date": date_compact},
        }
    return {"status": "empty", "hint": "现货/基差（AKShare futures_spot_price）", "items": [], "params": {"date": date_compact}}


def _fetch_roll_yield(ak: Any, *, variety: str, date_compact: str) -> Dict[str, Any]:
    if not date_compact:
        return _mod_unavailable("展期收益率", "missing date")
    last_exc: Exception | None = None
    for d in _date_candidates(date_compact):
        try:
            df = ak.get_roll_yield(date=d, var=variety)
            recs = _to_records(df)
            items = recs[:]
            if not items:
                continue

            summary = None
            r0 = items[0]
            val = None
            for k in ["roll_yield", "展期收益率", "yield", "value"]:
                if k in r0:
                    val = _num(r0.get(k))
                    break
            if val is not None:
                summary = f"展期收益率 {val}"
            return {
                "status": "ok",
                "hint": "展期收益率（AKShare get_roll_yield）",
                "summary": summary,
                "items": items,
                "params": {"date": d, "var": variety},
            }
        except Exception as e:
            last_exc = e
            continue

    if last_exc is not None:
        return {
            "status": "unavailable",
            "hint": f"展期收益率（接口异常：{type(last_exc).__name__}）",
            "items": [],
            "params": {"date": date_compact, "var": variety},
        }
    return {
        "status": "empty",
        "hint": "展期收益率（AKShare get_roll_yield）",
        "items": [],
        "params": {"date": date_compact, "var": variety},
    }


def _fetch_positions_rank(ak: Any, *, variety: str, date_compact: str) -> Dict[str, Any]:
    if not date_compact:
        return _mod_unavailable("会员持仓/成交排名", "missing date")
    last_exc: Exception | None = None
    for d in _date_candidates(date_compact):
        try:
            df = ak.get_rank_sum_daily(start_day=d, end_day=d, vars_list=[variety])
            recs = _to_records(df)
            items = recs[:]
            if not items:
                continue
            return {
                "status": "ok",
                "hint": "会员持仓/成交排名（AKShare get_rank_sum_daily）",
                "summary": f"{len(items)} 条汇总记录",
                "items": items,
                "params": {"date": d, "var": variety},
            }
        except Exception as e:
            last_exc = e
            continue

    if last_exc is not None:
        return {
            "status": "unavailable",
            "hint": f"会员持仓/成交排名（接口异常：{type(last_exc).__name__}）",
            "items": [],
            "params": {"date": date_compact, "var": variety},
        }
    return {
        "status": "empty",
        "hint": "会员持仓/成交排名（AKShare get_rank_sum_daily）",
        "items": [],
        "params": {"date": date_compact, "var": variety},
    }
