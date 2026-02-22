from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .aggregator import compute_corr20
from .utils import copy_file, ensure_dir, read_json, write_json, write_text


def build_site(cfg: Dict[str, Any], *, root_dir: Path) -> None:
    data_dir = root_dir / "data"
    docs_dir = root_dir / "docs"

    ensure_dir(docs_dir)
    write_text(docs_dir / ".nojekyll", "")

    env = Environment(
        loader=FileSystemLoader(str(root_dir / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )
    site_cfg = cfg.get("site", {}) or {}
    raw_base_path = str(site_cfg.get("base_path") or "").strip()
    # Default to relative paths so the site works both at domain root and under
    # a GitHub Pages project path (e.g. /<repo>/).
    if raw_base_path in ("", "/"):
        base_path_root = "."
    else:
        base_path_root = raw_base_path.rstrip("/")

    # Detail pages live under docs/s/*.html; when using relative paths we need
    # to go up one directory so ./api doesn't become /s/api.
    base_path_detail = ".." if base_path_root == "." else base_path_root

    latest = read_json(data_dir / "latest.json", default={"symbols": [], "date": "", "updated_at": ""})

    index_tpl = env.get_template("index.html.j2")
    index_html = index_tpl.render(site=site_cfg, base_path=base_path_root, latest=latest)
    write_text(docs_dir / "index.html", index_html)

    detail_tpl = env.get_template("detail.html.j2")
    ensure_dir(docs_dir / "s")

    # API copies
    ensure_dir(docs_dir / "api")
    write_json(docs_dir / "api" / "latest.json", latest)
    ensure_dir(docs_dir / "api" / "symbols")
    ensure_dir(docs_dir / "api" / "exports")

    # static
    ensure_dir(docs_dir / "static")
    copy_file(root_dir / "static" / "app.js", docs_dir / "static" / "app.js")
    copy_file(root_dir / "static" / "styles.css", docs_dir / "static" / "styles.css")

    for sym in latest.get("symbols", []) or []:
        sym_id = sym["id"]
        sym_name = sym["name"]
        history = read_json(data_dir / "symbols" / sym_id / "history.json", default={"symbol": sym, "days": []})
        corr20 = compute_corr20(history.get("days", []) or [])

        # Prefer loading today's payload (even if market closed) so the detail
        # page can display fresh news; price may be marked stale.
        global_latest_date = str(latest.get("date") or "")
        latest_day_payload = None
        if global_latest_date:
            latest_day_payload = read_json(
                data_dir / "symbols" / sym_id / "days" / f"{global_latest_date}.json",
                default=None,
            )
        latest_date_for_symbol = global_latest_date if latest_day_payload else (history.get("days", []) or [])[-1]["date"] if (history.get("days") or []) else ""
        latest_is_stale = bool((latest_day_payload or {}).get("is_stale") or (((latest_day_payload or {}).get("price") or {}).get("is_stale")))
        meta = {
            "symbol": {"id": sym_id, "name": sym_name},
            "updated_at": latest.get("updated_at", ""),
            "corr20": round(float(corr20), 3),
            "days": history.get("days", []) or [],
            "latest_date": latest_date_for_symbol,
            "latest_is_stale": latest_is_stale,
        }

        sym_api_dir = docs_dir / "api" / "symbols" / sym_id
        ensure_dir(sym_api_dir / "days")
        write_json(sym_api_dir / "index.json", meta)

        # daily payloads (for calendar/news)
        for d in meta["days"]:
            date = d["date"]
            day_payload = read_json(data_dir / "symbols" / sym_id / "days" / f"{date}.json", default=None)
            if day_payload:
                write_json(sym_api_dir / "days" / f"{date}.json", day_payload)

        # Also copy today's payload even if it's not a trading day (not in history).
        if global_latest_date and latest_day_payload:
            write_json(sym_api_dir / "days" / f"{global_latest_date}.json", latest_day_payload)

        export_src = data_dir / "exports" / f"{sym_id}.csv"
        if export_src.exists():
            copy_file(export_src, docs_dir / "api" / "exports" / f"{sym_id}.csv")

        detail_html = detail_tpl.render(
            site=site_cfg,
            base_path=base_path_detail,
            symbol={"id": sym_id, "name": sym_name},
        )
        write_text(docs_dir / "s" / f"{sym_id}.html", detail_html)
