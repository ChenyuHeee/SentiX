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
    def _find_target(recs: List[Dict[str, Any]]) -> Dict[str, Any] | None:
        for r in recs:
            candidates: List[Any] = []
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
                c_norm = _norm_text(c)
                if not c_norm:
                    continue
                for t in targets:
                    if not t:
                        continue
                    # Support exact and fuzzy contains matching.
                    if c_norm == t or (t in c_norm) or (c_norm in t):
                        return r
        return None

    for d in _date_candidates(date_compact):
        try:
            # Prefer passing vars_list to avoid default filtering dropping some varieties,
            # but if it yields no match, retry without vars_list to search the full table.
            df_filtered = None
            try:
                df_filtered = ak.futures_spot_price(d, vars_list=[variety.strip().upper()])
            except TypeError:
                df_filtered = None

            if df_filtered is not None:
                recs = _to_records(df_filtered)
                target = _find_target(recs)
                if target is None:
                    # retry full table
                    df_all = ak.futures_spot_price(d)
                    target = _find_target(_to_records(df_all))
            else:
                df_all = ak.futures_spot_price(d)
                target = _find_target(_to_records(df_all))

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
    # NOTE: 在 GitHub Actions 等环境中 www.99qh.com 可能出现自签证书链导致 SSLError；
    # 这里遇到 SSLError 时自动降级为 verify=False 以保证数据可用。
    if symbol_name:
        try:
            qh_recs, used_insecure_ssl = _fetch_spot_trend_99qh(symbol_name)

            best = None
            best_date = None
            for r in qh_recs:
                k = r.get("date")
                if not k:
                    continue
                try:
                    k_dt = datetime.strptime(str(k), "%Y-%m-%d").date()
                except Exception:
                    continue
                if asof_dt and k_dt > asof_dt:
                    continue
                if best_date is None or k_dt > best_date:
                    best_date = k_dt
                    best = r

            if best and best_date:
                spot = _num(best.get("spot_price"))
                fut_close = _num(best.get("futures_close"))
                dom_basis = None
                if spot is not None and fut_close is not None:
                    dom_basis = spot - fut_close
                item = {
                    "date": best_date.strftime("%Y%m%d"),
                    "symbol": variety,
                    "spot_price": spot,
                    "dom_contract_price": fut_close,
                    "dom_basis": dom_basis,
                    "source": "99qh",
                }
                summary = None
                if spot is not None:
                    summary = f"现货 {spot} · 基差 {dom_basis}"
                suffix = "（verify=False）" if used_insecure_ssl else ""
                return {
                    "status": "ok",
                    "hint": f"现货/基差（99qh{suffix}）",
                    "summary": summary,
                    "items": [item],
                    "params": {"asof": date_compact, "picked": item["date"]},
                }
        except Exception as e:
            last_exc = e

    # Fallback 3: 直接走 HTTP 版 100ppi（绕过 https SSLError）
    try:
        for d in _date_candidates(date_compact):
            item = _fetch_spot_basis_100ppi_http(
                date_compact=d,
                variety=variety.strip().upper(),
                symbol_name=symbol_name,
            )
            if not item:
                continue

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
                "params": {
                    "asof": date_compact,
                    "picked": item.get("date"),
                    "source": "100ppi_http",
                },
            }
    except Exception as e:
        last_exc = e

    if last_exc is not None:
        msg = str(last_exc).strip()
        detail = f"{type(last_exc).__name__}" + (f": {msg}" if msg else "")
        return {
            "status": "unavailable",
            "hint": f"现货/基差（接口异常：{detail}）",
            "items": [],
            "params": {"date": date_compact},
        }
    return {"status": "empty", "hint": "现货/基差（AKShare futures_spot_price）", "items": [], "params": {"date": date_compact}}


