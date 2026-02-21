from __future__ import annotations

import hashlib
import random
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List

import feedparser

from .cleaner import clean_text


def _seed(symbol_id: str, date: str) -> int:
    h = hashlib.sha256(f"{symbol_id}:{date}".encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _parse_entry_source(title: str) -> str:
    # Google News RSS titles are often like: "<headline> - <publisher>".
    parts = [p.strip() for p in (title or "").rsplit(" - ", 1)]
    if len(parts) == 2 and parts[1]:
        return parts[1]
    return "Google News"


def _gnews_rss_url(query: str, *, hl: str, gl: str, ceid: str) -> str:
    q = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"


def _fetch_gnews_rss(
    *,
    query: str,
    max_items: int,
    hl: str,
    gl: str,
    ceid: str,
) -> List[Dict[str, Any]]:
    url = _gnews_rss_url(query, hl=hl, gl=gl, ceid=ceid)
    feed = feedparser.parse(url)
    out: List[Dict[str, Any]] = []
    for e in (feed.entries or [])[: max_items * 3]:
        title = clean_text(getattr(e, "title", ""))
        link = clean_text(getattr(e, "link", ""))
        published = getattr(e, "published", "") or getattr(e, "updated", "") or ""
        if not title:
            continue
        out.append(
            {
                "title": title,
                "url": link,
                "source": _parse_entry_source(title),
                "published_at": clean_text(published) or "",
                "content": "",
            }
        )
        if len(out) >= max_items:
            break
    return out


def fetch_news(cfg: Dict[str, Any], symbol: Dict[str, Any], date: str) -> List[Dict[str, Any]]:
    provider = (cfg.get("news", {}) or {}).get("provider", "mock")
    max_n = int(((cfg.get("data", {}) or {}).get("max_news_per_day", 12)) or 12)

    # Prefer returning empty over mock when caller asked for real sources.
    if provider not in {"mock", "rss", "gnews"}:
        provider = "gnews"

    if provider == "gnews":
        gcfg = ((cfg.get("news", {}) or {}).get("gnews", {}) or {})
        want_lang = str(gcfg.get("language", "both") or "both").lower()
        # Heuristic query: include the symbol name + a few keywords.
        kws = list(symbol.get("keywords") or [])
        base_terms = [symbol.get("name") or symbol.get("id")]
        # Keep query short to avoid overly broad results.
        for k in kws[:4]:
            if k and k not in base_terms:
                base_terms.append(k)
        query = " ".join([t for t in base_terms if t])

        items: List[Dict[str, Any]] = []
        seen = set()

        if want_lang in {"zh", "both", "cn", "zh-cn"}:
            hl = str(gcfg.get("zh_hl", "zh-CN"))
            gl = str(gcfg.get("zh_gl", "CN"))
            ceid = str(gcfg.get("zh_ceid", "CN:zh-Hans"))
            for it in _fetch_gnews_rss(query=query, max_items=max_n, hl=hl, gl=gl, ceid=ceid):
                k = (it.get("title") or "") + "|" + (it.get("url") or "")
                if k in seen:
                    continue
                seen.add(k)
                items.append(it)
                if len(items) >= max_n:
                    return items

        if want_lang in {"en", "both", "us", "en-us"}:
            hl = str(gcfg.get("en_hl", "en-US"))
            gl = str(gcfg.get("en_gl", "US"))
            ceid = str(gcfg.get("en_ceid", "US:en"))
            for it in _fetch_gnews_rss(query=query, max_items=max_n, hl=hl, gl=gl, ceid=ceid):
                k = (it.get("title") or "") + "|" + (it.get("url") or "")
                if k in seen:
                    continue
                seen.add(k)
                items.append(it)
                if len(items) >= max_n:
                    return items

        return items[:max_n]

    if provider == "rss":
        urls = ((cfg.get("news", {}) or {}).get("rss", {}) or {}).get("urls", []) or []
        if urls:
            items: List[Dict[str, Any]] = []
            for url in urls:
                feed = feedparser.parse(url)
                for e in feed.entries[: max_n * 2]:
                    title = clean_text(getattr(e, "title", ""))
                    link = clean_text(getattr(e, "link", ""))
                    published = getattr(e, "published", "") or getattr(e, "updated", "") or ""
                    source = clean_text(getattr(feed.feed, "title", "RSS"))
                    if not title:
                        continue
                    items.append(
                        {
                            "title": title,
                            "url": link,
                            "source": source,
                            "published_at": clean_text(published) or "",
                            "content": "",
                        }
                    )
            return items[:max_n]

        # Explicitly do not fallback to mock when RSS isn't configured.
        return []

    if provider == "mock":
        return []

    return []
