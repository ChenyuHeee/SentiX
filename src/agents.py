from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import requests

from .utils import clamp, sentiment_band


def _avg_conf(items: List[Dict[str, Any]]) -> float:
    ws: List[float] = []
    cs: List[float] = []
    for it in items:
        if it.get("confidence") is None:
            continue
        try:
            w = float(it.get("weight", 1.0) or 1.0)
        except Exception:
            w = 1.0
        w = float(max(0.0, min(1.0, w)))
        if w <= 0:
            continue
        ws.append(w)
        cs.append(float(it.get("confidence", 0.0) or 0.0))
    if not ws:
        return 0.55
    tot = float(sum(ws))
    if tot <= 0:
        return 0.55
    return float(sum(c * w for c, w in zip(cs, ws)) / tot)


def sentiment_from_analyzed(items: List[Dict[str, Any]]) -> Tuple[float, Dict[str, int], float]:
    if not items:
        return 0.0, {"bull": 0, "bear": 0, "neutral": 0}, 0.55
    bull = 0.0
    bear = 0.0
    total_w = 0.0
    for it in items:
        try:
            w = float(it.get("weight", 1.0) or 1.0)
        except Exception:
            w = 1.0
        w = float(max(0.0, min(1.0, w)))
        if w <= 0:
            continue
        total_w += w
        conf = float(it.get("confidence", 0.0) or 0.0)
        if it.get("sentiment") == "bull":
            bull += w * conf
        elif it.get("sentiment") == "bear":
            bear += w * conf

    if total_w <= 0:
        return 0.0, {"bull": 0, "bear": 0, "neutral": 0}, 0.55

    idx = (bull - bear) / float(total_w)
    idx = float(max(-1.0, min(1.0, idx)))
    counts = {
        "bull": sum(1 for it in items if it.get("sentiment") == "bull"),
        "bear": sum(1 for it in items if it.get("sentiment") == "bear"),
        "neutral": sum(1 for it in items if it.get("sentiment") == "neutral"),
    }
    return idx, counts, clamp(_avg_conf(items), 0.5, 0.95)


def _ma(xs: List[float], n: int) -> float:
    if not xs:
        return 0.0
    n = max(1, min(n, len(xs)))
    return float(sum(xs[-n:]) / n)


def _atr14(kline: List[Dict[str, Any]]) -> float:
    if len(kline) < 2:
        return 0.0
    trs: List[float] = []
    for i in range(1, len(kline)):
        h = float(kline[i].get("high") or 0.0)
        l = float(kline[i].get("low") or 0.0)
        pc = float(kline[i - 1].get("close") or 0.0)
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if not trs:
        return 0.0
    n = min(14, len(trs))
    return float(sum(trs[-n:]) / n)


def _deepseek_enabled(cfg: Dict[str, Any]) -> bool:
    provider = str(((cfg.get("analysis", {}) or {}).get("provider") or "lexicon")).lower()
    if provider not in {"deepseek"}:
        return False
    dcfg = ((cfg.get("analysis", {}) or {}).get("deepseek", {}) or {})
    key_env = str(dcfg.get("api_key_env", "DEEPSEEK_API_KEY") or "DEEPSEEK_API_KEY")
    return bool(os.environ.get(key_env, "").strip())


def _deepseek_chat_json(cfg: Dict[str, Any], *, system: str, user: str, timeout: int = 30) -> Dict[str, Any] | None:
    dcfg = ((cfg.get("analysis", {}) or {}).get("deepseek", {}) or {})
    base_url = str(dcfg.get("base_url", "https://api.deepseek.com") or "https://api.deepseek.com").rstrip("/")
    model = str(dcfg.get("model", "deepseek-chat") or "deepseek-chat")
    key_env = str(dcfg.get("api_key_env", "DEEPSEEK_API_KEY") or "DEEPSEEK_API_KEY")
    api_key = os.environ.get(key_env, "").strip()
    if not api_key:
        return None

    url = f"{base_url}/v1/chat/completions"
    payload = {
        "model": model,
        "temperature": float(dcfg.get("temperature", 0.2) or 0.2),
        "max_tokens": int(dcfg.get("max_tokens", 1200) or 1200),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        content = (((data.get("choices") or [])[0] or {}).get("message") or {}).get("content")
        if not content:
            return None
        text = str(content).strip()
        # Handle fenced code blocks like ```json ... ```
        if text.startswith("```"):
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1].strip()
                if text.lower().startswith("json"):
                    text = text[4:].strip()

        # Try direct JSON first; then best-effort extract outermost object.
        try:
            return json.loads(text)
        except Exception:
            i = text.find("{")
            j = text.rfind("}")
            if i >= 0 and j > i:
                try:
                    return json.loads(text[i : j + 1])
                except Exception:
                    return None
            return None
    except Exception as e:
        logging.info("DeepSeek call failed: %s", e)
        return None