def _fetch_spot_trend_99qh(symbol_name: str) -> Tuple[List[Dict[str, Any]], bool]:
    """Fetch spot trend series from 99qh.

    Returns (records, used_insecure_ssl).

    Each record has keys: date(YYYY-MM-DD), spot_price, futures_close.
    """

    import json
    import re

    import requests

    # Only disable warnings if we actually go insecure.
    used_insecure_ssl = False

    def _get(session: requests.Session, url: str, *, headers: Dict[str, str] | None = None, params: Dict[str, Any] | None = None):
        nonlocal used_insecure_ssl
        try:
            return session.get(url, headers=headers, params=params, timeout=20)
        except requests.exceptions.SSLError:
            used_insecure_ssl = True
            try:
                from urllib3.exceptions import InsecureRequestWarning

                requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)  # type: ignore[attr-defined]
            except Exception:
                pass
            return session.get(url, headers=headers, params=params, timeout=20, verify=False)

    # Small alias set for names that differ across data sources.
    alias = {
        "沪深300股指": "沪深300",
        "沪深300指数": "沪深300",
    }
    symbol_qh = alias.get(symbol_name, symbol_name)

    with requests.Session() as s:
        # 1) get productId mapping
        html = _get(s, "https://www.99qh.com/data/spotTrend").text
        m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if not m:
            raise RuntimeError("99qh: missing __NEXT_DATA__")
        data = json.loads(m.group(1))
        variety_list = (
            data.get("props", {})
            .get("pageProps", {})
            .get("data", {})
            .get("varietyListData", [])
        )
        products: List[Dict[str, Any]] = []
        for it in variety_list:
            products.extend(it.get("productList") or [])
        name_to_id = {
            str(p.get("name")): p.get("productId")
            for p in products
            if p.get("name") and p.get("productId")
        }
        product_id = name_to_id.get(symbol_qh)
        if not product_id:
            # fuzzy match to tolerate naming differences
            for n, pid in name_to_id.items():
                if symbol_qh in n or n in symbol_qh:
                    product_id = pid
                    break
        if not product_id:
            raise RuntimeError(f"99qh: unknown symbol {symbol_name}")

        # 2) get token from v.js response header
        headers = {
            "Origin": "https://www.99qh.com",
            "Referer": "https://www.99qh.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        }
        v = _get(s, "https://centerapi.fx168api.com/app/common/v.js", headers=headers)
        pcc = v.headers.get("_pcc")
        if not pcc:
            raise RuntimeError("99qh: missing _pcc token")

        # 3) fetch trend list
        trend_headers = dict(headers)
        trend_headers["_pcc"] = pcc
        params = {
            "productId": str(product_id),
            "pageNo": "1",
            "pageSize": "50000",
            "startDate": "",
            "endDate": "2050-01-01",
            "appCategory": "web",
        }
        r = _get(
            s,
            "https://centerapi.fx168api.com/app/qh/api/spot/trend",
            headers=trend_headers,
            params=params,
        )
        j: Any = r.json()
        # Some environments may return a JSON-encoded string (even double-encoded).
        for _ in range(0, 2):
            if not isinstance(j, str):
                break
            try:
                j = json.loads(j)
            except Exception:
                break
        if isinstance(j, str):
            raise RuntimeError(f"99qh: unexpected json string: {j[:200]}")
        if not isinstance(j, dict):
            raise RuntimeError(f"99qh: unexpected json type {type(j).__name__}")

        code = j.get("code")
        if code not in (0, "0", None):
            msg = j.get("message")
            raise RuntimeError(f"99qh: api code {code} {msg}")

        data_obj: Any = j.get("data")
        if isinstance(data_obj, str):
            for _ in range(0, 2):
                if not isinstance(data_obj, str):
                    break
                try:
                    data_obj = json.loads(data_obj)
                except Exception:
                    break
        lst = (data_obj or {}).get("list") if isinstance(data_obj, dict) else []
        out: List[Dict[str, Any]] = []
        for it in lst:
            out.append(
                {
                    "date": it.get("date"),
                    "futures_close": it.get("fp"),
                    "spot_price": it.get("sp"),
                }
            )
        return out, used_insecure_ssl


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

        tables = pd.read_html(StringIO(r.text), flavor="lxml")
    except Exception:
        return None

    def _col_text(c: Any) -> str:
        if isinstance(c, tuple):
            parts = [str(x).strip() for x in c if str(x).strip() and str(x).strip().lower() != "nan"]
            return " ".join(parts)
        return str(c).strip()

    # Find a table that contains spot and main futures info
    target_row = None
    chosen_cols = {}
    for t in tables:
        df = t.copy()
        if getattr(df, "empty", False):
            continue

        col_texts = [_col_text(c) for c in getattr(df, "columns", [])]
        if not col_texts:
            continue

        has_goods = any(("商品" in c) or ("品种" in c) for c in col_texts)
        has_spot = any("现货" in c for c in col_texts)
        has_main = any("主力" in c for c in col_texts)
        if not (has_goods and (has_spot or has_main)):
            continue

        goods_col = None
        spot_col = None
        dom_price_col = None
        dom_basis_col = None
        for c in df.columns:
            ct = _col_text(c)
            if goods_col is None and ("商品" in ct or "品种" in ct):
                goods_col = c
            if spot_col is None and ("现货" in ct and "价格" in ct):
                spot_col = c
            if dom_price_col is None and ("主力" in ct and "价格" in ct):
                dom_price_col = c
            if dom_basis_col is None and ("现期差2" in ct or ("主力" in ct and "现期差" in ct)):
                dom_basis_col = c

        # Fallback matching: pick non-code spot/main columns if exact '价格' columns are missing
        if spot_col is None:
            for c in df.columns:
                ct = _col_text(c)
                if "现货" in ct and "代码" not in ct:
                    spot_col = c
                    break
        if dom_price_col is None:
            for c in df.columns:
                ct = _col_text(c)
                if "主力" in ct and "代码" not in ct and "现期差" not in ct:
                    dom_price_col = c
                    break

        if goods_col is None or (spot_col is None and dom_price_col is None):
            continue

        for _, row in df.iterrows():
            goods = str(row.get(goods_col, "")).strip()
            if not goods:
                continue
            if symbol_name and goods == symbol_name:
                target_row = row
            elif variety and _norm_text(goods) == _norm_text(variety):
                target_row = row
            else:
                continue

            chosen_cols = {
                "goods": goods_col,
                "spot": spot_col,
                "dom_price": dom_price_col,
                "dom_basis": dom_basis_col,
            }
            break

        if target_row is not None:
            break

    if target_row is None:
        return None

    spot_col = chosen_cols.get("spot")
    dom_price_col = chosen_cols.get("dom_price")
    dom_basis_col = chosen_cols.get("dom_basis")

    spot = _num(target_row.get(spot_col)) if spot_col is not None else None
    dom_price = _num(target_row.get(dom_price_col)) if dom_price_col is not None else None
    dom_basis = _num(target_row.get(dom_basis_col)) if dom_basis_col is not None else None
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
