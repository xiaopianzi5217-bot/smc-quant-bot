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
import numpy as np

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
    sym = _bitget_sym(symbol)
    endpoints = [
        ("https://api.bitget.com/api/v2/mix/market/current-fund-rate", {"symbol": sym, "productType": "USDT-FUTURES"}),
        ("https://api.bitget.com/api/v2/mix/market/current-fund-rate", {"symbol": sym, "productType": "umcbl"}),
        ("https://api.bitget.com/api/v2/mix/market/current-fund-rate", {"symbol": sym}),
    ]
    for url, params in endpoints:
        try:
            r = requests.get(url, params=params, timeout=8)
            data = r.json()
            if str(data.get("code")) != "00000":
                continue
            d = data.get("data")
            if isinstance(d, list):
                d = d[0] if d else {}
            if not isinstance(d, dict):
                continue
            fr = d.get("fundingRate") or d.get("fundingRateStr")
            if fr is None:
                continue
            return float(fr) * 100.0
        except Exception:
            continue
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

def _sqzmom_series(df: pd.DataFrame, length: int = 20) -> pd.DataFrame:
    """对齐 SQZMOM[+]：BB(2.0)/KC(ATR·1.5) 挤压 + 动量柱 hist=close-KC_basis。"""
    d = df.copy()
    close = d["close"].astype(float)
    high = d["high"].astype(float)
    low = d["low"].astype(float)
    ma = close.rolling(length, min_periods=length).mean()
    std = close.rolling(length, min_periods=length).std()
    upper_bb, lower_bb = ma + 2.0 * std, ma - 2.0 * std
    tr = pd.concat([(high - low).abs(), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(length, min_periods=length).mean()
    kc = close.ewm(span=length, adjust=False).mean()
    upper_kc, lower_kc = kc + 1.5 * atr, kc - 1.5 * atr
    sqz_on = (upper_bb < upper_kc) & (lower_bb > lower_kc)
    hist = close - kc
    d["sqz_on"] = sqz_on
    d["sqz_hist"] = hist
    d["sqz_released"] = sqz_on.shift(1).fillna(False).astype(bool) & (~sqz_on.astype(bool))
    d["sqz_hist_rising"] = hist > hist.shift(1)
    d["sqz_hist_falling"] = hist < hist.shift(1)
    # 白柱：动量同号但减速（SQZMOM Plus 颜色逻辑简化）
    d["white_bear"] = (hist >= 0) & (hist < hist.shift(1))
    d["white_bull"] = (hist < 0) & (hist >= hist.shift(1))
    return d


def _pivot_flags(series: pd.Series, left: int = 3, right: int = 1) -> tuple:
    """简化 pivot high/low（对齐脚本 lbL/lbR 思想，右窗=1 降低滞后）。"""
    n = len(series)
    ph = pd.Series(False, index=series.index)
    pl = pd.Series(False, index=series.index)
    vals = series.values
    for i in range(left, n - right):
        window = vals[i - left : i + right + 1]
        if vals[i] == np.max(window) and vals[i] > vals[i - 1]:
            ph.iloc[i] = True
        if vals[i] == np.min(window) and vals[i] < vals[i - 1]:
            pl.iloc[i] = True
    return ph, pl


def detect_sqz_divergences(df: pd.DataFrame) -> Dict[str, Any]:
    """
    对齐 SQZMOM[+] / Better Divergence 规则：
    - Regular Bull (R): 价格 LL + 动量 HL，且 osc<0
    - Regular Bear (R): 价格 HH + 动量 LH，且 osc>0
    - Hidden Bull (H): 价格 HL + 动量 LL，且 osc<0
    - Hidden Bear (H): 价格 LH + 动量 HH，且 osc>0
    连续背离：近 20 根内同向 Regular ≥2 → 禁止追单并收紧止损。
    """
    import numpy as np
    d = _sqzmom_series(df)
    close = d["close"].astype(float)
    hist = d["sqz_hist"].astype(float)
    # 用价格与动量的滚动极值近似 pivot 比较（稳健、少 repaint）
    look = 5
    price_ll = close <= close.rolling(look, min_periods=3).min()
    price_hh = close >= close.rolling(look, min_periods=3).max()
    price_hl = (close > close.shift(look)) & (close <= close.rolling(look).max())
    price_lh = (close < close.shift(look)) & (close >= close.rolling(look).min())
    osc_hl = hist > hist.shift(look)
    osc_ll = hist < hist.shift(look)
    osc_lh = hist < hist.shift(look)
    osc_hh = hist > hist.shift(look)

    reg_bull = price_ll & osc_hl & (hist < 0)
    reg_bear = price_hh & osc_lh & (hist > 0)
    hid_bull = price_hl & osc_ll & (hist < 0)
    hid_bear = price_lh & osc_hh & (hist > 0)

    # 确认：shift(1) 降 repaint
    reg_bull_c = reg_bull.shift(1).fillna(False).astype(bool)
    reg_bear_c = reg_bear.shift(1).fillna(False).astype(bool)
    hid_bull_c = hid_bull.shift(1).fillna(False).astype(bool)
    hid_bear_c = hid_bear.shift(1).fillna(False).astype(bool)

    win = 20
    reg_bull_n = int(reg_bull_c.tail(win).sum())
    reg_bear_n = int(reg_bear_c.tail(win).sum())
    hid_bull_n = int(hid_bull_c.tail(win).sum())
    hid_bear_n = int(hid_bear_c.tail(win).sum())

    last = d.iloc[-1]
    return {
        "hist": float(last["sqz_hist"]),
        "sqz_on": bool(last["sqz_on"]),
        "released": bool(last["sqz_released"]),
        "regular_bull_R": bool(reg_bull_c.iloc[-1]),
        "regular_bear_R": bool(reg_bear_c.iloc[-1]),
        "hidden_bull_H": bool(hid_bull_c.iloc[-1]),
        "hidden_bear_H": bool(hid_bear_c.iloc[-1]),
        "reg_bull_count_20": reg_bull_n,
        "reg_bear_count_20": reg_bear_n,
        "hid_bull_count_20": hid_bull_n,
        "hid_bear_count_20": hid_bear_n,
        "serial_regular_bull": reg_bull_n >= 2,
        "serial_regular_bear": reg_bear_n >= 2,
        "white_bull": bool(last.get("white_bull")),
        "white_bear": bool(last.get("white_bear")),
        "phase": "SQUEEZE" if bool(last["sqz_on"]) else ("RELEASE" if bool(last["sqz_released"]) else "OPEN"),
        "bias": "DOWN" if float(last["sqz_hist"]) < 0 else ("UP" if float(last["sqz_hist"]) > 0 else "FLAT"),
    }


def analyze_sqzmom_depth(df: pd.DataFrame) -> Dict[str, Any]:
    """SQZMOM[+] 深度：挤压状态机 + R/H 背离 + 连续正规背离风控。"""
    if df is None or len(df) < 30:
        return {"ok": False, "summary": "K线不足"}
    base = df if "ema50" in df.columns else enrich(df)
    div = detect_sqz_divergences(base)
    # 挤压持续
    d = _sqzmom_series(base)
    dur = 0
    for i in range(len(d) - 1, -1, -1):
        if bool(d["sqz_on"].iloc[i]):
            dur += 1
        elif dur > 0:
            break
        else:
            break
    rules = []
    if div["phase"] == "SQUEEZE" and dur >= 6:
        rules.append("SQZ 长挤压：禁止市价赌方向，等 RELEASE 再顺势")
    if div["released"]:
        rules.append(f"SQZ 刚释放，动量 {div['bias']}：可作方向过滤，需 SMC 同向确认")
    if div["regular_bear_R"]:
        rules.append("正规顶背离 R：价创新高动量更弱 → 减多/等结构空，不追多")
    if div["regular_bull_R"]:
        rules.append("正规底背离 R：价创新低动量抬高 → 减空/等结构多，不追空")
    if div["hidden_bear_H"]:
        rules.append("隐藏顶背离 H：回调中动量仍强空 → 顺势空的延续信号（需结构）")
    if div["hidden_bull_H"]:
        rules.append("隐藏底背离 H：反弹中动量仍强多 → 顺势多的延续信号（需结构）")
    if div["serial_regular_bear"]:
        rules.append("⚠ 近20根≥2次正规顶背离R：禁止追空；空单上移止损/减仓防连续打损")
    if div["serial_regular_bull"]:
        rules.append("⚠ 近20根≥2次正规底背离R：禁止追多；多单下移止损/减仓防连续打损")
    if div["white_bear"]:
        rules.append("动量白柱空向：上涨减速，只作减多或等待")
    if div["white_bull"]:
        rules.append("动量白柱多向：下跌减速，只作减空或等待")

    return {
        "ok": True,
        **div,
        "squeeze_duration": dur,
        "rules": rules,
        "summary": (
            f"{div['phase']} {div['bias']} hist={div['hist']:.4f} "
            f"R↑{div['reg_bull_count_20']}/R↓{div['reg_bear_count_20']} "
            f"H↑{div['hid_bull_count_20']}/H↓{div['hid_bear_count_20']}"
            + (" |连续R顶" if div["serial_regular_bear"] else "")
            + (" |连续R底" if div["serial_regular_bull"] else "")
        ),
    }


def analyze_smc_depth(df: pd.DataFrame, scan: Optional[dict] = None) -> Dict[str, Any]:
    """
    对齐 SMC{WeloTrades} 可落地要素：
    OB / FVG / BSL·SSL(流动性) / BOS·CHOCH 代理 / 溢价折价区。
    """
    scan = scan or {}
    if df is None or len(df) < 20:
        return {"ok": False, "summary": "K线不足"}
    d = enrich(df) if "ema50" not in df.columns else df
    proxy = smc_proxy_from_df(d)
    last = d.iloc[-1]
    px = float(last["close"])
    e50 = float(last.get("ema50") or px)
    e200 = float(last.get("ema200") or px)
    premium = px > e50 and px > e200
    discount = px < e50 and px < e200
    zone = "PREMIUM" if premium else ("DISCOUNT" if discount else "EQUILIBRIUM")

    # 简易 BOS/CHOCH 代理：收盘突破近 look 高低点
    look = 10
    hh = float(d["high"].iloc[-look:-1].max())
    ll = float(d["low"].iloc[-look:-1].min())
    bos_up = px > hh
    bos_dn = px < ll
    # CHOCH 代理：与 ema 堆叠冲突的突破
    stack = proxy.get("ema_stack")
    choch_up = bos_up and stack == "BEAR_STACK"
    choch_dn = bos_dn and stack == "BULL_STACK"

    confluence = []
    if zone == "PREMIUM":
        confluence.append("溢价区(Welo)：优先供给/空头 OB，不做多")
    if zone == "DISCOUNT":
        confluence.append("折价区(Welo)：优先需求/多头 OB，不做空")
    if scan.get("bearish_ob"):
        confluence.append(f"Bearish OB: {scan.get('bearish_ob')}")
    if scan.get("bullish_ob"):
        confluence.append(f"Bullish OB: {scan.get('bullish_ob')}")
    if scan.get("bullish_fvg") is not None:
        confluence.append(f"Bullish FVG: {scan.get('bullish_fvg')}（回补前慎追空）")
    if scan.get("bearish_fvg") is not None:
        confluence.append(f"Bearish FVG: {scan.get('bearish_fvg')}（回补前慎追多）")
    if scan.get("is_bsl_swept"):
        confluence.append("BSL 流动性已扫：等回抽再空，忌扫单瞬间追空")
    if scan.get("is_ssl_swept"):
        confluence.append("SSL 流动性已扫：等回抽再多，忌扫单瞬间追多")
    if proxy.get("sweep_high_proxy"):
        confluence.append("代理扫高收回 → 空头猎杀流动性")
    if proxy.get("sweep_low_proxy"):
        confluence.append("代理扫低收回 → 多头猎杀流动性")
    if choch_up:
        confluence.append("CHOCH↑代理：空头结构下出现向上突破，空单风险升高")
    if choch_dn:
        confluence.append("CHOCH↓代理：多头结构下出现向下突破，多单风险升高")
    if bos_up and not choch_up:
        confluence.append("BOS↑代理：顺势向上结构延续")
    if bos_dn and not choch_dn:
        confluence.append("BOS↓代理：顺势向下结构延续")

    return {
        "ok": True,
        "zone": zone,
        "ema_stack": stack,
        "swing_high": proxy.get("swing_high"),
        "swing_low": proxy.get("swing_low"),
        "near_swing_high": proxy.get("near_swing_high"),
        "near_swing_low": proxy.get("near_swing_low"),
        "sweep_high_proxy": proxy.get("sweep_high_proxy"),
        "sweep_low_proxy": proxy.get("sweep_low_proxy"),
        "bos_up_proxy": bos_up,
        "bos_dn_proxy": bos_dn,
        "choch_up_proxy": choch_up,
        "choch_dn_proxy": choch_dn,
        "bearish_ob": scan.get("bearish_ob"),
        "bullish_ob": scan.get("bullish_ob"),
        "bullish_fvg": scan.get("bullish_fvg"),
        "bearish_fvg": scan.get("bearish_fvg"),
        "is_bsl_swept": scan.get("is_bsl_swept"),
        "is_ssl_swept": scan.get("is_ssl_swept"),
        "confluence": confluence,
        "summary": f"{zone} {stack} BOS↑{bos_up}/↓{bos_dn} CHOCH↑{choch_up}/↓{choch_dn}",
    }


def combine_smc_sqz_guidance(smc: dict, sqz: dict, direction_hint: Optional[str] = None) -> Dict[str, Any]:
    """
    SMC{Welo} × SQZMOM[+] 联合裁决。
    连续正规背离 R → 禁止追单并要求收紧止损（防连续打损）。
    """
    warns = []
    allow_short, allow_long = True, True
    prefer = "WAIT"
    if not smc.get("ok") or not sqz.get("ok"):
        return {"prefer": "WAIT", "allow_long": False, "allow_short": False,
                "warns": ["数据不足"], "entry_quality": "LOW", "playbook": "观望"}

    # 连续正规背离 = 硬约束
    if sqz.get("serial_regular_bear"):
        allow_short = False
        warns.append("连续正规顶背离R：禁止追空；已有空单上移止损/减仓")
    if sqz.get("serial_regular_bull"):
        allow_long = False
        warns.append("连续正规底背离R：禁止追多；已有多单下移止损/减仓")

    # 单次 R 背离：降低追单质量
    if sqz.get("regular_bear_R"):
        warns.append("当前正规顶背离R：不宜追多，空需等溢价/OB")
    if sqz.get("regular_bull_R"):
        warns.append("当前正规底背离R：不宜追空，多需等折价/OB")

    # 隐藏背离：顺势延续，仍要结构
    if sqz.get("hidden_bear_H"):
        warns.append("隐藏顶背离H：偏顺势空延续，需在 PREMIUM/Bear OB 入场")
    if sqz.get("hidden_bull_H"):
        warns.append("隐藏底背离H：偏顺势多延续，需在 DISCOUNT/Bull OB 入场")

    if smc.get("zone") == "PREMIUM":
        allow_long = False
        warns.append("溢价区不做多")
    if smc.get("zone") == "DISCOUNT":
        allow_short = False
        warns.append("折价区不做空")

    if smc.get("choch_up_proxy"):
        warns.append("CHOCH↑：空头叙事削弱")
        allow_short = allow_short and False if sqz.get("bias") == "DOWN" else allow_short
    if smc.get("choch_dn_proxy"):
        warns.append("CHOCH↓：多头叙事削弱")

    if sqz.get("phase") == "SQUEEZE":
        prefer = "WAIT_RELEASE"
        warns.append("SQZ 挤压中：只挂单等释放，不市价追")
    elif sqz.get("bias") == "DOWN" and allow_short:
        if smc.get("zone") in ("PREMIUM", "EQUILIBRIUM") or smc.get("sweep_high_proxy") or smc.get("is_bsl_swept") or smc.get("bearish_ob"):
            prefer = "SHORT_SETUP"
        else:
            prefer = "SHORT_WEAK"
            warns.append("动量向下但缺溢价/流动性/OB：质量偏低")
    elif sqz.get("bias") == "UP" and allow_long:
        if smc.get("zone") in ("DISCOUNT", "EQUILIBRIUM") or smc.get("sweep_low_proxy") or smc.get("is_ssl_swept") or smc.get("bullish_ob"):
            prefer = "LONG_SETUP"
        else:
            prefer = "LONG_WEAK"
            warns.append("动量向上但缺折价/流动性/OB：质量偏低")

    if direction_hint:
        d = str(direction_hint).lower()
        if d.startswith("short") and not allow_short:
            prefer = "REJECT_SHORT"
            warns.append("系统想做空但联合规则禁止（连续R背离/折价/CHOCH）")
        if d.startswith("long") and not allow_long:
            prefer = "REJECT_LONG"
            warns.append("系统想做多但联合规则禁止（连续R背离/溢价/CHOCH）")

    quality = "HIGH" if prefer in ("SHORT_SETUP", "LONG_SETUP") else (
        "MED" if prefer in ("SHORT_WEAK", "LONG_WEAK", "WAIT_RELEASE") else "LOW"
    )
    playbooks = {
        "SHORT_SETUP": "溢价/BearOB/扫BSL + SQZ向下 → 回抽入场，止损在流动性高点上；若出现连续R顶背离则立刻上移止损",
        "LONG_SETUP": "折价/BullOB/扫SSL + SQZ向上 → 回抽入场，止损在流动性低点下；若出现连续R底背离则立刻下移止损",
        "WAIT_RELEASE": "等 SQZ RELEASE 且与 SMC 区同向",
        "REJECT_SHORT": "连续R顶或折价区 → 空单规避，防连续打损",
        "REJECT_LONG": "连续R底或溢价区 → 多单规避，防连续打损",
        "SHORT_WEAK": "仅有动量向下，缺结构：观望或极轻仓",
        "LONG_WEAK": "仅有动量向上，缺结构：观望或极轻仓",
    }
    return {
        "prefer": prefer,
        "allow_long": allow_long,
        "allow_short": allow_short,
        "entry_quality": quality,
        "warns": warns,
        "playbook": playbooks.get(prefer, "观望"),
        "divergence_guard": {
            "block_chase_short": bool(sqz.get("serial_regular_bear")),
            "block_chase_long": bool(sqz.get("serial_regular_bull")),
            "tighten_sl_on_serial_div": True,
        },
    }


def tech_analysis_markdown(symbol: str, df: pd.DataFrame, scan: Optional[dict] = None) -> str:
    """仪表盘 SMC×SQZMOM[+] 技术分析。"""
    sqz = analyze_sqzmom_depth(df)
    smc = analyze_smc_depth(df, scan)
    guide = combine_smc_sqz_guidance(smc, sqz, (scan or {}).get("direction"))
    lines = [f"### {symbol} · SMC{{Welo}} × SQZMOM[+] 技术分析", ""]
    if sqz.get("ok"):
        lines.append(f"**SQZMOM**: `{sqz['summary']}`")
        lines.append(f"- 阶段 **{sqz['phase']}** · 动量 **{sqz['bias']}** · 挤压 **{sqz.get('squeeze_duration', 0)}** 根")
        lines.append(
            f"- 背离 R(正规) 底/顶: **{sqz.get('reg_bull_count_20')}** / **{sqz.get('reg_bear_count_20')}** · "
            f"H(隐藏) 底/顶: **{sqz.get('hid_bull_count_20')}** / **{sqz.get('hid_bear_count_20')}**"
        )
        if sqz.get("regular_bull_R") or sqz.get("regular_bear_R") or sqz.get("hidden_bull_H") or sqz.get("hidden_bear_H"):
            flags = []
            if sqz.get("regular_bull_R"):
                flags.append("R底")
            if sqz.get("regular_bear_R"):
                flags.append("R顶")
            if sqz.get("hidden_bull_H"):
                flags.append("H底")
            if sqz.get("hidden_bear_H"):
                flags.append("H顶")
            lines.append(f"- 当前触发: **{', '.join(flags)}**")
        for r in sqz.get("rules") or []:
            lines.append(f"- {r}")
    lines.append("")
    if smc.get("ok"):
        lines.append(f"**SMC**: `{smc['summary']}`")
        lines.append(f"- 区段 **{smc['zone']}** · 堆叠 **{smc['ema_stack']}**")
        lines.append(f"- Swing H/L `{_fmt(smc.get('swing_high'))}` / `{_fmt(smc.get('swing_low'))}`")
        lines.append(f"- OB 空/多 `{smc.get('bearish_ob')}` / `{smc.get('bullish_ob')}`")
        lines.append(f"- FVG 多/空 `{smc.get('bullish_fvg')}` / `{smc.get('bearish_fvg')}`")
        lines.append(f"- BSL/SSL `{smc.get('is_bsl_swept')}` / `{smc.get('is_ssl_swept')}`")
        for c in smc.get("confluence") or []:
            lines.append(f"- {c}")
    lines.append("")
    lines.append(f"**联合裁决**: **{guide['prefer']}** · 质量 **{guide['entry_quality']}**")
    lines.append(f"- 允许多 `{guide['allow_long']}` · 允许空 `{guide['allow_short']}`")
    dg = guide.get("divergence_guard") or {}
    lines.append(f"- 背离护栏: 禁追空=`{dg.get('block_chase_short')}` 禁追多=`{dg.get('block_chase_long')}` 连续背离收紧止损=`{dg.get('tighten_sl_on_serial_div')}`")
    lines.append(f"- 手册: {guide.get('playbook')}")
    for w in guide.get("warns") or []:
        lines.append(f"- ⚠ {w}")
    lines.append("")
    return "\n".join(lines)
def load_last_scan_snapshots() -> Dict[str, Any]:
    """读取 hf_auto_trader 写出的最近扫描快照。"""
    for p in (Path("data/last_scan_snapshot.json"), Path("/app/data/last_scan_snapshot.json")):
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            continue
    return {}


def smc_proxy_from_df(df: pd.DataFrame) -> Dict[str, Any]:
    """在无完整 SMC 引擎时，从 K 线推导轻量结构代理，避免 AI 完全无 SMC 字段。"""
    out = {
        "swing_high": None,
        "swing_low": None,
        "near_swing_high": False,
        "near_swing_low": False,
        "bullish_engulf_proxy": False,
        "bearish_engulf_proxy": False,
        "sweep_high_proxy": False,
        "sweep_low_proxy": False,
        "ema_stack": "UNKNOWN",
    }
    if df is None or len(df) < 10:
        return out
    d = df.tail(30)
    last = d.iloc[-1]
    prev = d.iloc[-2]
    sh = float(d["high"].iloc[-6:-1].max())
    sl = float(d["low"].iloc[-6:-1].min())
    out["swing_high"] = sh
    out["swing_low"] = sl
    c = float(last["close"])
    out["near_swing_high"] = abs(c - sh) / max(sh, 1e-9) < 0.002
    out["near_swing_low"] = abs(c - sl) / max(sl, 1e-9) < 0.002
    out["sweep_high_proxy"] = float(last["high"]) > sh and c < sh
    out["sweep_low_proxy"] = float(last["low"]) < sl and c > sl
    out["bullish_engulf_proxy"] = (
        float(last["close"]) > float(last["open"])
        and float(prev["close"]) < float(prev["open"])
        and float(last["close"]) >= float(prev["open"])
        and float(last["open"]) <= float(prev["close"])
    )
    out["bearish_engulf_proxy"] = (
        float(last["close"]) < float(last["open"])
        and float(prev["close"]) > float(prev["open"])
        and float(last["close"]) <= float(prev["open"])
        and float(last["open"]) >= float(prev["close"])
    )
    e50 = float(last.get("ema50") or 0)
    e200 = float(last.get("ema200") or 0)
    if e50 and e200:
        if c > e50 > e200:
            out["ema_stack"] = "BULL_STACK"
        elif c < e50 < e200:
            out["ema_stack"] = "BEAR_STACK"
        else:
            out["ema_stack"] = "MIXED"
    return out


def build_ai_context(frames: dict, positions: dict, prices: dict, fundings: dict) -> dict:
    """打包完整快照：行情 + 最近系统扫描(score/EV/SMC) + 持仓。"""
    scans = load_last_scan_snapshots()
    snap = {
        "asof": datetime.now(BJ).isoformat(),
        "data_notes": [],
        "markets": {},
        "system_signals": {},
        "positions": positions,
    }
    for sym in SYMBOLS:
        df = frames.get(sym)
        scan = scans.get(sym) if isinstance(scans.get(sym), dict) else {}
        if df is None or (hasattr(df, "empty") and df.empty):
            snap["markets"][sym] = {"error": "no_ohlcv"}
            continue
        last = df.iloc[-1]
        proxy = smc_proxy_from_df(df)
        sqz_d = analyze_sqzmom_depth(df)
        smc_d = analyze_smc_depth(df, scan if isinstance(scan, dict) else {})
        guide = combine_smc_sqz_guidance(smc_d, sqz_d, (scan or {}).get("direction") if isinstance(scan, dict) else None)
        mkt = {
            "price": prices.get(sym),
            "funding_pct": fundings.get(sym),
            "rsi": float(last.get("rsi") or 0),
            "vol_ratio": float(last.get("vol_ratio") or 0),
            "atr": float(last.get("atr") or 0),
            "regime": regime_from_df(df),
            "sqz": sqz_state(df),
            "ema20": float(last.get("ema20") or 0),
            "ema50": float(last.get("ema50") or 0),
            "ema200": float(last.get("ema200") or 0),
            "smc_proxy": proxy,
            "sqzmom_depth": sqz_d,
            "smc_depth": smc_d,
            "smc_sqz_guidance": guide,
        }
        snap["markets"][sym] = mkt
        # 系统扫描字段（若存在）
        if scan:
            age = None
            try:
                age = float(__import__("time").time() - float(scan.get("ts") or 0))
            except Exception:
                age = None
            if scan.get("status") == "NO_RECENT_SIGNAL":
                snap["system_signals"][sym] = {
                    "status": "NO_RECENT_SIGNAL",
                    "age_seconds": age,
                    "note": scan.get("note"),
                    "score": None,
                    "fused_ev": None,
                    "direction": None,
                    "setup_type": None,
                }
                snap["data_notes"].append(f"{sym} 近窗无新形态（非数据缺失）")
                continue
            snap["system_signals"][sym] = {
                "age_seconds": age,
                "direction": scan.get("direction"),
                "setup_type": scan.get("setup_type"),
                "score": scan.get("score"),
                "orig_score": scan.get("orig_score"),
                "fused_ev": scan.get("fused_ev") or scan.get("expected_value"),
                "feedback_ev": scan.get("feedback_ev"),
                "confidence": scan.get("confidence"),
                "entry": scan.get("entry"),
                "sl": scan.get("sl"),
                "tp1": scan.get("tp1"),
                "tp2": scan.get("tp2"),
                "tp3": scan.get("tp3"),
                "rr": scan.get("rr"),
                "regime": scan.get("regime"),
                "htf_blocked": scan.get("htf_blocked"),
                "features": scan.get("features") or {},
                "sqz_data": scan.get("sqz_data") or {},
                "bullish_ob": scan.get("bullish_ob"),
                "bearish_ob": scan.get("bearish_ob"),
                "bullish_fvg": scan.get("bullish_fvg"),
                "bearish_fvg": scan.get("bearish_fvg"),
                "is_bsl_swept": scan.get("is_bsl_swept"),
                "is_ssl_swept": scan.get("is_ssl_swept"),
                "bsl_level": scan.get("bsl_level"),
                "ssl_level": scan.get("ssl_level"),
                "funding_rate_from_scan": scan.get("funding_rate"),
            }
            if age is not None and age > 3600:
                snap["data_notes"].append(f"{sym} 系统扫描快照已超过1小时，仅供参考")
        else:
            snap["system_signals"][sym] = None
            snap["data_notes"].append(f"{sym} 尚无系统扫描快照（需主循环跑过至少一轮）")
        if fundings.get(sym) is None:
            snap["data_notes"].append(f"{sym} funding 拉取失败/为空")
    return snap


def run_ai_advice(frames: dict, positions: dict, prices: dict, fundings: dict, note: str = "") -> str:
    if ask_deepseek is None and analyze_signal_result is None:
        return "❌ 未加载 ai_advisor。请部署 utils/ai_advisor.py 并配置 DEEPSEEK_API_KEY。"
    ctx = build_ai_context(frames, positions, prices, fundings)
    extra = (note or "") + "\n请优先使用 system_signals 中的 score/fused_ev/setup/OB/FVG/Sweep；若为 null 再说明信息不足。"
    if ask_deepseek is not None:
        out = ask_deepseek(ctx, extra_note=extra)
        if out.get("ok"):
            notes = ctx.get("data_notes") or []
            head = ("数据备注: " + "; ".join(notes) + "\n\n") if notes else ""
            return f"✅ DeepSeek ({out.get('latency_ms')}ms)\n\n{head}{out.get('text')}"
        return f"❌ AI 失败: {out.get('error')}"
    result = {"symbol": "BTC/USDT", "features": ctx}
    out = analyze_signal_result(result, extra_note=extra)
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
        scans = load_last_scan_snapshots()
        scan_n = sum(1 for s in SYMBOLS if isinstance(scans.get(s), dict))
        tech_md = ""
        for _sym in SYMBOLS:
            _df = frames.get(_sym)
            _sc = scans.get(_sym) if isinstance(scans.get(_sym), dict) else {}
            if _df is not None and hasattr(_df, "empty") and not _df.empty:
                try:
                    tech_md += tech_analysis_markdown(_sym, _df, _sc) + "\n---\n"
                except Exception as _te:
                    tech_md += f"### {_sym} 技术分析失败: {_te}\n"
        status = (
            f"### 系统快览\n- 刷新: **{now} CST** · 周期 **{timeframe}**\n"
            f"- 持仓 **{len(positions)}** · 近窗 **{stats['n']}** 笔 · "
            f"胜率 **{stats['winrate']:.1f}%** · PF **{stats['pf']}** · 累计 **{stats['sum_r']:+.2f}R**\n"
            f"- 系统扫描快照: **{scan_n}/{len(SYMBOLS)}** 品种（供 AI 使用 score/EV/SMC）\n"
            f"- 自动刷新约 60s\n\n"
            f"{tech_md}"
        )

        _LAST_SNAP = {
            "frames": frames,
            "positions": positions,
            "prices": prices,
            "fundings": fundings,
            "scans": scans,
        }

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
        gr.Markdown("# SMC 交易仪表盘\n自动刷新 · **SMC×SQZMOM 深度分析/连续背离风控** · 多周期 · 盘口 · DeepSeek")
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
        ai_out = gr.Markdown(label="AI 建议", value="点击上方按钮生成分析（长文完整显示）")

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
