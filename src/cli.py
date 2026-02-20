from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict

from .analyzer import analyze_news_items
from .crawler_news import fetch_news
from .crawler_price import fetch_kline
from .aggregator import upsert_symbol_day, write_latest
from .generator import build_site
from .utils import iso_datetime_now, iter_enabled_symbols, load_yaml, parse_date, setup_logging


def _symbol_to_dict(s) -> Dict[str, Any]:
    d: Dict[str, Any] = {"id": s.id, "name": s.name, "keywords": s.keywords}
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

    data_dir = root_dir / "data"
    symbols = [_symbol_to_dict(s) for s in iter_enabled_symbols(cfg)]
    if not symbols:
        raise SystemExit("No enabled symbols in config.yaml")

    for sym in symbols:
        logging.info("Updating %s %s", sym["id"], date)
        kline = fetch_kline(cfg, sym, end_date=date, days=kline_days)
        news = fetch_news(cfg, sym, date)
        analyzed = analyze_news_items(cfg, news)
        upsert_symbol_day(
            data_dir=data_dir,
            symbol=sym,
            date=date,
            kline=kline,
            analyzed_news=analyzed,
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
