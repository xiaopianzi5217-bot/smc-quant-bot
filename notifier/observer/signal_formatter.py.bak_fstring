# -*- coding: utf-8 -*-
"""Spacious Telegram formatter for SMC observer / strategy messages.

排版原则：
- 大标题独占一行。
- 内容标题独占一行。
- 内容另起一行。
- 同一大标题内的小内容之间空 1 行。
- 大标题与大标题之间空 2 行。
- 不使用 <pre>，避免 Telegram 小字体和复制按钮。
"""
from __future__ import annotations

import html
import math
import re
from typing import Any, Dict, Iterable, List, Optional

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _raw(v: Any, default: str = "暂无") -> str:
    if v in [None, "", "N/A", "nan", "None"]:
        return default
    try:
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return default
    except Exception:
        pass
    return str(v)


def _clean(v: Any, default: str = "暂无") -> str:
    return ANSI_RE.sub("", _raw(v, default)).replace("\t", " ").replace("\r", " ").strip()


def _esc(v: Any, default: str = "暂无") -> str:
    return html.escape(_clean(v, default), quote=False)


def _sf(v: Any, default: Optional[float] = None) -> Optional[float]:
    if v in [None, "", "N/A", "暂无", "nan", "None"]:
        return default
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def _price(v: Any) -> str:
    x = _sf(v)
    if x is None:
        return "暂无"
    if abs(x) >= 100:
        return f"{x:.2f}"
    if abs(x) >= 1:
        return f"{x:.4f}"
    return f"{x:.6f}"


def _yes(v: Any) -> str:
    return "是" if bool(v) else "否"


def _pct_score(score: Any, threshold: Any) -> float:
    try:
        s = float(score or 0)
        t = max(float(threshold or 1), 1.0)
        return max(0.0, min(100.0, s / t * 100.0))
    except Exception:
        return 0.0


def _score_grade(pct: float) -> str:
    if pct >= 100:
        return "🟢 达标"
    if pct >= 75:
        return "🟡 接近"
    return "🔴 不足"


def _direction_text(v: Any) -> str:
    s = _clean(v)
    if s == "Long":
        return "看涨 / 偏多"
    if s == "Short":
        return "看跌 / 偏空"
    return "中性 / 等待"


def _message_type_name(message_type: str) -> str:
    m = (message_type or "OBSERVER").upper()
    if m == "STRATEGY":
        return "Strategy 可执行机会"
    if m == "EXECUTION":
        return "Execution 持仓管理"
    return "Observer 结构观察"


def _trend_explain(v: Any) -> str:
    """大方向：直接输出中文标识，不加解释"""
    s = _clean(v)
    # 1H macro_trend / allowed_direction 字段格式
    if "TOP" in s or "顶部" in s or "看空" in s or "派发" in s:
        return "看空"
    if "BOT" in s or "底部" in s or "看多" in s or "吸筹" in s:
        return "看多"
    if "BULL" in s or "bullish" in s.lower() or "Long" in s or "看涨" in s:
        return "看多"
    if "BEAR" in s or "bearish" in s.lower() or "Short" in s or "看跌" in s:
        return "看空"
    if "震" in s or "neutral" in s.lower() or "Both" in s:
        return "震荡"
    if "premium" in s.lower():
        return "看空（溢价区）"
    if "discount" in s.lower():
        return "看多（折价区）"
    return s if s else "暂无"


def _regime_explain(v: Any) -> str:
    """行情状态：直接输出中文标识"""
    s = _clean(v).lower()
    table = {
        "mud": "混沌震荡",
        "transition": "过渡状态",
        "trend": "趋势行情",
        "range": "区间震荡",
        "chop": "杂乱震荡",
    }
    return table.get(s, _clean(v) if s else "暂无")


def _volatility_explain(v: Any) -> str:
    """波动状态：直接输出中文标识"""
    s = _clean(v).lower()
    table = {
        "high": "高波动",
        "normal": "正常波动",
        "medium": "正常波动",
        "low": "低波动",
    }
    return table.get(s, _clean(v) if s else "暂无")


