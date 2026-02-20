from __future__ import annotations

import hashlib
import random
from datetime import datetime
from typing import Any, Dict, List

import feedparser

from .cleaner import clean_text


def _seed(symbol_id: str, date: str) -> int:
    h = hashlib.sha256(f"{symbol_id}:{date}".encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def fetch_news(cfg: Dict[str, Any], symbol: Dict[str, Any], date: str) -> List[Dict[str, Any]]:
    provider = (cfg.get("news", {}) or {}).get("provider", "mock")
    max_n = int(((cfg.get("data", {}) or {}).get("max_news_per_day", 12)) or 12)

    if provider == "rss":
        urls = ((cfg.get("news", {}) or {}).get("rss", {}) or {}).get("urls", []) or []
        if not urls:
            provider = "mock"
        else:
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

    rng = random.Random(_seed(symbol["id"], date))
    positive = [
        "利好预期升温",
        "需求回暖",
        "政策支持加码",
        "风险偏好回升",
        "供应收缩推升价格",
    ]
    negative = [
        "承压回落",
        "需求走弱",
        "库存上升",
        "监管收紧",
        "宏观不确定性增加",
    ]
    neutral = ["市场观望", "波动加剧", "多空分歧", "消息面平淡"]
    sources = ["财联社", "新浪财经", "腾讯财经", "期货日报", "七禾网"]

    n = rng.randint(max(3, max_n // 2), max_n)
    out: List[Dict[str, Any]] = []
    for i in range(n):
        tag = rng.choices(["pos", "neg", "neu"], weights=[4, 4, 2], k=1)[0]
        if tag == "pos":
            tail = rng.choice(positive)
        elif tag == "neg":
            tail = rng.choice(negative)
        else:
            tail = rng.choice(neutral)
        title = f"{symbol['name']}：{tail}"
        out.append(
            {
                "title": title,
                "url": "",
                "source": rng.choice(sources),
                "published_at": f"{date} {rng.randint(8, 15):02d}:{rng.randint(0, 59):02d}",
                "content": "",
            }
        )
    return out
