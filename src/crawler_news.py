from __future__ import annotations

from datetime import date as _date
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import math
import re
import time
import urllib.parse
from html.parser import HTMLParser
from typing import Any, Dict, List

import feedparser
import requests

from .cleaner import clean_text


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_a = False
        self._href: str | None = None
        self._buf: list[str] = []
        self.anchors: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() != "a":
            return
        href = None
        for k, v in attrs:
            if k.lower() == "href" and v:
                href = str(v)
                break
        if not href:
            return
        self._in_a = True
        self._href = href
        self._buf = []

    def handle_data(self, data: str):
        if self._in_a and data:
            self._buf.append(data)

    def handle_endtag(self, tag: str):
        if tag.lower() != "a":
            return
        if not self._in_a:
            return
        text = clean_text(" ".join(self._buf))
        href = clean_text(self._href or "")
        if text and href:
            self.anchors.append({"title": text, "href": href})
        self._in_a = False
        self._href = None
        self._buf = []


def _fetch_html(url: str, *, timeout: int = 15, user_agent: str | None = None) -> str:
    headers = {
        "User-Agent": user_agent
        or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()

    content = resp.content or b""

    def normalize_enc(enc: str | None) -> str | None:
        if not enc:
            return None
        e = str(enc).strip().strip('"').strip("'")
        if not e:
            return None
        e = e.lower()
        if e in {"gb2312", "gbk"}:
            return "gb18030"
        if e in {"utf8"}:
            return "utf-8"
        return e

    def sniff_meta_charset(buf: bytes) -> str | None:
        try:
            head = buf[:8192].decode("ascii", errors="ignore").lower()
        except Exception:
            return None
        m = re.search(r"charset\s*=\s*([a-z0-9_\-]+)", head)
        if m:
            return normalize_enc(m.group(1))
        return None

    # Prefer declared encoding when it's meaningful. Requests may default to
    # ISO-8859-1 for text/* without charset, which is wrong for many CN sites.
    declared = normalize_enc(resp.encoding)
    if declared and declared not in {"iso-8859-1", "ascii"}:
        try:
            return content.decode(declared, errors="replace")
        except Exception:
            pass

    candidates: list[str] = []
    meta_enc = sniff_meta_charset(content)
    if meta_enc:
        candidates.append(meta_enc)
    apparent = normalize_enc(getattr(resp, "apparent_encoding", None))
    if apparent:
        candidates.append(apparent)
    # Common fallbacks.
    candidates.extend(["utf-8", "gb18030"])

    tried: set[str] = set()
    best: str | None = None
    best_score: int | None = None
    for enc in candidates:
        enc = normalize_enc(enc) or "utf-8"
        if enc in tried:
            continue
        tried.add(enc)
        try:
            text = content.decode(enc, errors="replace")
        except Exception:
            continue
        # Heuristic: fewer replacement chars indicates better decoding.
        score = text.count("\ufffd")
        if best is None or (best_score is not None and score < best_score):
            best = text
            best_score = score
            if score == 0:
                break

    return best or content.decode("utf-8", errors="replace")


def _extract_links(html: str, *, base_url: str) -> list[dict[str, str]]:
    p = _AnchorParser()
    p.feed(html)
    out: list[dict[str, str]] = []
    for a in p.anchors:
        href = a.get("href", "")
        title = a.get("title", "")
        if not href or not title:
            continue
        href = urllib.parse.urljoin(base_url, href)
        out.append({"title": title, "url": href})
    return out


def _match_keywords(title: str, symbol: Dict[str, Any]) -> bool:
    t = (title or "").lower()
    if not t:
        return False
    # Always allow if the title contains the symbol name.
    name = str(symbol.get("name") or symbol.get("id") or "").strip().lower()
    if name and name in t:
        return True
    kws = [str(k).strip().lower() for k in (symbol.get("keywords") or []) if str(k).strip()]
    return any(k and k in t for k in kws)


def _dedup_items(items: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    seen: set[str] = set()
    out: list[Dict[str, Any]] = []
    for it in items:
        k = clean_text((it.get("url") or "") + "|" + (it.get("title") or ""))
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _get_news_max_age_days(cfg: Dict[str, Any]) -> int:
    ncfg = (cfg.get("news", {}) or {})
    dcfg = (cfg.get("data", {}) or {})
    v = ncfg.get("max_age_days", None)
    if v is None:
        v = dcfg.get("news_max_age_days", None)
    try:
        days = int(v) if v is not None else 30
    except Exception:
        days = 30
    if days < 0:
        return 0
    return min(days, 365)


def _get_news_weighting_half_life_days(cfg: Dict[str, Any]) -> int:
    ncfg = (cfg.get("news", {}) or {})
    wcfg = (ncfg.get("weighting", {}) or {})
    v = wcfg.get("half_life_days", None)
    try:
        if v is not None:
            d = int(v)
            return max(1, min(d, 365))
    except Exception:
        pass

    # Backward compatible default: derive from max_age_days.
    # If max_age_days=30, half-life defaults to ~10 days.
    max_age = _get_news_max_age_days(cfg)
    if max_age <= 0:
        return 10
    return max(1, min(int(round(max_age / 3.0)), 365))


def _get_news_weighting_params(cfg: Dict[str, Any]) -> Dict[str, Any]:
    ncfg = (cfg.get("news", {}) or {})
    wcfg = (ncfg.get("weighting", {}) or {})

    half_life_days = _get_news_weighting_half_life_days(cfg)

    try:
        min_weight = float(wcfg.get("min_weight", 0.0005) or 0.0005)
    except Exception:
        min_weight = 0.0005
    min_weight = float(max(0.0, min(min_weight, 1.0)))

    try:
        fresh_boost_days = int(wcfg.get("fresh_boost_days", 1) or 1)
    except Exception:
        fresh_boost_days = 1
    fresh_boost_days = max(0, min(fresh_boost_days, 30))

    try:
        fresh_boost = float(wcfg.get("fresh_boost", 1.25) or 1.25)
    except Exception:
        fresh_boost = 1.25
    fresh_boost = float(max(1.0, min(fresh_boost, 3.0)))

    scfg = (ncfg.get("supersede", {}) or {})
    supersede_enabled = bool(scfg.get("enabled", True))

    return {
        "half_life_days": half_life_days,
        "min_weight": min_weight,
        "fresh_boost_days": fresh_boost_days,
        "fresh_boost": fresh_boost,
        "supersede_enabled": supersede_enabled,
    }


def _parse_published_date(published_at: str) -> _date | None:
    s = (published_at or "").strip()
    if not s:
        return None
    # Common cases:
    # - RFC2822: "Fri, 20 Feb 2026 23:30:02 GMT"
    # - ISO date: "2026-02-23"
    # - ISO datetime: "2026-02-23T09:06:00"
    try:
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            return _date.fromisoformat(s)
    except Exception:
        pass

    try:
        dt = parsedate_to_datetime(s)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date()
    except Exception:
        pass

    try:
        dt2 = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt2.tzinfo is None:
            dt2 = dt2.replace(tzinfo=timezone.utc)
        return dt2.astimezone(timezone.utc).date()
    except Exception:
        return None

def _compute_recency_weight(cfg: Dict[str, Any], *, published_at: str, date: str) -> tuple[float, int | None]:
    params = _get_news_weighting_params(cfg)
    half_life_days = float(params["half_life_days"])
    min_weight = float(params["min_weight"])
    fresh_boost_days = int(params["fresh_boost_days"])
    fresh_boost = float(params["fresh_boost"])

    try:
        target = _date.fromisoformat(str(date))
    except Exception:
        return 1.0, None

    pub = _parse_published_date(published_at)
    if pub is None:
        # Unknown timestamp: keep, but do not let it dominate.
        return 0.25, None

    age_days = (target - pub).days
    if age_days < 0:
        age_days = 0

    # Exponential decay with half-life.
    # w = 0.5^(age/half_life)
    w = math.pow(0.5, float(age_days) / max(1.0, half_life_days))
    if fresh_boost_days > 0 and age_days <= fresh_boost_days:
        w *= fresh_boost

    w = float(min(1.0, max(min_weight, w)))
    return w, int(age_days)


def _classify_supersede_topic(title: str) -> tuple[str, int] | None:
    """Classify a headline into (topic_key, direction).

    Only for a few macro policy-like topics where newer opposite actions
    can supersede older ones (e.g., rate hike -> later rate cut).
    """
    t = clean_text(title).lower()
    if not t:
        return None

    # Policy rate: many CN headlines only say "加息/降息" without "利率".
    rate_markers = ["利率", "rates", "rate ", "rate-", "rate", "加息", "降息", "升息"]
    has_rate = any(k in t for k in rate_markers)
    if has_rate:
        up = any(k in t for k in ["加息", "升息", "上调", "提高", "raise", "hike", "tighten"])
        down = any(k in t for k in ["降息", "下调", "降低", "cut", "lower", "ease"])
        if up ^ down:
            direction = 1 if up else -1
            entity = "rate"
            if any(k in t for k in ["美联储", "fed", "fomc"]):
                entity = "fed"
            elif any(k in t for k in ["欧洲央行", "ecb"]):
                entity = "ecb"
            elif any(k in t for k in ["人民银行", "央行", "pboc"]):
                entity = "pboc"
            return f"{entity}:policy_rate", direction

    # Tariff: require tariff keyword and directional cue
    if any(k in t for k in ["关税", "tariff"]):
        up = any(k in t for k in ["加征", "上调", "提高", "raise", "impose", "increase"])
        down = any(k in t for k in ["取消", "下调", "降低", "reduce", "cut", "suspend", "roll back"])
        if up ^ down:
            direction = 1 if up else -1
            return "global:tariff", direction

    return None


def _apply_supersede(cfg: Dict[str, Any], items: list[Dict[str, Any]], *, date: str) -> list[Dict[str, Any]]:
    params = _get_news_weighting_params(cfg)
    if not params.get("supersede_enabled", True):
        return items

    # Find the latest item per topic; if latest direction differs from an older one,
    # we consider the older one superseded and set its weight to 0.
    latest: dict[str, dict[str, Any]] = {}
    latest_dir: dict[str, int] = {}
    latest_pub: dict[str, _date] = {}
    for it in items:
        cls = _classify_supersede_topic(str((it or {}).get("title") or ""))
        if not cls:
            continue
        topic, direction = cls
        pub = _parse_published_date(str((it or {}).get("published_at") or ""))
        if pub is None:
            continue
        prev = latest_pub.get(topic)
        if prev is None or pub > prev:
            latest_pub[topic] = pub
            latest[topic] = it
            latest_dir[topic] = direction

    if not latest_pub:
        return items

    out: list[Dict[str, Any]] = []
    for it in items:
        cls = _classify_supersede_topic(str((it or {}).get("title") or ""))
        if not cls:
            out.append(it)
            continue
        topic, direction = cls
        pub = _parse_published_date(str((it or {}).get("published_at") or ""))
        if pub is None:
            out.append(it)
            continue
        latest_p = latest_pub.get(topic)
        if latest_p and pub < latest_p and latest_dir.get(topic) is not None and direction != int(latest_dir[topic]):
            it2 = {**it}
            it2["weight"] = 0.0
            it2["superseded"] = True
            out.append(it2)
            continue
        out.append(it)
    return out


def _annotate_and_rank_items(cfg: Dict[str, Any], items: list[Dict[str, Any]], *, date: str, max_items: int) -> list[Dict[str, Any]]:
    if not items:
        return []
    max_items = max(0, int(max_items))
    if max_items == 0:
        return []

    tmp: list[Dict[str, Any]] = []
    for it in items:
        it2 = {**(it or {})}
        w, age_days = _compute_recency_weight(cfg, published_at=str(it2.get("published_at") or ""), date=date)
        it2["weight"] = float(it2.get("weight") or w)
        if age_days is not None:
            it2["age_days"] = int(age_days)
        tmp.append(it2)

    tmp = _apply_supersede(cfg, tmp, date=date)

    tmp = [it for it in tmp if float(it.get("weight") or 0.0) > 0.0]

    def _sort_key(x: Dict[str, Any]):
        w = float(x.get("weight") or 0.0)
        age = x.get("age_days")
        try:
            agei = int(age) if age is not None else 10**9
        except Exception:
            agei = 10**9
        # Higher weight first; then newer first.
        return (-w, agei)

    tmp.sort(key=_sort_key)
    return tmp[:max_items]


def _web_items_from_links(
    links: list[dict[str, str]],
    *,
    source: str,
    symbol: Dict[str, Any],
    max_items: int,
    date: str,
    url_allow: str | None = None,
) -> list[Dict[str, Any]]:
    out: list[Dict[str, Any]] = []
    allow_re = re.compile(url_allow) if url_allow else None
    for it in links:
        title = clean_text(it.get("title", ""))
        url = clean_text(it.get("url", ""))
        if not title or not url:
            continue
        if allow_re and not allow_re.search(url):
            continue
        if len(title) < 8:
            continue
        if not _match_keywords(title, symbol):
            continue
        out.append(
            {
                "title": title,
                "url": url,
                "source": source,
                "published_at": date,
                "content": "",
            }
        )
        if len(out) >= max_items:
            break
    return out


def _fetch_news_jin10(cfg: Dict[str, Any], symbol: Dict[str, Any], date: str, max_items: int) -> list[Dict[str, Any]]:
    wcfg = ((cfg.get("news", {}) or {}).get("web", {}) or {}).get("jin10", {}) or {}
    urls = wcfg.get("urls") or ["https://xnews.jin10.com/"]
    timeout = int(wcfg.get("timeout_seconds", 15) or 15)
    ua = str(wcfg.get("user_agent", "") or "").strip() or None
    url_allow = str(wcfg.get("url_allow", r"xnews\\.jin10\\.com/(details|\d)") or r"xnews\\.jin10\\.com/")
    items: list[Dict[str, Any]] = []
    for url in urls:
        try:
            html = _fetch_html(url, timeout=timeout, user_agent=ua)
            links = _extract_links(html, base_url=url)
            items.extend(
                _web_items_from_links(
                    links,
                    source="金十数据",
                    symbol=symbol,
                    max_items=max_items,
                    date=date,
                    url_allow=url_allow,
                )
            )
        except Exception:
            continue
        if len(items) >= max_items:
            break
        time.sleep(0.2)
    return _dedup_items(items)[:max_items]


def _fetch_news_eastmoney(cfg: Dict[str, Any], symbol: Dict[str, Any], date: str, max_items: int) -> list[Dict[str, Any]]:
    wcfg = ((cfg.get("news", {}) or {}).get("web", {}) or {}).get("eastmoney_futures", {}) or {}
    urls = wcfg.get("urls") or ["https://futures.eastmoney.com/"]
    timeout = int(wcfg.get("timeout_seconds", 15) or 15)
    ua = str(wcfg.get("user_agent", "") or "").strip() or None
    # Keep only article links (avoid quote/contract links).
    url_allow = str(wcfg.get("url_allow", r"eastmoney\\.com/a/\d{16,}\\.html") or r"eastmoney")

    items: list[Dict[str, Any]] = []
    for url in urls:
        try:
            html = _fetch_html(url, timeout=timeout, user_agent=ua)
            links = _extract_links(html, base_url=url)
            items.extend(
                _web_items_from_links(
                    links,
                    source="东方财富期货",
                    symbol=symbol,
                    max_items=max_items,
                    date=date,
                    url_allow=url_allow,
                )
            )
        except Exception:
            continue
        if len(items) >= max_items:
            break
        time.sleep(0.2)
    return _dedup_items(items)[:max_items]


def _fetch_news_qhrb(cfg: Dict[str, Any], symbol: Dict[str, Any], date: str, max_items: int) -> list[Dict[str, Any]]:
    wcfg = ((cfg.get("news", {}) or {}).get("web", {}) or {}).get("qhrb", {}) or {}
    urls = wcfg.get("urls") or ["https://www.qhrb.com.cn/"]
    timeout = int(wcfg.get("timeout_seconds", 15) or 15)
    ua = str(wcfg.get("user_agent", "") or "").strip() or None
    url_allow = str(wcfg.get("url_allow", r"qhrb\\.com\\.cn") or r"qhrb")

    items: list[Dict[str, Any]] = []
    for url in urls:
        try:
            html = _fetch_html(url, timeout=timeout, user_agent=ua)
            links = _extract_links(html, base_url=url)
            items.extend(
                _web_items_from_links(
                    links,
                    source="期货日报",
                    symbol=symbol,
                    max_items=max_items,
                    date=date,
                    url_allow=url_allow,
                )
            )
        except Exception:
            continue
        if len(items) >= max_items:
            break
        time.sleep(0.2)
    return _dedup_items(items)[:max_items]


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


def _fetch_global_gnews(cfg: Dict[str, Any], *, max_items: int) -> list[Dict[str, Any]]:
    gcfg = ((cfg.get("news", {}) or {}).get("gnews", {}) or {})
    glob = ((cfg.get("news", {}) or {}).get("global", {}) or {})
    enabled = bool(glob.get("enabled", True))
    if not enabled or max_items <= 0:
        return []

    terms = glob.get("terms") or []
    if not isinstance(terms, list):
        terms = []
    terms = [str(t).strip() for t in terms if str(t).strip()]
    if not terms:
        # Sensible defaults: macro/commodity common drivers.
        terms = [
            "特朗普 关税",
            "美联储 利率",
            "通胀",
            "美元指数",
            "地缘冲突",
            "OPEC 原油",
            "中国 经济 数据",
        ]

    want_lang = str(gcfg.get("language", "both") or "both").lower()
    query = " OR ".join([f'({t})' for t in terms[:8]])

    items: list[Dict[str, Any]] = []
    seen: set[str] = set()

    def _add(arr: list[Dict[str, Any]]):
        nonlocal items
        for it in arr:
            k = (it.get("title") or "") + "|" + (it.get("url") or "")
            if k in seen:
                continue
            seen.add(k)
            it = {**it}
            it["source"] = it.get("source") or "Google News"
            items.append(it)
            if len(items) >= max_items:
                return

    if want_lang in {"zh", "both", "cn", "zh-cn"}:
        hl = str(gcfg.get("zh_hl", "zh-CN"))
        gl = str(gcfg.get("zh_gl", "CN"))
        ceid = str(gcfg.get("zh_ceid", "CN:zh-Hans"))
        _add(_fetch_gnews_rss(query=query, max_items=max_items, hl=hl, gl=gl, ceid=ceid))
        if len(items) >= max_items:
            return items[:max_items]

    if want_lang in {"en", "both", "us", "en-us"}:
        hl = str(gcfg.get("en_hl", "en-US"))
        gl = str(gcfg.get("en_gl", "US"))
        ceid = str(gcfg.get("en_ceid", "US:en"))
        _add(_fetch_gnews_rss(query=query, max_items=max_items, hl=hl, gl=gl, ceid=ceid))

    return items[:max_items]


def fetch_global_news(cfg: Dict[str, Any], *, date: str, max_items: int) -> list[Dict[str, Any]]:
    """Fetch macro news that should be shared across all symbols."""
    items = _fetch_global_gnews(cfg, max_items=max_items)
    items = _dedup_items(items)
    return _annotate_and_rank_items(cfg, items, date=date, max_items=max_items)


def fetch_symbol_news(cfg: Dict[str, Any], symbol: Dict[str, Any], *, date: str, max_items: int) -> list[Dict[str, Any]]:
    """Fetch symbol-specific news (without global macro mixing)."""
    provider = (cfg.get("news", {}) or {}).get("provider", "gnews")
    # For symbol-specific, treat "multi" as: gnews + web.
    if provider == "multi":
        provider = "gnews+web"

    items: list[Dict[str, Any]] = []

    if provider in {"gnews", "gnews+web"}:
        # Reuse existing gnews logic by calling this module's fetch_news with gnews,
        # but disable global mixing by forcing global.max_items=0.
        cfg2 = {
            **cfg,
            "news": {
                **(cfg.get("news", {}) or {}),
                "provider": "gnews",
                "global": {**(((cfg.get("news", {}) or {}).get("global", {}) or {})), "max_items": 0},
            },
        }
        try:
            items.extend(fetch_news(cfg2, symbol, date))
        except Exception:
            pass

    if provider in {"web", "gnews+web"}:
        try:
            items.extend(_fetch_news_jin10(cfg, symbol, date, max(3, max_items)))
        except Exception:
            pass
        try:
            items.extend(_fetch_news_qhrb(cfg, symbol, date, max(3, max_items)))
        except Exception:
            pass
        try:
            items.extend(_fetch_news_eastmoney(cfg, symbol, date, max(3, max_items)))
        except Exception:
            pass

    items = _dedup_items(items)
    return _annotate_and_rank_items(cfg, items, date=date, max_items=max_items)


def fetch_news_bundle(cfg: Dict[str, Any], symbol: Dict[str, Any], *, date: str, max_items: int) -> Dict[str, Any]:
    """Return global + symbol news + merged list (deduped)."""
    global_cap = int((((cfg.get("news", {}) or {}).get("global", {}) or {}).get("max_items", 6)) or 6)
    global_cap = max(0, min(global_cap, max_items))
    sym_cap = max_items

    global_items = fetch_global_news(cfg, date=date, max_items=global_cap)
    symbol_items = fetch_symbol_news(cfg, symbol, date=date, max_items=sym_cap)

    merged: list[Dict[str, Any]] = []
    for it in global_items:
        merged.append({**it, "scope": "macro"})
    for it in symbol_items:
        merged.append({**it, "scope": "symbol"})

    merged = _dedup_items(merged)
    merged = _annotate_and_rank_items(cfg, merged, date=date, max_items=max_items)
    return {"global": global_items, "symbol": symbol_items, "merged": merged}


def fetch_news(cfg: Dict[str, Any], symbol: Dict[str, Any], date: str) -> List[Dict[str, Any]]:
    provider = (cfg.get("news", {}) or {}).get("provider", "mock")
    max_n = int(((cfg.get("data", {}) or {}).get("max_news_per_day", 12)) or 12)

    # Prefer returning empty over mock when caller asked for real sources.
    if provider not in {"mock", "rss", "gnews", "multi", "web"}:
        provider = "gnews"

    if provider == "multi":
        bundle = fetch_news_bundle(cfg, symbol, date=date, max_items=max_n)
        return bundle["merged"]

    if provider == "web":
        return fetch_symbol_news(cfg, symbol, date=date, max_items=max_n)

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
                    items = _dedup_items(items)
                    return _annotate_and_rank_items(cfg, items, date=date, max_items=max_n)

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
                    items = _dedup_items(items)
                    return _annotate_and_rank_items(cfg, items, date=date, max_items=max_n)

        # Optionally mix global macro news even in pure gnews mode.
        global_cap = int((((cfg.get("news", {}) or {}).get("global", {}) or {}).get("max_items", 0)) or 0)
        if global_cap > 0:
            try:
                items = _dedup_items(_fetch_global_gnews(cfg, max_items=min(global_cap, max_n)) + items)
            except Exception:
                pass
        items = _dedup_items(items)
        return _annotate_and_rank_items(cfg, items, date=date, max_items=max_n)

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
            items = _dedup_items(items)
            return _annotate_and_rank_items(cfg, items, date=date, max_items=max_n)

        # Explicitly do not fallback to mock when RSS isn't configured.
        return []

    return []