def _squeeze_explain(v: Any) -> str:
    s = _clean(v).lower()
    if s in ["building", "build"]:
        return "正在压缩"
    if s in ["released", "release"]:
        return "已释放"
    if s in ["none", "off", "false", "暂无"]:
        return "无明显压缩"
    return _clean(v) if s else "暂无"


def _parse_volume_ratio(volume_state: Any) -> Optional[float]:
    s = _clean(volume_state)
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*x", s, re.I)
    if not m:
        return None
    return _sf(m.group(1))


def _volume_explain(volume_state: Any) -> str:
    s = _clean(volume_state)
    r = _parse_volume_ratio(s)
    if r is None:
        return s
    percent = r * 100
    if r < 0.35:
        zone = "极度缩量"
        meaning = "参与资金很少，单根K线可信度低，容易被小资金打出假突破。"
    elif r < 0.65:
        zone = "缩量"
        meaning = "成交低于常态，说明还没有明显主力跟进。"
    elif r < 1.20:
        zone = "正常"
        meaning = "成交接近最近20根均量，可以按普通信号看。"
    elif r < 1.80:
        zone = "温和放量"
        meaning = "开始有资金跟进，信号可信度提高。"
    else:
        zone = "明显放量"
        meaning = "资金参与强，但也要防止冲高/杀跌后的反抽。"
    return (
        f"{s}\n\n"
        f"解释：约等于最近20根均量的 {percent:.0f}%。\n\n"
        f"区间：\n"
        f"0.35x以下 = 极度缩量\n"
        f"0.35x - 0.65x = 缩量\n"
        f"0.65x - 1.20x = 正常\n"
        f"1.20x - 1.80x = 温和放量\n"
        f"1.80x以上 = 明显放量\n\n"
        f"当前判断：{zone}。{meaning}"
    )


def _rsi_explain(v: Any) -> str:
    x = _sf(v)
    if x is None:
        return "暂无"
    if x < 30:
        zone = "超卖区"
        meaning = "空头已经打得较深，继续追空风险变高。"
    elif x < 45:
        zone = "偏弱区"
        meaning = "空方略占优，但还没到极端。"
    elif x <= 55:
        zone = "中性区"
        meaning = "多空力量接近平衡。"
    elif x <= 70:
        zone = "偏强区"
        meaning = "多方略占优，但还没到过热。"
    else:
        zone = "超买区"
        meaning = "多头已经较热，继续追多风险变高。"
    return (
        f"当前数值：{x:.2f}\n\n"
        f"区间：\n"
        f"30以下 = 超卖\n"
        f"30 - 45 = 偏弱\n"
        f"45 - 55 = 中性\n"
        f"55 - 70 = 偏强\n"
        f"70以上 = 超买\n\n"
        f"当前判断：{zone}。{meaning}"
    )


def _macd_explain(hist: Any, price: Any = None) -> str:
    h = _sf(hist)
    if h is None:
        return "暂无"
    direction = "多方动能占优" if h > 0 else "空方动能占优" if h < 0 else "多空动能接近平衡"
    axis = "0轴上方" if h > 0 else "0轴下方" if h < 0 else "0轴附近"
    extra = ""
    p = _sf(price)
    if p and p > 0:
        pct = abs(h) / p * 100
        if pct < 0.03:
            strength = "动能很弱"
        elif pct < 0.10:
            strength = "动能中等"
        else:
            strength = "动能较强"
        extra = f"\n\n结合现价：MACD柱体约占现价 {pct:.3f}%，当前属于{strength}。"
    return (
        f"当前柱体：{h:.4f}\n\n"
        f"区间：\n"
        f"0轴上方 = 偏多\n"
        f"0轴下方 = 偏空\n"
        f"0轴附近 = 多空动能接近平衡\n\n"
        f"当前判断：{axis}，{direction}。{extra}"
    )


def _adx_explain(v: Any) -> str:
    x = _sf(v)
    if x is None:
        return "暂无"
    if x < 20:
        zone = "弱趋势 / 震荡"
        meaning = "方向不稳定，容易来回打脸。"
    elif x < 25:
        zone = "趋势萌芽"
        meaning = "刚有方向，但还不够强。"
    elif x < 35:
        zone = "有效趋势"
        meaning = "趋势强度已经够用。"
    else:
        zone = "强趋势"
        meaning = "方向延续性较强，但追单也更容易追到尾端。"
    return (
        f"当前数值：{x:.2f}\n\n"
        f"区间：\n"
        f"20以下 = 弱趋势 / 震荡\n"
        f"20 - 25 = 趋势启动\n"
        f"25 - 35 = 有效趋势\n"
        f"35以上 = 强趋势\n\n"
        f"当前判断：{zone}。{meaning}"
    )