@dataclass(frozen=True)
class AgentScore:
    index: float
    band: str
    confidence: float
    mode: str  # llm|heuristic
    rationale: List[str]


def macro_agent(cfg: Dict[str, Any], *, date: str, analyzed_global_news: List[Dict[str, Any]]) -> AgentScore:
    if _deepseek_enabled(cfg) and analyzed_global_news:
        titles = [str(it.get("title") or "")[:200] for it in analyzed_global_news][:30]
        system = "你是宏观交易情绪分析Agent。输出JSON，字段: index(-1到1), confidence(0.5到0.95), rationale(数组,<=5条)。不要输出多余字段。"
        user = f"日期 {date}。宏观新闻标题如下:\n" + "\n".join([f"- {t}" for t in titles])
        j = _deepseek_chat_json(cfg, system=system, user=user)
        if isinstance(j, dict) and "index" in j:
            idx = float(j.get("index") or 0.0)
            conf = float(j.get("confidence") or 0.55)
            rat = j.get("rationale") or []
            if not isinstance(rat, list):
                rat = []
            idx = float(max(-1.0, min(1.0, idx)))
            conf = float(clamp(conf, 0.5, 0.95))
            return AgentScore(index=idx, band=sentiment_band(idx), confidence=conf, mode="llm", rationale=[str(x) for x in rat][:5])

    idx, _counts, conf = sentiment_from_analyzed(analyzed_global_news)
    rat = []
    if analyzed_global_news:
        rat = [str(it.get("title") or "") for it in analyzed_global_news[:3] if it.get("title")]
    return AgentScore(index=idx, band=sentiment_band(idx), confidence=conf, mode="heuristic", rationale=rat)


def symbol_news_agent(cfg: Dict[str, Any], *, symbol: Dict[str, Any], date: str, analyzed_symbol_news: List[Dict[str, Any]]) -> AgentScore:
    if _deepseek_enabled(cfg) and analyzed_symbol_news:
        titles = [str(it.get("title") or "")[:200] for it in analyzed_symbol_news][:30]
        system = "你是品种新闻交易情绪分析Agent。输出JSON，字段: index(-1到1), confidence(0.5到0.95), rationale(数组,<=5条)。不要输出多余字段。"
        user = f"日期 {date}，品种 {symbol.get('name')}。相关新闻标题如下:\n" + "\n".join([f"- {t}" for t in titles])
        j = _deepseek_chat_json(cfg, system=system, user=user)
        if isinstance(j, dict) and "index" in j:
            idx = float(j.get("index") or 0.0)
            conf = float(j.get("confidence") or 0.55)
            rat = j.get("rationale") or []
            if not isinstance(rat, list):
                rat = []
            idx = float(max(-1.0, min(1.0, idx)))
            conf = float(clamp(conf, 0.5, 0.95))
            return AgentScore(index=idx, band=sentiment_band(idx), confidence=conf, mode="llm", rationale=[str(x) for x in rat][:5])

    idx, _counts, conf = sentiment_from_analyzed(analyzed_symbol_news)
    rat = []
    if analyzed_symbol_news:
        rat = [str(it.get("title") or "") for it in analyzed_symbol_news[:3] if it.get("title")]
    return AgentScore(index=idx, band=sentiment_band(idx), confidence=conf, mode="heuristic", rationale=rat)


