from __future__ import annotations

import hashlib
import logging
import os
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List

import requests


def _seed(symbol_id: str) -> int:
    h = hashlib.sha256(symbol_id.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def fetch_kline(cfg: Dict[str, Any], symbol: Dict[str, Any], end_date: str, days: int) -> List[Dict[str, Any]]:
    provider = (cfg.get("price", {}) or {}).get("provider", "mock")
    if provider == "akshare":
        try:
            return fetch_kline_akshare(cfg, symbol, end_date=end_date, days=days)
        except Exception as e:
            logging.warning("AKShare price fetch failed for %s: %s; fallback to mock", symbol.get("id"), e)
    if provider == "tushare":
        try:
            return fetch_kline_tushare(cfg, symbol, end_date=end_date, days=days)
        except Exception as e:
            logging.warning("Tushare price fetch failed for %s: %s; fallback to mock", symbol.get("id"), e)

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


def fetch_kline_akshare(cfg: Dict[str, Any], symbol: Dict[str, Any], *, end_date: str, days: int) -> List[Dict[str, Any]]:
    # AKShare: 新浪-期货-主力连续合约历史数据
    # 接口: ak.futures_main_sina(symbol="IF0", start_date="YYYYMMDD", end_date="YYYYMMDD")
    # 输出字段(常见): 日期, 开盘价, 最高价, 最低价, 收盘价, 成交量, 持仓量, 动态结算价
    ak_symbol = (symbol.get("akshare_symbol") or "").strip()
    if not ak_symbol:
        raise RuntimeError(f"missing akshare_symbol for symbol {symbol.get('id')}")

    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=days * 3)
    start_compact = start_dt.strftime("%Y%m%d")
    end_compact = end_dt.strftime("%Y%m%d")

    try:
        import akshare as ak  # type: ignore
    except Exception as e:
        raise RuntimeError(f"akshare not available: {e}")

    df = ak.futures_main_sina(symbol=ak_symbol, start_date=start_compact, end_date=end_compact)
    if df is None:
        raise RuntimeError("empty akshare response")

    # Avoid importing pandas explicitly; rely on DataFrame API.
    try:
        records = df.to_dict("records")
    except Exception as e:
        raise RuntimeError(f"unexpected akshare dataframe: {e}")
    if not records:
        raise RuntimeError("empty futures_main_sina")

    def _to_float(v: Any) -> float:
        if v is None:
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip().replace(",", "")
        if s in {"", "nan", "None"}:
            return 0.0
        return float(s)

    def _to_int(v: Any) -> int:
        return int(_to_float(v))

    out: List[Dict[str, Any]] = []
    for r in records:
        date_raw = r.get("日期") or r.get("date") or r.get("Date")
        if not date_raw:
            continue
        date_iso = str(date_raw)
        # common formats: YYYY-MM-DD, YYYY/MM/DD, datetime
        date_iso = date_iso.split(" ")[0].replace("/", "-")
        if len(date_iso) == 8 and date_iso.isdigit():
            date_iso = f"{date_iso[0:4]}-{date_iso[4:6]}-{date_iso[6:8]}"
        if len(date_iso) != 10:
            continue

        out.append(
            {
                "date": date_iso,
                "open": _to_float(r.get("开盘价") or r.get("open")),
                "high": _to_float(r.get("最高价") or r.get("high")),
                "low": _to_float(r.get("最低价") or r.get("low")),
                "close": _to_float(r.get("收盘价") or r.get("close")),
                "volume": _to_int(r.get("成交量") or r.get("volume")),
                "open_interest": _to_int(r.get("持仓量") or r.get("open_interest") or r.get("oi")),
            }
        )

    out.sort(key=lambda x: x["date"])
    # Keep within range and last N trading bars
    out = [x for x in out if x["date"] <= end_date]
    return out[-days:]


def _tushare_post(token: str, api_name: str, params: Dict[str, Any], fields: str) -> Dict[str, Any]:
    url = "https://api.tushare.pro"
    payload = {"api_name": api_name, "token": token, "params": params, "fields": fields}
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    # success: {"code":0,"msg":"","data":{...}}
    if not isinstance(data, dict) or data.get("code", 0) != 0:
        msg = data.get("msg") if isinstance(data, dict) else "unknown"
        raise RuntimeError(f"tushare error: {msg}")
    return data


def fetch_kline_tushare(cfg: Dict[str, Any], symbol: Dict[str, Any], *, end_date: str, days: int) -> List[Dict[str, Any]]:
    price_cfg = cfg.get("price", {}) or {}
    token_env = ((price_cfg.get("tushare", {}) or {}).get("token_env") or "TUSHARE_TOKEN")
    token = os.environ.get(token_env, "").strip()
    if not token:
        raise RuntimeError(f"missing env {token_env}")

    ts_code = (symbol.get("tushare_ts_code") or "").strip()
    if not ts_code:
        raise RuntimeError(f"missing tushare_ts_code for symbol {symbol.get('id')}")

    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    # 留足自然日以覆盖交易日缺口
    start_dt = end_dt - timedelta(days=days * 3)
    start_date = start_dt.strftime("%Y%m%d")
    end_date_compact = end_dt.strftime("%Y%m%d")

    # 主力连续：先取连续合约->月合约的每日映射，再拼接月合约日线
    mapping = _fetch_fut_mapping(
        token,
        ts_code=ts_code,
        start_date=start_date,
        end_date=end_date_compact,
    )
    if mapping:
        return _build_continuous_from_mapping(token, mapping=mapping, start_date=start_date, end_date=end_date_compact, days=days)

    # fallback：如果 mapping 为空（例如传入的是月合约代码），直接按 ts_code 拉日线
    return _fetch_fut_daily_series(token, ts_code=ts_code, start_date=start_date, end_date=end_date_compact)[-days:]