def _atr_explain(atr: Any, price: Any = None) -> str:
    a = _sf(atr)
    if a is None:
        return "暂无"
    p = _sf(price)
    if not p or p <= 0:
        return f"当前数值：{a:.4f}\n\n解释：ATR没有固定绝对区间，通常要除以现价看百分比。"
    pct = a / p * 100
    if pct < 0.25:
        zone = "低波动"
        meaning = "短线空间偏小，容易磨人。"
    elif pct < 0.70:
        zone = "正常波动"
        meaning = "适合按计划观察关键位。"
    elif pct < 1.20:
        zone = "高波动"
        meaning = "止损距离要放宽，仓位要降。"
    else:
        zone = "极高波动"
        meaning = "容易上下剧烈插针，不适合重仓。"
    return (
        f"当前数值：{a:.4f}\n\n"
        f"占现价比例：{pct:.2f}%\n\n"
        f"区间：\n"
        f"0.25%以下 = 低波动\n"
        f"0.25% - 0.70% = 正常波动\n"
        f"0.70% - 1.20% = 高波动\n"
        f"1.20%以上 = 极高波动\n\n"
        f"当前判断：{zone}。{meaning}"
    )


def _distance_pct(price: Any, level: Any) -> Optional[float]:
    p = _sf(price)
    l = _sf(level)
    if p is None or l is None or p <= 0:
        return None
    return abs(p - l) / p * 100


def _distance_explain(price: Any, level: Any) -> str:
    dist = _distance_pct(price, level)
    if dist is None:
        return "暂无"
    if dist < 0.25:
        zone = "贴脸，很近，随时可能被扫到。"
    elif dist < 0.60:
        zone = "很近，短线一波波动就可能到。"
    elif dist < 1.20:
        zone = "中等距离，需要一段行情推动。"
    else:
        zone = "偏远，短线暂时没那么容易到。"
    return f"距离：{dist:.2f}%\n\n解释：{zone}"


def _heat_kind(kind: str) -> str:
    table = {
        "PH": "前高附近",
        "PL": "前低附近",
        "FVG": "价格失衡区",
        "OB": "主力建仓区",
        "BSL": "上方止损密集区",
        "SSL": "下方止损密集区",
        "1H-BSL": "1小时上方止损密集区",
        "1H-SSL": "1小时下方止损密集区",
    }
    return table.get(kind.upper(), kind)


def _plain_heatmap_levels(levels: Any, max_lines: int = 4) -> str:
    s = _clean(levels)
    if s == "暂无":
        return "暂无有效热力位置，先观察。"
    lines = []
    parts = [p.strip() for p in s.split("|") if p.strip()]
    pattern = re.compile(r"(上方|下方)\s+([0-9.]+)\s+\(([+-]?[0-9.]+)%\)\s+([A-Za-z0-9-]+)\s+强([0-9.]+)")
    for p in parts[:max_lines]:
        m = pattern.search(p)
        if not m:
            lines.append(p.replace("FVG", "价格失衡区").replace("OB", "主力建仓区"))
            continue
        side, level, dist, kind, strength = m.groups()
        lines.append(f"{side} {level}，距离 {dist}%，位置类型：{_heat_kind(kind)}，吸引力：{strength}/3。")
    lines.append("\n强度说明：1.0普通，1.4以上明显，2.0以上较强。它不是胜率，只表示价格到这里时更容易产生反应。")
    return "\n".join(lines)


