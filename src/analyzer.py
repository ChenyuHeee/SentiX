from __future__ import annotations

from typing import Any, Dict, List

from .cleaner import clean_text
from .utils import clamp


POS_WORDS = ["利好", "回暖", "支持", "加码", "走强", "上行", "突破", "改善", "增产不及预期", "降息"]
NEG_WORDS = ["承压", "回落", "走弱", "下行", "下跌", "收紧", "风险", "不确定", "库存上升", "加息"]


def analyze_news_items(cfg: Dict[str, Any], items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    provider = (cfg.get("analysis", {}) or {}).get("provider", "lexicon")
    if provider != "lexicon":
        provider = "lexicon"

    out: List[Dict[str, Any]] = []
    for it in items:
        title = clean_text(it.get("title", ""))
        text = title + " " + clean_text(it.get("content", ""))
        pos = sum(1 for w in POS_WORDS if w in text)
        neg = sum(1 for w in NEG_WORDS if w in text)
        score = pos - neg
        if score > 0:
            label = "bull"
        elif score < 0:
            label = "bear"
        else:
            label = "neutral"

        confidence = 0.55
        confidence += 0.1 * min(3, abs(score))
        confidence = clamp(confidence, 0.5, 0.95)
        out.append(
            {
                **it,
                "sentiment": label,
                "confidence": float(confidence),
            }
        )
    return out
