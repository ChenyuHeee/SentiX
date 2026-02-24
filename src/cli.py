from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict

from .analyzer import analyze_news_items
from .crawler_news import fetch_news_bundle
from .crawler_price import fetch_kline
from .crawler_extras import fetch_extras
from .aggregator import upsert_symbol_day, write_latest
from .agents import combine_final, macro_agent, market_agent, market_agent_llm, symbol_news_agent, trade_plan
from .fundamentals import fundamentals_signals_for_llm, update_fundamentals
from .generator import build_site
from .utils import iso_datetime_now, iter_enabled_symbols, load_yaml, parse_date, read_json, setup_logging


def _symbol_to_dict(s) -> Dict[str, Any]:
    d: Dict[str, Any] = {"id": s.id, "name": s.name, "keywords": s.keywords, "asset": getattr(s, "asset", "futures")}
    if getattr(s, "akshare_symbol", None):
        d["akshare_symbol"] = s.akshare_symbol
    if getattr(s, "tushare_ts_code", None):
        d["tushare_ts_code"] = s.tushare_ts_code
    return d


def cmd_update_data(cfg: Dict[str, Any], *, root_dir: Path, date: str) -> None:
    data_cfg = cfg.get("data", {}) or {}
    tz = data_cfg.get("timezone", "Asia/Shanghai")
    tz_label = iso_datetime_now(tz)
    kline_days = int(data_cfg.get("lookback_days", 180) or 180)
    max_news = int(data_cfg.get("max_news_per_day", 12) or 12)

    data_dir = root_dir / "data"
    symbols = [_symbol_to_dict(s) for s in iter_enabled_symbols(cfg)]
    if not symbols:
        raise SystemExit("No enabled symbols in config.yaml")

    analysis_cfg = cfg.get("analysis", {}) or {}
    weights = analysis_cfg.get("weights", None) or {"macro": 0.3, "symbol": 0.3, "market": 0.4}

    def _fallback_kline(sym_id: str, day_date: str) -> list[dict[str, Any]]:
        # Prefer the previously persisted full kline (from day payload).
        day = read_json(data_dir / "symbols" / sym_id / "days" / f"{day_date}.json", default=None)
        if isinstance(day, dict):
            k = day.get("kline")
            if isinstance(k, list) and k:
                return [x for x in k if isinstance(x, dict)]

        # Fallback to history bars if present (trading days only).
        hist = read_json(data_dir / "symbols" / sym_id / "history.json", default=None)
        if isinstance(hist, dict):
            hs = hist.get("days")
            if isinstance(hs, list) and hs:
                out: list[dict[str, Any]] = []
                for r in hs[-400:]:
                    if not isinstance(r, dict):
                        continue
                    d = r.get("date")
                    if not d:
                        continue
                    out.append(
                        {
                            "date": d,
                            "open": r.get("open"),
                            "high": r.get("high"),
                            "low": r.get("low"),
                            "close": r.get("close"),
                            "volume": r.get("volume"),
                            "open_interest": r.get("open_interest"),
                        }
                    )
                return out
        return []

    for sym in symbols:
        logging.info("Updating %s %s", sym["id"], date)
        kline = fetch_kline(cfg, sym, end_date=date, days=kline_days)
        if not kline:
            fb = _fallback_kline(sym["id"], date)
            if fb:
                logging.info("Using fallback kline for %s (%d bars)", sym["id"], len(fb))
                kline = fb
        bundle = fetch_news_bundle(cfg, sym, date=date, max_items=max_news)

        analyzed_global = analyze_news_items(cfg, bundle.get("global", []) or [])
        analyzed_symbol = analyze_news_items(cfg, bundle.get("symbol", []) or [])
        analyzed_merged = analyze_news_items(cfg, bundle.get("merged", []) or [])

        asset = str(sym.get("asset") or "futures").strip().lower() or "futures"
        extras = None
        fund_sig = {"status": "missing", "asof": "", "signals": {}}
        if asset == "futures":
            # Many AKShare "extras" datasets (basis, roll yield, rank tables) are
            # published on trading days. When market is closed or the kline source
            # is delayed, align extras to the latest available trading bar.
            extras_asof = (kline[-1].get("date") if kline else None) or date
            extras = fetch_extras(cfg, sym, extras_asof)
            fund_sig = fundamentals_signals_for_llm(extras)

        macro = macro_agent(cfg, date=date, analyzed_global_news=analyzed_global)
        sym_news = symbol_news_agent(cfg, symbol=sym, date=date, analyzed_symbol_news=analyzed_symbol)
        market = None
        if kline:
            market = market_agent_llm(cfg, symbol=sym, kline=kline, date=date, fundamentals=fund_sig) or market_agent(cfg, kline=kline, date=date)

        agents_payload: Dict[str, Any] = {
            "weights": {"macro": float(weights.get("macro", 0.3)), "symbol": float(weights.get("symbol", 0.3)), "market": float(weights.get("market", 0.4))},
            "macro": {
                "status": "ok",
                "index": macro.index,
                "band": macro.band,
                "confidence": macro.confidence,
                "mode": macro.mode,
                "rationale": macro.rationale,
            },
            "symbol": {
                "status": "ok",
                "index": sym_news.index,
                "band": sym_news.band,
                "confidence": sym_news.confidence,
                "mode": sym_news.mode,
                "rationale": sym_news.rationale,
            },
        }

        plans_payload: Dict[str, Any] | None = None
        if not kline:
            agents_payload["market"] = {"status": "unavailable", "reason": "missing kline"}
            agents_payload["final"] = {"status": "unavailable", "reason": "missing market signal"}
            plans_payload = {"status": "unavailable", "reason": "missing kline"}
        elif market is None:
            agents_payload["market"] = {"status": "skipped", "reason": "market closed / non-trading day"}
            agents_payload["final"] = {"status": "skipped", "reason": "market closed / non-trading day"}
            plans_payload = {"status": "skipped", "reason": "market closed / non-trading day"}
        else:
            agents_payload["market"] = {
                "status": "ok",
                "index": market.index,
                "band": market.band,
                "confidence": market.confidence,
                "mode": market.mode,
                "rationale": market.rationale,
            }
            final = combine_final(macro=macro, symbol_news=sym_news, market=market, weights=weights)
            agents_payload["final"] = {
                "status": "ok",
                "index": final.index,
                "band": final.band,
                "confidence": final.confidence,
                "mode": final.mode,
                "rationale": final.rationale,
            }
            plans_payload = trade_plan(symbol=sym, kline=kline, final_score=final)

        if asset == "futures":
            update_fundamentals(data_dir=data_dir, symbol=sym, extras=extras, tz_label=tz_label)
        upsert_symbol_day(
            data_dir=data_dir,
            symbol=sym,
            date=date,
            kline=kline,
            analyzed_news=analyzed_merged,
            extras=extras,
            agents=agents_payload,
            plans=plans_payload,
            tz_label=tz_label,
        )

    write_latest(data_dir, date, tz_label, symbols)


