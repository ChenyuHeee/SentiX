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
    symbol_name = (symbol.get("name") or "").strip()
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
    modules["inventory"] = _fetch_inventory(ak, variety=variety, symbol_name=symbol_name)

    # Spot & basis (needs trading date)
    modules["spot_basis"] = _fetch_spot_basis(
        ak, variety=variety, symbol_name=symbol_name, date_compact=date_compact
    )

    # Roll yield (needs trading date)
    modules["roll_yield"] = _fetch_roll_yield(
        ak, variety=variety, symbol_name=symbol_name, date_compact=date_compact
    )

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


def _norm_text(s: Any) -> str:
    if s is None:
        return ""
    t = str(s).strip().replace(" ", "").replace("\t", "")
    return t.upper()


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


def _fetch_inventory(ak: Any, *, variety: str, symbol_name: str) -> Dict[str, Any]:
    last_exc: Exception | None = None
    candidates = []
    if variety:
        candidates.append(variety)
    if symbol_name and symbol_name not in candidates:
        candidates.append(symbol_name)

    for cand in candidates:
        try:
            df = ak.futures_inventory_em(symbol=cand)
            recs = _to_records(df)
            items = []
            for r in recs[-60:]:
                d = r.get("日期") or r.get("date")
                inv = _num(r.get("库存") or r.get("inventory"))
                chg = _num(r.get("增减") or r.get("change"))
                if not d:
                    continue
                items.append(
                    {
                        "date": str(d).split(" ")[0].replace("/", "-"),
                        "inventory": inv,
                        "change": chg,
                    }
                )
            if not items:
                continue
            last = items[-1]
            summary = f"最新库存 {last.get('inventory')}，增减 {last.get('change')}"
            return {
                "status": "ok",
                "hint": "仓单/库存（AKShare futures_inventory_em）",
                "summary": summary,
                "items": items,
                "params": {"symbol": cand},
            }
        except Exception as e:
            last_exc = e
            continue

    if last_exc is not None:
        return {
            "status": "unavailable",
            "hint": f"仓单/库存（无数据或接口异常：{type(last_exc).__name__}）",
            "items": [],
        }
    return {"status": "empty", "hint": "仓单/库存（AKShare futures_inventory_em）", "items": []}


