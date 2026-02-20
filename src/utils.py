from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def load_yaml(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(path: str | Path, default: Any) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_text(path: str | Path, text: str) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def copy_file(src: str | Path, dst: str | Path) -> None:
    src_p = Path(src)
    dst_p = Path(dst)
    ensure_dir(dst_p.parent)
    dst_p.write_bytes(src_p.read_bytes())


def today_in_tz(tz_name: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now()


def parse_date(date_str: Optional[str], tz_name: str) -> str:
    if date_str:
        return date_str
    return today_in_tz(tz_name).strftime("%Y-%m-%d")


def iso_datetime_now(tz_name: str) -> str:
    return today_in_tz(tz_name).strftime("%Y-%m-%d %H:%M")


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def sentiment_band(v: float) -> str:
    if v > 0.2:
        return "bull"
    if v < -0.2:
        return "bear"
    return "neutral"


def correlation(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    x = [float(v) for v in xs]
    y = [float(v) for v in ys]
    mx = sum(x) / len(x)
    my = sum(y) / len(y)
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    denx = sum((xi - mx) ** 2 for xi in x)
    deny = sum((yi - my) ** 2 for yi in y)
    if denx <= 0.0 or deny <= 0.0:
        return 0.0
    return float(num / ((denx * deny) ** 0.5))


@dataclass(frozen=True)
class Symbol:
    id: str
    name: str
    keywords: list[str]


def iter_enabled_symbols(cfg: Dict[str, Any]) -> Iterable[Symbol]:
    for s in cfg.get("symbols", []) or []:
        if not s.get("enabled", True):
            continue
        yield Symbol(id=s["id"], name=s["name"], keywords=list(s.get("keywords", []) or []))
