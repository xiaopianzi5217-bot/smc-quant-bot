# -*- coding: utf-8 -*-
"""trading_dashboard.py — SMC 综合交易仪表盘 (自动刷新 / 多周期 / 盘口 / AI)

部署到: /app/utils/trading_dashboard.py
app.py: from utils.trading_dashboard import build_dashboard_tab
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except Exception:
    go = None
    make_subplots = None

try:
    import gradio as gr
except Exception:
    gr = None

try:
    from utils.ai_advisor import analyze_signal_result, ask_deepseek
except Exception:
    try:
        from ai_advisor import analyze_signal_result, ask_deepseek
    except Exception:
        analyze_signal_result = None
        ask_deepseek = None

BJ = timezone(timedelta(hours=8))
SYMBOLS = ["BTC/USDT", "ETH/USDT"]
MTF = ("15m", "1h", "4h")


def _norm_sym(symbol: str) -> str:
    s = (symbol or "BTC/USDT").strip().upper()
    if ":USDT" in s:
        s = s.split(":")[0]
    if "/" not in s and s.endswith("USDT"):
        s = s[:-4] + "/USDT"
    return s


def _bitget_sym(symbol: str) -> str:
    return _norm_sym(symbol).replace("/", "").split(":")[0]


def fetch_ohlcv(symbol: str, timeframe: str = "15m", limit: int = 120) -> pd.DataFrame:
    import requests
    tf_map = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}
    url = "https://api.bitget.com/api/v2/mix/market/candles"
    params = {
        "symbol": _bitget_sym(symbol),
        "productType": "umcbl",
        "granularity": tf_map.get(timeframe, "15m"),
        "limit": min(int(limit), 200),
    }
    r = requests.get(url, params=params, timeout=12)
    r.raise_for_status()
    data = r.json()
    if str(data.get("code")) != "00000":
        raise RuntimeError(data.get("msg") or "bitget error")
    bars = data.get("data") or []
    df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume", "quoteVol"])
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    df["timestamp"] = df["timestamp"].astype("int64")
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert(BJ)
    return df


def fetch_funding(symbol: str) -> Optional[float]:
    import requests
    try:
        r = requests.get(
            "https://api.bitget.com/api/v2/mix/market/current-fund-rate",
            params={"symbol": _bitget_sym(symbol)},
            timeout=8,
        )
        data = r.json()
        if str(data.get("code")) == "00000":
            fr = (data.get("data") or {}).get("fundingRate")
            return float(fr) * 100.0 if fr is not None else None
    except Exception:
        return None
    return None


def fetch_ticker(symbol: str) -> Dict[str, Any]:
    import requests
    out = {"last": None, "change24h": None, "high24h": None, "low24h": None, "vol24h": None}
    try:
        r = requests.get(
            "https://api.bitget.com/api/v2/mix/market/ticker",
            params={"symbol": _bitget_sym(symbol), "productType": "umcbl"},
            timeout=8,
        )
        data = r.json()
        if str(data.get("code")) == "00000":
            d = data.get("data") or {}
            if isinstance(d, list):
                d = d[0] if d else {}
            out["last"] = float(d.get("lastPr") or d.get("last") or 0) or None
            if d.get("change24h") is not None:
                out["change24h"] = float(d.get("change24h")) * 100
            out["high24h"] = float(d.get("high24h") or 0) or None
            out["low24h"] = float(d.get("low24h") or 0) or None
            out["vol24h"] = float(d.get("baseVolume") or d.get("quoteVolume") or 0) or None
    except Exception:
        pass
    return out


def fetch_orderbook(symbol: str, limit: int = 30) -> Dict[str, Any]:
    """Bitget 合约盘口深度。"""
    import requests
    try:
        r = requests.get(
            "https://api.bitget.com/api/v2/mix/market/merge-depth",
            params={"symbol": _bitget_sym(symbol), "productType": "umcbl", "limit": str(limit)},
            timeout=8,
        )
        data = r.json()
        if str(data.get("code")) != "00000":
            # fallback endpoint
            r = requests.get(
                "https://api.bitget.com/api/v2/mix/market/orderbook",
                params={"symbol": _bitget_sym(symbol), "productType": "umcbl", "limit": str(limit)},
                timeout=8,
            )
            data = r.json()
        d = data.get("data") or {}
        bids = d.get("bids") or d.get("b") or []
        asks = d.get("asks") or d.get("a") or []
        # [[price, size], ...]
        def _parse(side):
            out = []
            for x in side[:limit]:
                if isinstance(x, (list, tuple)) and len(x) >= 2:
                    out.append((float(x[0]), float(x[1])))
            return out
        return {"bids": _parse(bids), "asks": _parse(asks)}
    except Exception as e:
        return {"bids": [], "asks": [], "error": str(e)}


def fetch_liquidations_proxy(symbol: str, df: pd.DataFrame) -> pd.DataFrame:
    """无官方清算流时，用影线+放量构造「清算压力代理」热力（非交易所真实清算）。"""
    if df is None or df.empty:
        return pd.DataFrame()
    d = df.tail(48).copy()
    body = (d["close"] - d["open"]).abs()
    rng = (d["high"] - d["low"]).replace(0, pd.NA)
    wick_up = d["high"] - d[["open", "close"]].max(axis=1)
    wick_dn = d[["open", "close"]].min(axis=1) - d["low"]
    vol_z = (d["volume"] - d["volume"].rolling(20).mean()) / d["volume"].rolling(20).std().replace(0, pd.NA)
    d["long_liq_proxy"] = (wick_dn / rng * vol_z.clip(lower=0)).fillna(0)  # 下影+放量 ≈ 多头被扫
    d["short_liq_proxy"] = (wick_up / rng * vol_z.clip(lower=0)).fillna(0)
    return d


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).rolling(n).mean()
    down = (-d.clip(upper=0)).rolling(n).mean()
    rs = up / down.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l).abs(), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["rsi"] = _rsi(d["close"])
    d["ema20"] = _ema(d["close"], 20)
    d["ema50"] = _ema(d["close"], 50)
    d["ema200"] = _ema(d["close"], 200)
    d["atr"] = _atr(d)
    d["vol_ma"] = d["volume"].rolling(20).mean()
    d["vol_ratio"] = d["volume"] / d["vol_ma"].replace(0, pd.NA)
    mid = d["close"].rolling(20).mean()
    std = d["close"].rolling(20).std()
    bb_u, bb_l = mid + 2 * std, mid - 2 * std
    atr = d["atr"]
    kc_u, kc_l = mid + 1.5 * atr, mid - 1.5 * atr
    d["sqz_on"] = (bb_u < kc_u) & (bb_l > kc_l)
    d["sqz_mom"] = d["close"] - mid
    return d


def regime_from_df(df: pd.DataFrame) -> str:
    if df is None or len(df) < 50:
        return "UNKNOWN"
    e50, e200 = float(df["ema50"].iloc[-1] or 0), float(df["ema200"].iloc[-1] or 0)
    if e50 > e200 * 1.001:
        return "BULL"
    if e50 < e200 * 0.999:
        return "BEAR"
    return "RANGE"


def sqz_state(df: pd.DataFrame) -> str:
    if df is None or len(df) < 5:
        return "N/A"
    on = bool(df["sqz_on"].iloc[-1])
    prev = bool(df["sqz_on"].iloc[-2])
    mom = float(df["sqz_mom"].iloc[-1] or 0)
    if prev and not on:
        return f"RELEASE {'↑' if mom >= 0 else '↓'}"
    if on:
        return "SQUEEZE 蓄能"
    return f"OPEN {'↑' if mom >= 0 else '↓'}"


def load_positions() -> Dict[str, dict]:
    try:
        from state.position_manager import position_manager
        pos = position_manager.get()
        return pos if isinstance(pos, dict) else {}
    except Exception:
        for p in (Path("data/positions_state.json"), Path("/app/data/positions_state.json")):
            if p.exists():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass
    return {}


def load_closed_trades(limit: int = 20) -> pd.DataFrame:
    cols = ["signal_id", "symbol", "direction", "regime", "entry_price", "exit_price",
            "pnl_r", "exit_reason", "model_ev", "confidence", "exit_timestamp"]
    for db in (Path("data/v6_research.db"), Path("/app/data/v6_research.db"), Path("v6_research.db")):
        if not db.exists():
            continue
        try:
            conn = sqlite3.connect(str(db))
            df = pd.read_sql_query(
                f"""SELECT signal_id, symbol, direction, regime, entry_price, exit_price,
                           pnl_r, exit_reason, model_ev, confidence, exit_timestamp
                    FROM trade_snapshots
                    WHERE pnl_r IS NOT NULL AND exit_reason IS NOT NULL
                      AND exit_reason NOT IN ('OPEN','')
                    ORDER BY COALESCE(exit_timestamp, timestamp) DESC LIMIT {int(limit)}""",
                conn,
            )
            conn.close()
            return df
        except Exception:
            continue
    return pd.DataFrame(columns=cols)


def trade_stats(df: pd.DataFrame) -> Dict[str, Any]:
    if df is None or df.empty or "pnl_r" not in df.columns:
        return {"n": 0, "winrate": 0, "pf": 0, "avg_r": 0, "sum_r": 0}
    s = pd.to_numeric(df["pnl_r"], errors="coerce").dropna()
    if s.empty:
        return {"n": 0, "winrate": 0, "pf": 0, "avg_r": 0, "sum_r": 0}
    wins, losses = s[s > 0], s[s <= 0]
    gp, gl = float(wins.sum()) if len(wins) else 0.0, float((-losses).sum()) if len(losses) else 0.0
    pf = (gp / gl) if gl > 1e-9 else (99.0 if gp > 0 else 0.0)
    return {"n": int(len(s)), "winrate": float((s > 0).mean() * 100), "pf": round(pf, 2),
            "avg_r": round(float(s.mean()), 3), "sum_r": round(float(s.sum()), 3)}


def _fmt(v, nd=2, suffix=""):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "—"
    try:
        return f"{float(v):.{nd}f}{suffix}"
    except Exception:
        return str(v)


def make_kline_figure(df: pd.DataFrame, title: str, positions: dict, symbol: str = "") -> Any:
    if go is None or df is None or df.empty:
        return None
    d = df.tail(80).copy()
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.04,
        row_heights=[0.55, 0.2, 0.25],
        subplot_titles=(title, "Volume", "RSI"),
    )
    fig.add_trace(go.Candlestick(
        x=d["dt"], open=d["open"], high=d["high"], low=d["low"], close=d["close"],
        name="OHLC", increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
    ), row=1, col=1)
    for col, color, name in (("ema20", "#42a5f5", "EMA20"), ("ema50", "#ffca28", "EMA50"), ("ema200", "#ab47bc", "EMA200")):
        if col in d.columns:
            fig.add_trace(go.Scatter(x=d["dt"], y=d[col], mode="lines", name=name, line=dict(width=1.1, color=color)), row=1, col=1)

    pos = (positions or {}).get(symbol) or {}
    if pos:
        for y, name, color in (
            (pos.get("entry") or pos.get("entry_price"), "Entry", "#00e676"),
            (pos.get("current_sl") or pos.get("sl"), "SL", "#ff1744"),
            (pos.get("tp1"), "TP1", "#76ff03"),
            (pos.get("tp2"), "TP2", "#c6ff00"),
            (pos.get("tp3"), "TP3", "#eeff41"),
        ):
            if y:
                fig.add_hline(y=float(y), line_dash="dot", line_color=color, annotation_text=name, row=1, col=1)

    colors = ["#26a69a" if c >= o else "#ef5350" for o, c in zip(d["open"], d["close"])]
    fig.add_trace(go.Bar(x=d["dt"], y=d["volume"], marker_color=colors, opacity=0.7, name="Vol"), row=2, col=1)
    if "rsi" in d.columns:
        fig.add_trace(go.Scatter(x=d["dt"], y=d["rsi"], mode="lines", line=dict(color="#29b6f6", width=1.4), name="RSI"), row=3, col=1)
        fig.add_hline(y=70, line_dash="dash", line_color="#ef5350", row=3, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="#26a69a", row=3, col=1)

    fig.update_layout(
        template="plotly_dark", height=520, margin=dict(l=36, r=16, t=36, b=16),
        xaxis_rangeslider_visible=False, showlegend=False,
        paper_bgcolor="#0d1117", plot_bgcolor="#0d1117", font=dict(color="#e6edf3", size=11),
    )
    fig.update_xaxes(showgrid=True, gridcolor="#21262d")
    fig.update_yaxes(showgrid=True, gridcolor="#21262d")
    return fig


def make_depth_figure(ob: dict, symbol: str) -> Any:
    if go is None:
        return None
    bids, asks = ob.get("bids") or [], ob.get("asks") or []
    if not bids and not asks:
        fig = go.Figure()
        fig.update_layout(title=f"{symbol} 盘口无数据", template="plotly_dark", height=280,
                          paper_bgcolor="#0d1117", plot_bgcolor="#0d1117")
        return fig
    bp, bs = zip(*bids) if bids else ([], [])
    ap, asz = zip(*asks) if asks else ([], [])
    # 累计深度
    b_cum = list(pd.Series(bs).cumsum()) if bs else []
    a_cum = list(pd.Series(asz).cumsum()) if asz else []
    fig = go.Figure()
    if bp:
        fig.add_trace(go.Scatter(x=list(bp), y=b_cum, fill="tozeroy", name="Bids",
                                 line=dict(color="#26a69a"), fillcolor="rgba(38,166,154,0.35)"))
    if ap:
        fig.add_trace(go.Scatter(x=list(ap), y=a_cum, fill="tozeroy", name="Asks",
                                 line=dict(color="#ef5350"), fillcolor="rgba(239,83,80,0.35)"))
    fig.update_layout(
        title=f"{symbol} 订单簿深度", template="plotly_dark", height=280,
        margin=dict(l=40, r=16, t=36, b=30), paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
        font=dict(color="#e6edf3"), xaxis_title="价格", yaxis_title="累计数量",
        legend=dict(orientation="h"),
    )
    return fig


def make_liq_heat_figure(df: pd.DataFrame, symbol: str) -> Any:
    if go is None or df is None or df.empty:
        return None
    d = fetch_liquidations_proxy(symbol, df)
    if d.empty:
        return None
    fig = go.Figure()
    fig.add_trace(go.Bar(x=d["dt"], y=d["long_liq_proxy"], name="多头清算压力(代理)", marker_color="#ef5350"))
    fig.add_trace(go.Bar(x=d["dt"], y=-d["short_liq_proxy"], name="空头清算压力(代理)", marker_color="#26a69a"))
    fig.update_layout(
        title=f"{symbol} 清算压力代理（影线×放量，非交易所真实清算单）",
        template="plotly_dark", height=260, barmode="relative",
        margin=dict(l=40, r=16, t=40, b=30), paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
        font=dict(color="#e6edf3"), legend=dict(orientation="h", y=1.12),
    )
    return fig


def make_heat_figure(btc: pd.DataFrame, eth: pd.DataFrame) -> Any:
    if go is None:
        return None
    rows, ylabels = [], []
    for name, df in (("BTC", btc), ("ETH", eth)):
        if df is None or df.empty:
            continue
        last = df.iloc[-1]
        rsi = float(last.get("rsi") or 50)
        vr = float(last.get("vol_ratio") or 1)
        trend = 1 if float(last.get("ema50") or 0) > float(last.get("ema200") or 0) else -1
        sqz = 1 if not bool(last.get("sqz_on")) else -0.5
        mom = float(last.get("sqz_mom") or 0)
        atr = abs(float(last.get("atr") or 1)) + 1e-9
        rows.append([(rsi - 50) / 50, min(max((vr - 1) / 1.5, -1), 1), trend, sqz, min(max(mom / atr, -1), 1)])
        ylabels.append(name)
    if not rows:
        return None
    fig = go.Figure(data=go.Heatmap(
        z=rows, x=["RSI", "量能", "EMA趋势", "SQZ", "动量"], y=ylabels,
        colorscale="RdYlGn", zmid=0, colorbar=dict(title="强度"),
    ))
    fig.update_layout(template="plotly_dark", height=200, margin=dict(l=50, r=20, t=30, b=30),
                      paper_bgcolor="#0d1117", plot_bgcolor="#0d1117", font=dict(color="#e6edf3"),
                      title="多因子热力")
    return fig


def market_card(symbol: str, df: pd.DataFrame, ticker: dict, funding: Optional[float]) -> str:
    last = ticker.get("last") or (float(df["close"].iloc[-1]) if df is not None and len(df) else None)
    ch = ticker.get("change24h")
    ch_s = f"{ch:+.2f}%" if ch is not None else "—"
    color = "#26a69a" if (ch or 0) >= 0 else "#ef5350"
    rsi = float(df["rsi"].iloc[-1]) if df is not None and "rsi" in df.columns else None
    reg = regime_from_df(df) if df is not None else "—"
    sqz = sqz_state(df) if df is not None else "—"
    vr = float(df["vol_ratio"].iloc[-1]) if df is not None and "vol_ratio" in df.columns else None
    atr = float(df["atr"].iloc[-1]) if df is not None and "atr" in df.columns else None
    fr = f"{funding:+.4f}%" if funding is not None else "—"
    return f"""