def market_agent(cfg: Dict[str, Any], *, kline: List[Dict[str, Any]], date: str) -> AgentScore | None:
    # Skip if the requested date is not in kline dates (market closed).
    dates = {x.get("date") for x in kline if x.get("date")}
    if date not in dates:
        return None
    closes = [float(x.get("close") or 0.0) for x in kline if x.get("close") is not None]
    vols = [float(x.get("volume") or 0.0) for x in kline if x.get("volume") is not None]
    if len(closes) < 10:
        return AgentScore(index=0.0, band="neutral", confidence=0.55, mode="heuristic", rationale=["K线数据不足"])

    c = closes[-1]
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)
    trend = 0.0
    if ma20 and ma60:
        trend += 0.5 if ma20 > ma60 else -0.5
    if ma20:
        trend += 0.5 if c > ma20 else -0.5

    v = vols[-1] if vols else 0.0
    vma20 = _ma(vols, 20) if vols else 0.0
    vol_boost = 0.0
    if vma20 > 0:
        vol_boost = clamp((v / vma20) - 1.0, -0.5, 0.5)

    idx = clamp(trend + 0.5 * vol_boost, -1.0, 1.0)
    atr = _atr14(kline)
    conf = 0.6
    if c > 0 and atr > 0:
        # Lower confidence when volatility is extremely high.
        conf -= clamp((atr / c) * 10.0, 0.0, 0.2)
    conf = clamp(conf, 0.5, 0.9)

    rat = [
        f"收盘 {c:.2f}，MA20 {ma20:.2f}，MA60 {ma60:.2f}",
        f"成交量/20日均量 {v / vma20:.2f}" if vma20 else "成交量均值不足",
    ]
    return AgentScore(index=float(idx), band=sentiment_band(idx), confidence=float(conf), mode="heuristic", rationale=rat)


def market_agent_llm(
    cfg: Dict[str, Any],
    *,
    symbol: Dict[str, Any],
    kline: List[Dict[str, Any]],
    date: str,
    fundamentals: Dict[str, Any] | None,
) -> AgentScore | None:
    """LLM-enhanced market agent (technical + fundamentals).

    Hallucination mitigation:
    - Provide only compact, precomputed numeric signals (no raw tables).
    - Strict JSON-only output contract.
    - Validate/clamp response; fallback to heuristic on any anomaly.
    - Sanitize numbers in rationale to avoid displaying unverified figures.
    """

    if not _deepseek_enabled(cfg):
        return None

    # Skip if the requested date is not in kline dates (market closed).
    dates = {x.get("date") for x in kline if x.get("date")}
    if date not in dates:
        return None

    closes = [float(x.get("close") or 0.0) for x in kline if x.get("close") is not None]
    vols = [float(x.get("volume") or 0.0) for x in kline if x.get("volume") is not None]
    if len(closes) < 10:
        return AgentScore(index=0.0, band="neutral", confidence=0.55, mode="heuristic", rationale=["K线数据不足"])

    c = float(closes[-1])
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)
    atr = _atr14(kline)
    v = float(vols[-1]) if vols else 0.0
    vma20 = _ma(vols, 20) if vols else 0.0
    vol_ratio = (v / vma20) if vma20 else None

    sym_name = str(symbol.get("name") or "")
    f = fundamentals if isinstance(fundamentals, dict) else {"status": "missing", "asof": "", "signals": {}}

    system = (
        "你是期货市场数据Agent。你必须严格输出JSON对象，字段仅允许: "
        "index(-1到1), confidence(0.5到0.95), rationale(数组,<=5条)。"
        "不要输出其它字段、不要输出Markdown。\n"
        "重要约束(反幻觉): 只能使用输入中给出的事实与数值，不要编造/猜测任何数值；"
        "如果数据缺失，就在rationale里明确写‘未知/缺失’，并降低confidence。"
    )

    user = (
        f"日期 {date}，品种 {sym_name}。以下是可用的权威信号(仅可引用这些数值)：\n"
        f"技术面: close={c:.4f}, ma20={ma20:.4f}, ma60={ma60:.4f}, atr14={atr:.6f}, "
        f"vol_ratio20={(f'{vol_ratio:.4f}' if isinstance(vol_ratio, float) else 'unknown')}\n"
        f"基本面(asof={str(f.get('asof') or '')}): {json.dumps(f.get('signals') or {}, ensure_ascii=False)}\n"
        "任务: 综合技术面与基本面，输出情绪指数index与confidence，并给出<=5条rationale。"
        "不要引入输入中没有的指标名或数值。"
    )

    j = _deepseek_chat_json(cfg, system=system, user=user)
    if not isinstance(j, dict) or "index" not in j:
        return None

    try:
        idx = float(j.get("index") or 0.0)
        conf = float(j.get("confidence") or 0.55)
    except Exception:
        return None

    rat = j.get("rationale") or []
    if not isinstance(rat, list):
        rat = []
    rat2 = [str(x) for x in rat][:5]

    idx = float(max(-1.0, min(1.0, idx)))
    conf = float(clamp(conf, 0.5, 0.95))

    # Remove numbers to reduce the impact of hallucinated figures.
    sanitized: List[str] = []
    for line in rat2:
        s = str(line)
        s = re.sub(r"(?<!\\d)(-?\\d+(?:\\.\\d+)?)(?!\\d)", "", s)
        s = re.sub(r"\\s{2,}", " ", s).strip()
        if s:
            sanitized.append(s)
    rat2 = sanitized[:5]

    # Cap confidence when fundamentals are missing/unavailable.
    if str(f.get("status") or "").lower() != "ok":
        conf = float(min(conf, 0.75))

    # If fundamentals asof differs from requested date, cap confidence.
    fasof = str(f.get("asof") or "").strip()
    if fasof and fasof != str(date):
        conf = float(min(conf, 0.72))

    return AgentScore(index=idx, band=sentiment_band(idx), confidence=conf, mode="llm", rationale=rat2)