def cmd_build_site(cfg: Dict[str, Any], *, root_dir: Path) -> None:
    build_site(cfg, root_dir=root_dir)


def main() -> None:
    argv = sys.argv[1:]
    config_path = "config.yaml"
    # Allow --config to appear anywhere (argparse + subparsers doesn't support this well).
    if "--config" in argv:
        i = argv.index("--config")
        if i + 1 >= len(argv):
            raise SystemExit("--config requires a value")
        config_path = argv[i + 1]
        del argv[i : i + 2]
    else:
        for a in list(argv):
            if a.startswith("--config="):
                config_path = a.split("=", 1)[1]
                argv.remove(a)
                break

    parser = argparse.ArgumentParser(prog="futusense")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_upd = sub.add_parser("update-data", help="Fetch/analyze/aggregate data")
    p_upd.add_argument("--date", default=None, help="YYYY-MM-DD, default today in configured timezone")

    sub.add_parser("build-site", help="Generate docs/ static site")
    sub.add_parser("run-all", help="Update data then build site")

    args = parser.parse_args(argv)
    root_dir = Path(__file__).resolve().parents[1]
    cfg = load_yaml(root_dir / config_path)
    setup_logging("INFO")

    tz = (cfg.get("data", {}) or {}).get("timezone", "Asia/Shanghai")
    date = parse_date(getattr(args, "date", None), tz)

    if args.cmd == "update-data":
        cmd_update_data(cfg, root_dir=root_dir, date=date)
    elif args.cmd == "build-site":
        cmd_build_site(cfg, root_dir=root_dir)
    elif args.cmd == "run-all":
        cmd_update_data(cfg, root_dir=root_dir, date=date)
        cmd_build_site(cfg, root_dir=root_dir)
    else:
        raise SystemExit("Unknown command")


if __name__ == "__main__":
    main()