<div style="background:linear-gradient(145deg,#161b22,#0d1117);border:1px solid #30363d;border-radius:12px;padding:14px 16px;">
  <div style="display:flex;justify-content:space-between;align-items:center;">
    <div style="font-size:18px;font-weight:700;color:#e6edf3;">{symbol}</div>
    <div style="font-size:12px;padding:2px 8px;border-radius:8px;background:#21262d;color:#8b949e;">1H {reg}</div>
  </div>
  <div style="font-size:26px;font-weight:700;color:{color};margin:6px 0;">{_fmt(last,2)}
    <span style="font-size:13px;">{ch_s}</span></div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px;color:#c9d1d9;">
    <div>RSI <b style="color:#e6edf3">{_fmt(rsi,1)}</b></div>
    <div>量比 <b style="color:#e6edf3">{_fmt(vr,2)}x</b></div>
    <div>ATR <b style="color:#e6edf3">{_fmt(atr,2)}</b></div>
    <div>资金费率 <b style="color:#e6edf3">{fr}</b></div>
    <div>SQZMOM <b style="color:#ffca28">{sqz}</b></div>
    <div>24H高 <b>{_fmt(ticker.get('high24h'),2)}</b></div>
  </div>
</div>"""


def positions_md(positions: dict, prices: Dict[str, float]) -> str:
    if not positions:
        return "### 持仓\n\n_当前空仓_"
    lines = ["### 持仓监控", ""]
    for sym, pos in positions.items():
        if not isinstance(pos, dict):
            continue
        d = pos.get("direction") or pos.get("side") or "?"
        entry = float(pos.get("entry") or pos.get("entry_price") or 0)
        sl = float(pos.get("current_sl") or pos.get("sl") or 0)
        risk = float(pos.get("initial_risk") or abs(entry - float(pos.get("sl") or sl) or 0) or 1)
        px = prices.get(sym) or prices.get(_norm_sym(sym))
        if px and entry and risk:
            r = (px - entry) / risk if str(d).lower().startswith("long") else (entry - px) / risk
        else:
            r = None
        tp1, tp2, tp3 = pos.get("tp1"), pos.get("tp2"), pos.get("tp3")
        risk_abs = abs(entry - float(sl)) if entry and sl else 0
        rr = abs(float(tp1) - entry) / risk_abs if tp1 and risk_abs else None
        lines.append(
            f"**{sym}** `{d}` stage={pos.get('stage',0)} | 入场 `{_fmt(entry)}` 现价 `{_fmt(px)}` "
            f"| 浮盈 **{_fmt(r,2)}R** | SL `{_fmt(sl)}` | TP `{_fmt(tp1)}/{_fmt(tp2)}/{_fmt(tp3)}` "
            f"| RR `{_fmt(rr,2)}` | score `{_fmt(pos.get('score'),1)}` EV `{_fmt(pos.get('ev') or pos.get('expected_value'),3)}`"
        )
        lines.append("")
    return "\n".join(lines)


def build_ai_context(frames: dict, positions: dict, prices: dict, fundings: dict) -> dict:
    """打包当前仪表盘快照给 DeepSeek。"""
    snap = {"asof": datetime.now(BJ).isoformat(), "markets": {}, "positions": positions}
    for sym in SYMBOLS:
        df = frames.get(sym)
        if df is None or df.empty:
            continue
        last = df.iloc[-1]
        snap["markets"][sym] = {
            "price": prices.get(sym),
            "funding_pct": fundings.get(sym),
            "rsi": float(last.get("rsi") or 0),
            "vol_ratio": float(last.get("vol_ratio") or 0),
            "atr": float(last.get("atr") or 0),
            "regime": regime_from_df(df),
            "sqz": sqz_state(df),
            "ema50": float(last.get("ema50") or 0),
            "ema200": float(last.get("ema200") or 0),
        }
    return snap


def run_ai_advice(frames: dict, positions: dict, prices: dict, fundings: dict, note: str = "") -> str:
    if ask_deepseek is None and analyze_signal_result is None:
        return "❌ 未加载 ai_advisor。请部署 utils/ai_advisor.py 并配置 DEEPSEEK_API_KEY。"
    ctx = build_ai_context(frames, positions, prices, fundings)
    # 优先用 ask_deepseek 直接吃仪表盘上下文
    if ask_deepseek is not None:
        out = ask_deepseek(ctx, extra_note=note or "请基于当前仪表盘快照给出手动交易建议（观望/做多/做空+关键位）")
        if out.get("ok"):
            return f"✅ DeepSeek ({out.get('latency_ms')}ms)\n\n{out.get('text')}"
        return f"❌ AI 失败: {out.get('error')}"
    # fallback: 用第一个有仓或 BTC 构造 result
    result = {"symbol": "BTC/USDT", "features": ctx}
    out = analyze_signal_result(result, extra_note=note)
    if out.get("ok"):
        return out.get("text") or ""
    return f"❌ {out.get('error')}"


# 模块级缓存：供 AI 按钮使用最近一次刷新快照
_LAST_SNAP: Dict[str, Any] = {}


def refresh_dashboard(timeframe: str = "15m") -> Tuple:
    global _LAST_SNAP
    empty = None
    try:
        frames, prices, fundings = {}, {}, {}
        cards = []
        # 主周期
        for sym in SYMBOLS:
            try:
                df = enrich(fetch_ohlcv(sym, timeframe=timeframe, limit=120))
            except Exception as e:
                frames[sym] = pd.DataFrame()
                cards.append(f"<div style='color:#ef5350'>{sym} 失败: {e}</div>")
                continue
            frames[sym] = df
            tk = fetch_ticker(sym)
            fr = fetch_funding(sym)
            fundings[sym] = fr
            prices[sym] = tk.get("last") or float(df["close"].iloc[-1])
            cards.append(market_card(sym, df, tk, fr))

        # 多周期 15m / 1h / 4h（BTC 为主展示条）
        mtf_figs = []
        for tf in MTF:
            try:
                d = enrich(fetch_ohlcv("BTC/USDT", timeframe=tf, limit=100))
                mtf_figs.append(make_kline_figure(d, f"BTC {tf}", load_positions(), "BTC/USDT"))
            except Exception:
                mtf_figs.append(None)
        while len(mtf_figs) < 3:
            mtf_figs.append(None)

        positions = load_positions()
        closed = load_closed_trades(30)
        stats = trade_stats(closed)

        fig_btc = make_kline_figure(frames.get("BTC/USDT"), f"BTC/USDT {timeframe}", positions, "BTC/USDT")
        fig_eth = make_kline_figure(frames.get("ETH/USDT"), f"ETH/USDT {timeframe}", positions, "ETH/USDT")
        heat = make_heat_figure(frames.get("BTC/USDT"), frames.get("ETH/USDT"))

        ob_btc = fetch_orderbook("BTC/USDT")
        ob_eth = fetch_orderbook("ETH/USDT")
        depth_btc = make_depth_figure(ob_btc, "BTC/USDT")
        depth_eth = make_depth_figure(ob_eth, "ETH/USDT")
        liq_btc = make_liq_heat_figure(frames.get("BTC/USDT"), "BTC/USDT")
        liq_eth = make_liq_heat_figure(frames.get("ETH/USDT"), "ETH/USDT")

        html_cards = "<div style='display:grid;grid-template-columns:1fr 1fr;gap:10px;'>" + "".join(cards) + "</div>"
        pos_md = positions_md(positions, prices)
        now = datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")
        status = (
            f"### 系统快览\n- 刷新: **{now} CST** · 周期 **{timeframe}**\n"
            f"- 持仓 **{len(positions)}** · 近窗 **{stats['n']}** 笔 · "
            f"胜率 **{stats['winrate']:.1f}%** · PF **{stats['pf']}** · 累计 **{stats['sum_r']:+.2f}R**\n"
            f"- 自动刷新开启后约每 60s 更新（受 HF 负载影响）\n"
        )

        _LAST_SNAP = {"frames": frames, "positions": positions, "prices": prices, "fundings": fundings}

        return (
            status,
            html_cards,
            fig_btc,
            fig_eth,
            mtf_figs[0],
            mtf_figs[1],
            mtf_figs[2],
            heat,
            depth_btc,
            depth_eth,
            liq_btc,
            liq_eth,
            pos_md,
            closed,
            f"上次刷新: {datetime.now(BJ).strftime('%H:%M:%S')}",
        )
    except Exception as e:
        err = f"刷新失败: {e}"
        return (err, f"<div>{err}</div>", empty, empty, empty, empty, empty, empty,
                empty, empty, empty, empty, err, pd.DataFrame(), err)


def ai_from_last_snap(note: str = "") -> str:
    if not _LAST_SNAP:
        return "请先点击「刷新仪表盘」再调用 AI。"
    return run_ai_advice(
        _LAST_SNAP.get("frames") or {},
        _LAST_SNAP.get("positions") or {},
        _LAST_SNAP.get("prices") or {},
        _LAST_SNAP.get("fundings") or {},
        note=note or "",
    )


def build_dashboard_tab():
    if gr is None:
        raise RuntimeError("gradio 未安装")

    with gr.Tab("交易仪表盘"):
        gr.Markdown("# SMC 交易仪表盘\n自动刷新 · 多周期 · 盘口深度 · 清算代理热力 · DeepSeek 一键分析")
        with gr.Row():
            tf = gr.Dropdown(choices=["5m", "15m", "30m", "1h", "4h"], value="15m", label="主图周期", scale=1)
            btn = gr.Button("刷新仪表盘", variant="primary", scale=1)
            ts = gr.Textbox(label="状态", interactive=False, scale=2)

        status = gr.Markdown("点击刷新或等待自动刷新…")
        cards = gr.HTML("<div style='color:#8b949e'>等待数据…</div>")

        gr.Markdown("### 主周期 BTC / ETH")
        with gr.Row():
            plot_btc = gr.Plot(label="BTC")
            plot_eth = gr.Plot(label="ETH")

        gr.Markdown("### 多周期并排（BTC 15m · 1H · 4H）")
        with gr.Row():
            mtf15 = gr.Plot(label="BTC 15m")
            mtf1h = gr.Plot(label="BTC 1H")
            mtf4h = gr.Plot(label="BTC 4H")

        heat = gr.Plot(label="多因子热力")

        gr.Markdown("### 订单簿深度")
        with gr.Row():
            depth_btc = gr.Plot(label="BTC 深度")
            depth_eth = gr.Plot(label="ETH 深度")

        gr.Markdown("### 清算压力代理热力（影线×放量，非交易所真实清算流）")
        with gr.Row():
            liq_btc = gr.Plot(label="BTC 清算代理")
            liq_eth = gr.Plot(label="ETH 清算代理")

        pos = gr.Markdown("### 持仓")
        closed = gr.Dataframe(label="最近平仓", interactive=False, wrap=True)

        gr.Markdown("### DeepSeek 入场顾问（只建议，不自动下单）")
        with gr.Row():
            ai_note = gr.Textbox(label="补充说明", value="结合 SMC + SQZMOM + 费率，给出是否手动入场", scale=3)
            ai_btn = gr.Button("一键 AI 分析当前快照", variant="secondary", scale=1)
        ai_out = gr.Textbox(label="AI 建议", lines=16)

        outs = [status, cards, plot_btc, plot_eth, mtf15, mtf1h, mtf4h, heat,
                depth_btc, depth_eth, liq_btc, liq_eth, pos, closed, ts]

        btn.click(fn=refresh_dashboard, inputs=[tf], outputs=outs)
        # 定时自动刷新（60s）—— Gradio 4/5: every 参数在 load 或 timer
        try:
            timer = gr.Timer(value=60, active=True)
            timer.tick(fn=refresh_dashboard, inputs=[tf], outputs=outs)
        except Exception:
            # 旧版 Gradio：用 demo.load every
            try:
                gr.HTML("")  # no-op anchor
            except Exception:
                pass

        ai_btn.click(fn=ai_from_last_snap, inputs=[ai_note], outputs=[ai_out])

        gr.Markdown(
            """
---
**说明**
- **自动刷新**：约每 60 秒（`gr.Timer`）；也可手动刷新  
- **清算热力**：用长下影/上影 × 放量作代理，**不是** Coinglass 级真实清算数据  
- **AI**：需 HF Secret `DEEPSEEK_API_KEY` + `utils/ai_advisor.py`  
- 本页**不会**向交易所下单  
"""
        )
    return True