def _plain_heatmap_analysis(d: Dict[str, Any]) -> str:
    price = d.get("price")
    up = _distance_pct(price, d.get("bsl_level"))
    down = _distance_pct(price, d.get("ssl_level"))
    if up is not None and down is not None:
        if up < down:
            first = "上方止损区更近，价格更容易先向上试探，扫掉一批追空止损。"
        elif down < up:
            first = "下方止损区更近，价格更容易先向下试探，扫掉一批追多止损。"
        else:
            first = "上下两边距离差不多，容易先震荡洗盘。"
        return first + "\n\n操作含义：不要只看方向分就追，最好等它先打到一边关键位，再看K线颜色、成交量和MACD是否跟上。"
    return _clean(d.get("heatmap_analysis"), "上下关键位置还不够清晰，先观察。")


def _funding_text(v: Any, reason: Any = None) -> str:
    x = _sf(v)
    if x is None:
        return "暂无\n\n解释：交易所暂未返回资金费率。"
    if x > 0:
        base = f"当前数值：{x:.4f}%\n\n解释：多头付费，说明多头更拥挤；过高时不建议追多。"
    elif x < 0:
        base = f"当前数值：{x:.4f}%\n\n解释：空头付费，说明空头更拥挤；过低时不建议追空。"
    else:
        base = "当前数值：0.0000%\n\n解释：多空成本基本中性。"
    r = _clean(reason)
    if r != "暂无":
        base += f"\n\n过滤结果：{r}"
    return base


def _reason_detail(reason: Any) -> str:
    s = _clean(reason)
    if s == "暂无":
        return "暂无"
    s = s.replace("趨勢", "趋势").replace("掃蕩", "扫荡")
    if s.startswith("regime="):
        return s.replace("regime=", "行情=").replace("squeeze=", "压缩=").replace("vol=", "波动=")
    if "趋势" in s:
        return s + "\n解释：大周期方向支持当前一边，所以方向分提高。"
    if "扫荡" in s:
        return s + "\n解释：价格先打掉一边止损，可能是诱多/诱空后的反向机会。"
    if "背离" in s:
        return s + "\n解释：价格创新高/低但动能没跟上，反转概率上升。"
    if "FVG" in s:
        return s.replace("FVG", "价格失衡区") + "\n解释：这里以前走得太快，后面价格回到这里容易有反应。"
    if "OB" in s:
        return s.replace("OB", "主力建仓区") + "\n解释：这里可能成为支撑或压力。"
    return s


def _reason_text(reasons: Optional[Iterable[Any]], decision: Optional[Dict[str, Any]]) -> str:
    src = list(reasons or [])
    blocks = []
    for i, item in enumerate(src[:6], 1):
        blocks.append(f"{i}）{_reason_detail(item)}")
    if decision:
        reason = decision.get("reason") or decision.get("reason_cn") or decision.get("explain")
        if reason:
            blocks.append("中枢结论：\n" + _clean(reason))
    if not blocks:
        return "暂无强触发。\n\n解释：目前更适合观察，等待扫止损、放量、背离或主力建仓区反应。"
    return "\n\n".join(blocks)


def _kv(title: str, content: Any) -> str:
    # 内容标题独占一行；内容另起一行；每个小块之间由调用方 join('\n\n') 留空。
    return f"<b>{html.escape(title, quote=False)}</b>\n{html.escape(_clean(content), quote=False)}"


def _section(title: str, blocks: Iterable[str]) -> str:
    # 大标题与本节内容之间空一行；本节小块之间空一行。
    return f"<b>{html.escape(title, quote=False)}</b>\n\n" + "\n\n".join([b for b in blocks if b])


def _safe_trim(msg: str, limit: int = 3600) -> str:
    if len(msg) <= limit:
        return msg
    return msg[: limit - 30] + "\n\n内容较长，已自动截断。"