def combine_final(
    *,
    macro: AgentScore,
    symbol_news: AgentScore,
    market: AgentScore | None,
    weights: Dict[str, float],
) -> AgentScore:
    wm = float(weights.get("macro", 0.3))
    ws = float(weights.get("symbol", 0.3))
    wk = float(weights.get("market", 0.4))
    if market is None:
        # Market closed: only show macro + symbol. Keep confidence but do not
        # pretend we had market signal.
        s = wm + ws
        wm2 = 0.5 if s <= 0 else wm / s
        ws2 = 0.5 if s <= 0 else ws / s
        idx = clamp(wm2 * macro.index + ws2 * symbol_news.index, -1.0, 1.0)
        conf = clamp(0.5 + 0.5 * (macro.confidence * wm2 + symbol_news.confidence * ws2), 0.5, 0.9)
        rat = ["休市：最终分数未计算（仅展示宏观/品种分）"]
        return AgentScore(index=float(idx), band=sentiment_band(idx), confidence=float(conf), mode="heuristic", rationale=rat)

    idx = clamp(wm * macro.index + ws * symbol_news.index + wk * market.index, -1.0, 1.0)
    conf = clamp((macro.confidence * wm + symbol_news.confidence * ws + market.confidence * wk), 0.5, 0.95)
    rat = ["宏观+品种新闻+市场数据加权"]
    return AgentScore(index=float(idx), band=sentiment_band(idx), confidence=float(conf), mode="heuristic", rationale=rat)


def trade_plan(
    *,
    symbol: Dict[str, Any],
    kline: List[Dict[str, Any]],
    final_score: AgentScore,
) -> Dict[str, Any]:
    if not kline:
        return {"status": "unavailable", "reason": "missing kline"}

    last = kline[-1]
    close = float(last.get("close") or 0.0)
    atr = _atr14(kline)
    if close <= 0 or atr <= 0:
        return {"status": "unavailable", "reason": "insufficient kline"}

    direction = "long" if final_score.index > 0.2 else "short" if final_score.index < -0.2 else "neutral"

    def mk(mult_entry: float, mult_stop: float, mult_t1: float, mult_t2: float) -> Dict[str, Any]:
        if direction == "long":
            entry = [close - atr * mult_entry, close + atr * mult_entry]
            stop = close - atr * mult_stop
            t1 = close + atr * mult_t1
            t2 = close + atr * mult_t2
        elif direction == "short":
            entry = [close - atr * mult_entry, close + atr * mult_entry]
            stop = close + atr * mult_stop
            t1 = close - atr * mult_t1
            t2 = close - atr * mult_t2
        else:
            entry = [close - atr * mult_entry, close + atr * mult_entry]
            stop = None
            t1 = None
            t2 = None

        pos = "轻仓" if final_score.confidence < 0.65 else "中等仓位" if final_score.confidence < 0.8 else "偏重仓位"
        return {
            "direction": direction,
            "entry_zone": [round(entry[0], 2), round(entry[1], 2)],
            "stop": None if stop is None else round(float(stop), 2),
            "target1": None if t1 is None else round(float(t1), 2),
            "target2": None if t2 is None else round(float(t2), 2),
            "position": pos,
            "triggers": [
                "若价格突破入场区间并放量，则执行",
                "若触发止损则严格离场",
            ],
        }

    return {
        "status": "ok",
        "asof": str(last.get("date") or ""),
        "symbol": {"id": symbol.get("id"), "name": symbol.get("name")},
        "short_term": mk(0.5, 1.5, 1.0, 2.0),
        "swing": mk(1.0, 2.5, 2.0, 4.0),
        "mid_term": mk(1.5, 3.5, 3.0, 6.0),
        "notes": [
            "该计划为基于历史波动(ATR)的结构化模板，非投资建议",
        ],
    }