def _parse_tushare_table(raw: Dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    ds = (raw.get("data") or {})
    fields = ds.get("fields") or []
    items = ds.get("items") or []
    if not fields or not items:
        return [], []
    return list(fields), list(items)


def _fetch_fut_mapping(token: str, *, ts_code: str, start_date: str, end_date: str) -> list[dict[str, str]]:
    # 接口：fut_mapping
    # 输出：ts_code(连续合约代码), trade_date, mapping_ts_code(月合约代码)
    for candidate in _candidate_continuous_codes(ts_code):
        raw = _tushare_post(
            token,
            "fut_mapping",
            params={"ts_code": candidate, "start_date": start_date, "end_date": end_date},
            fields="ts_code,trade_date,mapping_ts_code",
        )
        fields, items = _parse_tushare_table(raw)
        if not fields or not items:
            continue
        idx = {name: i for i, name in enumerate(fields)}
        out: list[dict[str, str]] = []
        for row in items:
            td = str(row[idx.get("trade_date")]) if idx.get("trade_date") is not None else ""
            mp = str(row[idx.get("mapping_ts_code")]) if idx.get("mapping_ts_code") is not None else ""
            if len(td) == 8 and mp:
                out.append({"trade_date": td, "mapping_ts_code": mp})
        if out:
            # 通常是倒序，统一为升序
            out.sort(key=lambda x: x["trade_date"])
            return out
    return []


def _candidate_continuous_codes(ts_code: str) -> list[str]:
    # Tushare 文档里中金所连续合约示例用 .CFX，但有些人会写成 .CFFEX；这里做个兼容。
    codes = [ts_code]
    if ts_code.endswith(".CFFEX"):
        codes.append(ts_code.replace(".CFFEX", ".CFX"))
    elif ts_code.endswith(".CFX"):
        codes.append(ts_code.replace(".CFX", ".CFFEX"))
    # 去重保持顺序
    seen = set()
    out: list[str] = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _fetch_fut_daily_series(token: str, *, ts_code: str, start_date: str, end_date: str) -> list[Dict[str, Any]]:
    raw = _tushare_post(
        token,
        "fut_daily",
        params={"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
        fields="trade_date,open,high,low,close,vol,oi",
    )
    fields, items = _parse_tushare_table(raw)
    if not fields or not items:
        raise RuntimeError("empty fut_daily")
    idx = {name: i for i, name in enumerate(fields)}

    def g(row: list[Any], name: str, default: Any = None) -> Any:
        i = idx.get(name)
        return default if i is None else row[i]

    out: list[Dict[str, Any]] = []
    for row in items:
        td = str(g(row, "trade_date", ""))
        if len(td) != 8:
            continue
        date_iso = f"{td[0:4]}-{td[4:6]}-{td[6:8]}"
        out.append(
            {
                "date": date_iso,
                "open": float(g(row, "open", 0.0) or 0.0),
                "high": float(g(row, "high", 0.0) or 0.0),
                "low": float(g(row, "low", 0.0) or 0.0),
                "close": float(g(row, "close", 0.0) or 0.0),
                "volume": int(float(g(row, "vol", 0.0) or 0.0)),
                "open_interest": int(float(g(row, "oi", 0.0) or 0.0)),
            }
        )

    out.sort(key=lambda x: x["date"])
    return out


def _build_continuous_from_mapping(
    token: str,
    *,
    mapping: list[dict[str, str]],
    start_date: str,
    end_date: str,
    days: int,
) -> list[Dict[str, Any]]:
    # mapping: [{trade_date: YYYYMMDD, mapping_ts_code: XXX}...]
    wanted = mapping[-days:]
    unique_contracts: list[str] = []
    seen = set()
    for m in wanted:
        c = m["mapping_ts_code"]
        if c not in seen:
            seen.add(c)
            unique_contracts.append(c)

    daily_by_contract: dict[str, dict[str, Dict[str, Any]]] = {}
    for c in unique_contracts:
        series = _fetch_fut_daily_series(token, ts_code=c, start_date=start_date, end_date=end_date)
        daily_by_contract[c] = {x["date"].replace("-", ""): x for x in series}

    out: list[Dict[str, Any]] = []
    for m in wanted:
        td = m["trade_date"]
        c = m["mapping_ts_code"]
        bar = (daily_by_contract.get(c) or {}).get(td)
        if not bar:
            continue
        out.append(bar)
    out.sort(key=lambda x: x["date"])
    return out