def format_signal_message(snapshot, message_type="OBSERVER", layer_reasons=None, decision=None):
    if snapshot is None:
        return "<b>AI 交易观察</b>\n\n快照为空，已跳过。"

    d: Dict[str, Any] = snapshot.to_dict() if hasattr(snapshot, "to_dict") else dict(snapshot)
    decision = decision or {}

    symbol = _clean(d.get("symbol", "UNKNOWN"))
    price = d.get("price")
    long_pct = _pct_score(d.get("long_score"), d.get("threshold_long"))
    short_pct = _pct_score(d.get("short_score"), d.get("threshold_short"))

    header = (
        f"<b>📡 {_esc(_message_type_name(message_type))}</b>\n\n"
        f"<b>{_esc(symbol)}</b>\n"
        f"现价：<b>{_esc(_price(price))}</b>"
    )

    summary = _section("① AI 全局多空博弈天平", [
        _kv("当前方向", _direction_text(d.get("direction_bias"))),
        _kv("多军战斗力", f"{_score_grade(long_pct)} {long_pct:.0f}分\n原始分：{float(d.get('long_score') or 0):.1f}/{float(d.get('threshold_long') or 0):.1f}"),
        _kv("空军战斗力", f"{_score_grade(short_pct)} {short_pct:.0f}分\n原始分：{float(d.get('short_score') or 0):.1f}/{float(d.get('threshold_short') or 0):.1f}"),
        _kv("操作建议", d.get("suggestion")),
    ])

    market = _section("② 行情环境", [
        _kv("大方向", _trend_explain(d.get("trend_result"))),
        _kv("行情状态", _regime_explain(d.get("regime"))),
        _kv("波动状态", _volatility_explain(d.get("volatility"))),
        _kv("压缩状态", _squeeze_explain(d.get("squeeze"))),
        _kv("成交量", _volume_explain(d.get("volume_state"))),
    ])

    divergence = "暂无明显背离"
    if d.get("bearish_divergence") and d.get("bullish_divergence"):
        divergence = "同时出现顶背离与底背离，说明行情噪音较大。"
    elif d.get("bearish_divergence"):
        divergence = "顶背离\n\n解释：价格偏强但动能没有同步增强，追多要谨慎。"
    elif d.get("bullish_divergence"):
        divergence = "底背离\n\n解释：价格偏弱但空头动能没有同步增强，追空要谨慎。"

    indicators = _section("③ 主力与指标数据透视", [
        _kv("背离", divergence),
        _kv("K线颜色", f"{_clean(d.get('candle_color'))}\n变色：{_yes(d.get('color_changed'))}"),
        _kv("RSI", _rsi_explain(d.get("rsi"))),
        _kv("MACD", _macd_explain(d.get("macd_hist"), price)),
        _kv("ADX", _adx_explain(d.get("adx"))),
        _kv("ATR", _atr_explain(d.get("atr"), price)),
    ])

    liquidity = _section("④ 流动性 / 热力图 / 资金", [
        _kv("上方止损密集区", f"价格：{_price(d.get('bsl_level'))}\n{_distance_explain(price, d.get('bsl_level'))}\n已扫：{_yes(d.get('is_bsl_swept'))}"),
        _kv("下方止损密集区", f"价格：{_price(d.get('ssl_level'))}\n{_distance_explain(price, d.get('ssl_level'))}\n已扫：{_yes(d.get('is_ssl_swept'))}"),
        _kv("主力建仓区", f"买方：{_clean(d.get('bullish_ob_range'))}\n卖方：{_clean(d.get('bearish_ob_range'))}"),
        _kv("价格失衡区", f"{_clean(d.get('fvg_level'))}\n\n解释：这里以前走得太快，后面容易回踩、回补或产生反应。"),
        _kv("热力图位置", _plain_heatmap_levels(d.get("heatmap_levels"))),
        _kv("热力图解读", _plain_heatmap_analysis(d)),
        _kv("资金费率", _funding_text(d.get("funding_rate"), d.get("funding_filter_reason"))),
    ])

    plan = _section("⑤ 风控计划", [
        _kv("入场参考", _price(d.get("entry"))),
        _kv("止损位置", _price(d.get("sl"))),
        _kv("第一目标", f"{_price(d.get('tp1'))}\n约 {_clean(d.get('rr1'))}R"),
        _kv("第二目标", f"{_price(d.get('tp2'))}\n约 {_clean(d.get('rr2'))}R"),
        _kv("第三目标", f"{_price(d.get('tp3'))}\n约 {_clean(d.get('rr3'))}R"),
    ])

    reasons = _section("⑥ 触发理由", [
        _kv("理由明细", _reason_text(layer_reasons or d.get("reasons"), decision)),
    ])

    # 大标题与大标题之间留 2 个空行。
    return _safe_trim("\n\n\n".join([header, summary, market, indicators, liquidity, plan, reasons]))