def _fetch_spot_basis(ak: Any, *, variety: str, symbol_name: str, date_compact: str) -> Dict[str, Any]:
    if not date_compact:
        return _mod_unavailable("现货/基差", "missing date")
    last_exc: Exception | None = None
    targets = {_norm_text(variety), _norm_text(symbol_name)}
    targets.discard("")
    try:
        asof_dt = datetime.strptime(date_compact, "%Y%m%d").date()
    except Exception:
        asof_dt = None
    for d in _date_candidates(date_compact):
        try:
            # Prefer passing vars_list to avoid default filtering dropping some varieties.
            try:
                df = ak.futures_spot_price(d, vars_list=[variety.strip().upper()])
            except TypeError:
                df = ak.futures_spot_price(d)
            recs = _to_records(df)
            target = None
            for r in recs:
                # Prefer well-known columns, then fall back to scanning all string fields.
                candidates = []
                for k in [
                    "symbol",
                    "品种",
                    "品种名称",
                    "品种名",
                    "var",
                    "VAR",
                    "代码",
                    "品种代码",
                ]:
                    if k in r and r.get(k) is not None:
                        candidates.append(r.get(k))
                if not candidates:
                    for v in r.values():
                        if isinstance(v, str) and v.strip():
                            candidates.append(v)

                for c in candidates:
                    if _norm_text(c) in targets:
                        target = r
                        break
                if target:
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

    # Fallback 1: 生意社现期图（时间序列，取 <= asof 最近一条）
    if symbol_name:
        try:
            price_df = ak.futures_spot_sys(symbol=symbol_name, indicator="市场价格")
            basis_df = ak.futures_spot_sys(symbol=symbol_name, indicator="主力基差")
            price_recs = _to_records(price_df)
            basis_recs = _to_records(basis_df)
            basis_by_date = {}
            for r in basis_recs:
                k = r.get("日期") or r.get("date")
                if k is None:
                    continue
                basis_by_date[str(k).split(" ")[0]] = r

            best = None
            best_date = None
            for r in price_recs:
                k = r.get("日期") or r.get("date")
                if k is None:
                    continue
                k_str = str(k).split(" ")[0]
                try:
                    k_dt = datetime.strptime(k_str, "%Y-%m-%d").date()
                except Exception:
                    continue
                if asof_dt and k_dt > asof_dt:
                    continue
                if best_date is None or k_dt > best_date:
                    best_date = k_dt
                    best = r

            if best and best_date:
                k_str = best_date.strftime("%Y-%m-%d")
                b = basis_by_date.get(k_str, {})
                spot = _num(best.get("现货价格") or best.get("spot") or best.get("现货"))
                fut = _num(best.get("主力合约") or best.get("dom") or best.get("主力"))
                dom_basis = _num(b.get("主力基差") or b.get("dom_basis") or b.get("基差"))
                if dom_basis is None and spot is not None and fut is not None:
                    dom_basis = spot - fut
                item = {
                    "date": best_date.strftime("%Y%m%d"),
                    "symbol": variety,
                    "spot_price": spot,
                    "dom_contract_price": fut,
                    "dom_basis": dom_basis,
                    "source": "futures_spot_sys",
                }
                summary = None
                if spot is not None:
                    summary = f"现货 {spot} · 主力基差 {dom_basis}"
                return {
                    "status": "ok",
                    "hint": "现货/基差（AKShare futures_spot_sys）",
                    "summary": summary,
                    "items": [item],
                    "params": {"asof": date_compact, "picked": item["date"]},
                }
        except Exception as e:
            last_exc = e

    # Fallback 2: 99 期货期现走势（时间序列，取 <= asof 最近一条）
    if symbol_name:
        try:
            qh_df = ak.spot_price_qh(symbol=symbol_name)
            qh_recs = _to_records(qh_df)
            best = None
            best_date = None
            for r in qh_recs:
                k = r.get("日期") or r.get("date")
                if k is None:
                    continue
                k_str = str(k).split(" ")[0]
                # 支持 YYYY-MM-DD 或 date 对象字符串
                try:
                    k_dt = datetime.strptime(k_str, "%Y-%m-%d").date()
                except Exception:
                    continue
                if asof_dt and k_dt > asof_dt:
                    continue
                if best_date is None or k_dt > best_date:
                    best_date = k_dt
                    best = r
            if best and best_date:
                spot = _num(best.get("现货价格") or best.get("spot"))
                fut_close = _num(best.get("期货收盘价") or best.get("futures_close") or best.get("期货"))
                dom_basis = None
                if spot is not None and fut_close is not None:
                    dom_basis = spot - fut_close
                item = {
                    "date": best_date.strftime("%Y%m%d"),
                    "symbol": variety,
                    "spot_price": spot,
                    "dom_contract_price": fut_close,
                    "dom_basis": dom_basis,
                    "source": "spot_price_qh",
                }
                summary = None
                if spot is not None:
                    summary = f"现货 {spot} · 基差 {dom_basis}"
                return {
                    "status": "ok",
                    "hint": "现货/基差（AKShare spot_price_qh）",
                    "summary": summary,
                    "items": [item],
                    "params": {"asof": date_compact, "picked": item["date"]},
                }
        except Exception as e:
            last_exc = e

    # Fallback 3: 直接走 HTTP 版 100ppi（绕过 https SSLError）
    try:
        item = _fetch_spot_basis_100ppi_http(
            date_compact=date_compact,
            variety=variety.strip().upper(),
            symbol_name=symbol_name,
        )
        if item:
            spot = item.get("spot_price")
            dom_basis = item.get("dom_basis")
            summary = None
            if spot is not None:
                summary = f"现货 {spot} · 基差 {dom_basis}"
            return {
                "status": "ok",
                "hint": "现货/基差（100ppi http fallback）",
                "summary": summary,
                "items": [item],
                "params": {"date": item.get("date"), "source": "100ppi_http"},
            }
    except Exception as e:
        last_exc = e

    if last_exc is not None:
        return {
            "status": "unavailable",
            "hint": f"现货/基差（接口异常：{type(last_exc).__name__}）",
            "items": [],
            "params": {"date": date_compact},
        }
    return {"status": "empty", "hint": "现货/基差（AKShare futures_spot_price）", "items": [], "params": {"date": date_compact}}


def _fetch_spot_basis_100ppi_http(*, date_compact: str, variety: str, symbol_name: str) -> Dict[str, Any] | None:
    """Parse 100ppi daily spot/basis page via HTTP.

    This is a fallback for environments where https requests fail with SSLError.
    """

    import requests
    from io import StringIO

    try:
        date_iso = datetime.strptime(date_compact, "%Y%m%d").strftime("%Y-%m-%d")
    except Exception:
        return None

    url = f"http://www.100ppi.com/sf/day-{date_iso}.html"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    r = requests.get(url, headers=headers, timeout=20)
    r.encoding = r.apparent_encoding or "utf-8"
    tables = []
    try:
        import pandas as pd

        tables = pd.read_html(StringIO(r.text))
    except Exception:
        return None

    # Find a table that contains spot and main futures info
    target_row = None
    for t in tables:
        cols = [str(c).strip() for c in getattr(t, "columns", [])]
        if not cols:
            continue

        # Common columns from 100ppi pages
        has_spot = any(c in cols for c in ["现货价格", "现货", "现货价"])
        has_main = any(c in cols for c in ["主力合约价格", "主力合约", "主力"])
        has_goods = any(c in cols for c in ["商品", "品种", "商品名称"])
        if not (has_goods and (has_spot or has_main)):
            continue

        # Normalize columns
        df = t.copy()
        for c in df.columns:
            if str(c).strip() == "商品名称":
                df.rename(columns={c: "商品"}, inplace=True)
            if str(c).strip() == "品种":
                df.rename(columns={c: "商品"}, inplace=True)

        if "商品" not in df.columns:
            continue

        # Match by chinese name first, then variety code
        for _, row in df.iterrows():
            goods = str(row.get("商品", "")).strip()
            if not goods:
                continue
            if symbol_name and goods == symbol_name:
                target_row = row
                break
            if variety and _norm_text(goods) == _norm_text(variety):
                target_row = row
                break
        if target_row is not None:
            break

    if target_row is None:
        return None

    spot = _num(target_row.get("现货价格") or target_row.get("现货") or target_row.get("现货价"))
    dom_price = _num(
        target_row.get("主力合约价格")
        or target_row.get("主力合约")
        or target_row.get("主力")
        or target_row.get("期货价格")
    )
    dom_basis = _num(
        target_row.get("主力合约基差")
        or target_row.get("主力基差")
        or target_row.get("基差")
        or target_row.get("主力合约基差值")
    )
    if dom_basis is None and spot is not None and dom_price is not None:
        dom_basis = spot - dom_price

    return {
        "date": date_compact,
        "symbol": variety,
        "spot_price": spot,
        "dom_contract_price": dom_price,
        "dom_basis": dom_basis,
        "source": "100ppi_http",
    }


def _fetch_roll_yield(ak: Any, *, variety: str, symbol_name: str, date_compact: str) -> Dict[str, Any]:
    if not date_compact:
        return _mod_unavailable("展期收益率", "missing date")
    last_exc: Exception | None = None
    for d in _date_candidates(date_compact):
        try:
            res = None
            # Some AKShare versions expect lowercase variety
            for v in [
                variety,
                variety.lower(),
                variety.strip(),
                variety.strip().lower(),
                symbol_name,
                symbol_name.strip(),
            ]:
                if not v or not str(v).strip():
                    continue
                try:
                    res = ak.get_roll_yield(date=d, var=v)
                    break
                except Exception as e:
                    last_exc = e
                    res = None

            if not res:
                continue

            # AKShare returns (roll_yield, near_by, deferred) in many versions.
            if isinstance(res, (tuple, list)) and len(res) >= 3:
                ry, near_by, deferred = res[0], res[1], res[2]
                ry_num = _num(ry)
                item = {
                    "date": d,
                    "var": variety,
                    "roll_yield": ry_num,
                    "near_by": str(near_by),
                    "deferred": str(deferred),
                }
                return {
                    "status": "ok",
                    "hint": "展期收益率（AKShare get_roll_yield）",
                    "summary": f"展期收益率 {ry_num}",
                    "items": [item],
                    "params": {"date": d, "var": variety},
                }

            # Fallback: if a DataFrame-like is returned
            recs = _to_records(res)
            if not recs:
                continue
            r0 = recs[0]
            val = None
            for k in ["roll_yield", "ry", "展期收益率", "yield", "value"]:
                if k in r0:
                    val = _num(r0.get(k))
                    break
            return {
                "status": "ok",
                "hint": "展期收益率（AKShare get_roll_yield）",
                "summary": f"展期收益率 {val}" if val is not None else None,
                "items": recs,
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
