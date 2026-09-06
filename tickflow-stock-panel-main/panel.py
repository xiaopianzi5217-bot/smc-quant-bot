"""Rich SMC Bot monitoring panel inspired by tickflow-stock-panel-main.

Run with: streamlit run tickflow-stock-panel-main/panel.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_client import AIClient, ai_settings

st.set_page_config(page_title="SMC Quant Command Center", page_icon="◈", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Space+Grotesk:wght@400;500;600;700&display=swap');
:root { --ink:#f5f7fb; --panel:#ffffff; --panel2:#f0f3f8; --line:#d8dee9; --text:#172033; --muted:#667085; --blue:#2563eb; --orange:#c47a08; --green:#0c9f70; --red:#d83a4a; }
html, body, [class*="css"] { font-family:'Space Grotesk', sans-serif; }
body, .stApp { background:var(--ink); color:var(--text); }
.block-container { max-width:1600px; padding:1.35rem 2rem 3rem; }
[data-testid="stSidebar"] { background:#ffffff; border-right:1px solid var(--line); }
[data-testid="stSidebar"] > div:first-child { padding-top:1.2rem; }
[data-testid="stMetric"] { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px 16px; }
[data-testid="stMetricLabel"] { color:var(--muted); font-size:.78rem; }
[data-testid="stMetricValue"] { color:var(--text); font-family:'DM Mono', monospace; font-size:1.45rem; }
.hero { display:flex; align-items:center; justify-content:space-between; background:linear-gradient(110deg,#ffffff,#eef4ff); border:1px solid var(--line); border-radius:9px; padding:18px 22px; margin-bottom:16px; }
.hero h1 { margin:0; font-size:1.65rem; letter-spacing:.01em; }
.hero p { color:var(--muted); margin:5px 0 0; font-size:.85rem; }
.live { color:var(--green); font-family:'DM Mono', monospace; font-size:.78rem; border:1px solid #1d6f58; border-radius:99px; padding:7px 11px; }
.section { color:var(--text); font-size:1.04rem; font-weight:600; border-left:3px solid var(--blue); padding-left:10px; margin:22px 0 10px; }
.market-card { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:13px 16px; min-height:76px; box-shadow:0 2px 8px rgba(31,45,61,.04); }
.market-card .symbol { color:var(--muted); font-size:.78rem; }
.market-card .direction { color:var(--blue); font-family:'DM Mono', monospace; font-size:1.15rem; font-weight:600; margin-top:7px; }
.market-card .meta { color:var(--muted); font-family:'DM Mono', monospace; font-size:.72rem; margin-top:5px; }
.rail-title { color:var(--muted); font-family:'DM Mono', monospace; font-size:.68rem; letter-spacing:.16em; margin:18px 0 8px; }
.rail-item { color:#475467; padding:9px 10px; border-radius:6px; margin:2px 0; font-size:.9rem; }
.rail-item.active { color:#1746a2; background:#eaf1ff; border-left:2px solid var(--blue); }
.brand { padding:5px 12px 18px; border-bottom:1px solid var(--line); margin-bottom:16px; }
.brand strong { display:block; font-size:1.25rem; letter-spacing:.04em; }
.brand span { color:var(--blue); font-family:'DM Mono', monospace; font-size:.68rem; letter-spacing:.18em; }
.stCaption, [data-testid="stCaptionContainer"] { color:var(--muted); }
.stDataFrame { border:1px solid var(--line); border-radius:8px; overflow:hidden; }
button[kind="primary"] { background:var(--blue); border-color:var(--blue); }
</style>
""", unsafe_allow_html=True)

DATA_FILE = ROOT / "data" / "features" / "trades_features.csv"
SIGNAL_FILE = ROOT / "logs" / "signal_diary.csv"
AUDIT_FILE = ROOT / "logs" / "reject_audit.jsonl"
OHLCV_FILES = {
    "BTC/USDT": ROOT / "BTCUSDT_15M_365d.csv",
}

AUDIT_TEXT_ZH = {
    "FEEDBACK_LOOP_REJECT": "反馈回路拒绝",
    "DEDUPED_EMPTY": "排重后为空",
    "HISTORY_ONLY": "仅历史记录",
    "UNKNOWN": "未知原因",
    "gate": "风控层",
    "reason": "原因",
    "stage": "阶段",
    "score": "评分",
    "ev": "期望值",
    "regime": "市场环境",
    "vol_state": "波动状态",
    "direction": "方向",
    "setup_type": "形态类型",
    "observer_events": "观察事件",
    "extra": "附加信息",
    "symbol": "品种",
    "signal_id": "信号编号",
    "confidence": "置信度",
    "Long": "做多",
    "Short": "做空",
    "unknown": "未知",
}

SIGNAL_TEXT_ZH = {
    "ts": "时间",
    "symbol": "品种",
    "direction": "方向",
    "approved": "是否通过",
    "state": "状态",
    "long_score": "做多评分",
    "short_score": "做空评分",
    "edge": "优势",
    "long_ev": "做多期望值",
    "short_ev": "做空期望值",
    "price": "现价",
    "sl": "止损",
    "tp1": "第一止盈",
    "rr": "盈亏比",
    "regime": "市场环境",
    "htf_allowed": "高周期允许方向",
    "volume_ratio": "成交量比",
    "adx": "趋势强度",
    "atr_pct": "波动率",
    "squeeze": "挤压状态",
    "has_bot_div": "底部背离",
    "has_top_div": "顶部背离",
    "is_ssl_swept": "下方流动性扫损",
    "is_bsl_swept": "上方流动性扫损",
    "reason": "原因",
    "funding_rate": "资金费率",
    "Long": "做多",
    "Short": "做空",
    "Both": "双向",
    "True": "是",
    "False": "否",
    "TRUE": "是",
    "FALSE": "否",
    "APPROVED": "已通过",
    "HOLD": "等待",
}


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, on_bad_lines="skip")
    except Exception:
        return pd.DataFrame()


def numeric(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = df.copy()
    for column in columns:
        if column not in out:
            out[column] = 0.0
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0.0)
    return out


def load_snapshot() -> Dict[str, Any]:
    trades = numeric(read_csv(DATA_FILE), ["pnl_r", "ev", "score"])
    signals = numeric(read_csv(SIGNAL_FILE), ["long_score", "short_score", "edge", "long_ev", "short_ev", "adx", "volume_ratio"])
    audit: list[dict[str, Any]] = []
    if AUDIT_FILE.exists():
        for line in AUDIT_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-100:]:
            try:
                audit.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {"trades": trades, "signals": signals, "audit": audit}


def latest_by_symbol(signals: pd.DataFrame) -> pd.DataFrame:
    if signals.empty or "symbol" not in signals:
        return signals
    result = signals.copy()
    if "ts" in result:
        result["ts"] = pd.to_datetime(result["ts"], errors="coerce")
        result = result.sort_values("ts")
    return result.groupby("symbol", as_index=False).tail(1)


def load_ohlcv(symbol: str, limit: int = 160) -> pd.DataFrame:
    """Prefer live public candles; use the local file only as a fallback."""
    symbol_code = symbol.replace("/", "").split(":")[0]
    try:
        response = requests.get(
            "https://api.bitget.com/api/v2/mix/market/candles",
            params={"symbol": symbol_code, "productType": "umcbl", "granularity": "15m", "limit": min(limit, 500)},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") == "00000" and payload.get("data"):
            frame = pd.DataFrame(
                payload["data"],
                columns=["ts", "open", "high", "low", "close", "volume", "quote"],
            )
            frame["datetime"] = pd.to_datetime(
                pd.to_numeric(frame["ts"], errors="coerce"), unit="ms", utc=True,
            ).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
            frame = frame.sort_values("datetime").tail(limit).copy()
            return numeric(frame, ["open", "high", "low", "close", "volume"])
    except (requests.RequestException, ValueError, KeyError, TypeError):
        pass

    path = OHLCV_FILES.get(symbol)
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        frame = pd.read_csv(path).tail(limit).copy()
        frame["datetime"] = pd.to_datetime(frame.get("datetime", frame.get("ts")), errors="coerce")
        return numeric(frame, ["open", "high", "low", "close", "volume"])
    except Exception:
        return pd.DataFrame()


def market_insight(frame: pd.DataFrame) -> Dict[str, str]:
    """Return simple, explainable structure notes from the visible candles."""
    if len(frame) < 55:
        return {"趋势": "数据不足", "形态": "等待更多 K 线", "动能": "数据不足", "区间": "数据不足", "建议": "暂不判断"}

    last = frame.iloc[-1]
    previous = frame.iloc[-2]
    close = float(last["close"])
    ema20 = float(last["EMA20"])
    ema50 = float(last["EMA50"])
    body = abs(float(last["close"]) - float(last["open"]))
    candle_range = max(float(last["high"]) - float(last["low"]), 1e-9)
    body_ratio = body / candle_range
    recent = frame.tail(20)
    support = float(recent["low"].min())
    resistance = float(recent["high"].max())
    avg_range = (frame["high"] - frame["low"]).tail(20).mean()
    avg_volume = max(float(frame["volume"].tail(20).mean()), 1e-9)
    volume_ratio = float(last["volume"]) / avg_volume
    delta = frame["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rsi = float((100 - 100 / (1 + gain / (loss + 1e-9))).iloc[-1])
    range_state = "波动放大" if candle_range > avg_range * 1.5 else "波动正常"

    if close > ema20 > ema50:
        trend = "多头排列"
    elif close < ema20 < ema50:
        trend = "空头排列"
    else:
        trend = "均线纠缠"

    upper_wick = float(last["high"]) - max(float(last["open"]), float(last["close"]))
    lower_wick = min(float(last["open"]), float(last["close"])) - float(last["low"])
    if body_ratio < 0.25 and lower_wick > upper_wick * 1.5:
        pattern = "长下影，低位承接"
    elif body_ratio < 0.25 and upper_wick > lower_wick * 1.5:
        pattern = "长上影，上方抛压"
    elif body_ratio < 0.25:
        pattern = "小实体，观望确认"
    elif last["close"] > last["open"] and previous["close"] < previous["open"] and last["close"] >= previous["open"]:
        pattern = "阳线反包，短线转强"
    elif last["close"] < last["open"] and previous["close"] > previous["open"] and last["close"] <= previous["open"]:
        pattern = "阴线反包，短线转弱"
    elif last["close"] > last["open"]:
        pattern = "阳线推进"
    else:
        pattern = "阴线回压"

    momentum = "收盘站上 EMA20" if close > ema20 else "收盘跌破 EMA20"
    prior_high = float(frame["high"].iloc[-21:-1].max())
    prior_low = float(frame["low"].iloc[-21:-1].min())
    if close > prior_high:
        breakout = "向上突破近 20 根高点"
    elif close < prior_low:
        breakout = "向下跌破近 20 根低点"
    else:
        breakout = "仍在近 20 根区间内"

    long_points = int(close > ema20 > ema50) + int(close > prior_high) + int(rsi < 70) + int(volume_ratio > 1.1)
    short_points = int(close < ema20 < ema50) + int(close < prior_low) + int(rsi > 30) + int(volume_ratio > 1.1)
    long_advice = (
        "可做多观察：等待回踩 EMA20 不破，且成交量放大或阳线突破近 20 根高点。"
        if long_points >= 3 else
        "做多暂缓：均线、突破或成交量确认不足。"
    )
    short_advice = (
        "可做空观察：等待反抽 EMA20 受压，且成交量放大或阴线跌破近 20 根低点。"
        if short_points >= 3 else
        "做空暂缓：均线、跌破或成交量确认不足。"
    )
    if long_points >= short_points + 2:
        recommendation = "偏多观察"
    elif short_points >= long_points + 2:
        recommendation = "偏空观察"
    else:
        recommendation = "方向不明确"
    return {
        "趋势": trend,
        "形态": pattern,
        "动能": f"{momentum} · RSI {rsi:.1f}",
        "突破": breakout,
        "成交量": f"量比 {volume_ratio:.2f}",
        "区间": f"支撑 {support:,.2f} · 压力 {resistance:,.2f}",
        "波动": range_state,
        "建议": recommendation,
        "做多建议": long_advice,
        "做空建议": short_advice,
    }


def make_context(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    signals = latest_by_symbol(snapshot["signals"])
    trades = snapshot["trades"]
    return {
        "latest_signals": signals.tail(10).to_dict("records"),
        "trade_count": int(len(trades)),
        "realized_pnl_r": round(float(trades["pnl_r"].sum()), 4) if not trades.empty else 0,
        "audit_tail": snapshot["audit"][-10:],
    }


def signal_explanation(row: pd.Series) -> str:
    direction = str(row.get("direction", "未知方向"))
    score = max(float(row.get("long_score", 0)), float(row.get("short_score", 0)))
    htf = str(row.get("htf_allowed", "未知"))
    state = str(row.get("state", "未知状态"))
    ev = max(float(row.get("long_ev", 0)), float(row.get("short_ev", 0)))
    if state.upper() in {"APPROVED", "OPEN"}:
        conclusion = "信号已通过前置筛选，但仍需经过最终 HTF、EV 和执行风控。"
    else:
        conclusion = "当前没有形成可执行交易，优先查看 HTF、EV 或 Quality Gate 拒绝原因。"
    return f"方向：{direction}；评分：{score:.1f}；HTF 允许方向：{htf}；估计 EV：{ev:+.4f}；状态：{state}。{conclusion}"


def audit_explanation(audit_rows: list[dict[str, Any]]) -> str:
    if not audit_rows:
        return "暂无审计记录，可能是近期没有候选进入该层，或日志尚未刷新。"
    latest_item = audit_rows[-1]
    gate = str(latest_item.get("gate", latest_item.get("reason", "未知")))
    explanations = {
        "FEEDBACK_LOOP_REJECT": "反馈回路拒绝：历史反馈模型认为当前组合信号的风险收益不足。",
        "DEDUPED_EMPTY": "排重后为空：信号已经处理过，系统避免重复发送或重复开单。",
        "HISTORY_ONLY": "历史窗口记录：候选落在回看窗口之外，不参与当前开单。",
    }
    return f"最近审计：{AUDIT_TEXT_ZH.get(gate, gate)}。{explanations.get(gate, '建议展开下方明细，结合评分、期望值、方向和时间判断具体原因。')}"


def translated_audit_frame(audit_rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(audit_rows[-50:]).rename(columns=AUDIT_TEXT_ZH)
    return frame.replace(AUDIT_TEXT_ZH)


def translated_signal_frame(frame: pd.DataFrame) -> pd.DataFrame:
    translated = frame.rename(columns=SIGNAL_TEXT_ZH).replace(SIGNAL_TEXT_ZH)
    if "原因" in translated.columns:
        translated["原因"] = translated["原因"].map(
            lambda value: str(value)
            .replace("score_", "评分 ")
            .replace("edge_", "优势 ")
            .replace("ev_", "期望值 ")
            .replace("_approved", " 已通过")
            .replace("_too_low", " 过低")
            .replace("_below_min", " 低于最低值")
        )
    return translated


snapshot = load_snapshot()
trades = snapshot["trades"]
signals = snapshot["signals"]
latest = latest_by_symbol(signals)
closed = trades[trades.get("exit_reason", pd.Series(index=trades.index)).isin(["SL", "TP1", "TP2", "TP3", "TRAIL", "CLOSE"])]

st.markdown("""
<div class="hero">
    <div><h1>◈ SMC 量化指挥台</h1><p>BTC/USDT · ETH/USDT &nbsp;|&nbsp; 实时策略、风控与 AI 分析工作台</p></div>
    <div class="live">● 引擎运行中</div>
</div>
""", unsafe_allow_html=True)

st.markdown("<div class='rail-title'>市场脉搏</div>", unsafe_allow_html=True)
market_cols = st.columns(max(1, min(4, len(latest))))
if latest.empty:
    market_cols[0].markdown("<div class='market-card'><div class='symbol'>市场状态</div><div class='direction'>等待数据</div><div class='meta'>暂无最新信号</div></div>", unsafe_allow_html=True)
else:
    for card, (_, row) in zip(market_cols, latest.head(4).iterrows()):
        direction = str(row.get("direction", "--"))
        score = max(float(row.get("long_score", 0)), float(row.get("short_score", 0)))
        status = "已通过" if bool(row.get("approved", False)) else str(row.get("state", "等待"))
        card.markdown(
            f"<div class='market-card'><div class='symbol'>{row.get('symbol', '--')} · {status}</div>"
            f"<div class='direction'>{direction} <span style='font-size:.8rem'>评分 {score:.1f}</span></div>"
            f"<div class='meta'>环境 {row.get('regime', '--')} · HTF {row.get('htf_allowed', '--')}</div></div>",
            unsafe_allow_html=True,
        )

with st.sidebar:
    st.markdown("<div class='brand'><strong>SMC 量化</strong><span>交易指挥台</span></div>", unsafe_allow_html=True)
    active_view = st.radio(
        "功能导航",
        ["▦ 总览看板", "⌁ 信号雷达", "◌ 风控审计", "◫ 绩效复盘", "✦ AI 分析"],
        label_visibility="collapsed",
    )
    st.divider()
    st.markdown("### 系统状态")
    st.success("主引擎：运行中")
    st.metric("AI 接口", "已配置" if ai_settings()["configured"] == "是" else "未配置")
    st.caption(f"模型：{ai_settings()['model']}")
    if st.button("刷新数据", use_container_width=True):
        st.rerun()
    st.divider()
    st.markdown("### 观察范围")
    symbols = st.multiselect("交易品种", sorted(latest["symbol"].dropna().unique()) if "symbol" in latest else [], default=None)
    chart_symbol = st.selectbox("K线品种", ["BTC/USDT", "ETH/USDT"])

k1, k2, k3, k4, k5 = st.columns(5)
win_rate = float((closed["pnl_r"] > 0).mean()) if len(closed) else 0.0
avg_ev = float(trades["ev"].mean()) if len(trades) else 0.0
pnl_total = float(trades["pnl_r"].sum()) if len(trades) else 0.0
k1.metric("候选信号", len(signals))
k2.metric("最新品种", len(latest))
k3.metric("已记录交易", len(trades))
k4.metric("胜率", f"{win_rate:.1%}")
k5.metric("累计 PnL (R)", f"{pnl_total:+.2f}")

if symbols and "symbol" in latest:
    latest = latest[latest["symbol"].isin(symbols)]

if active_view == "⌁ 信号雷达":
    st.markdown("<div class='section'>信号雷达 · 最新候选</div>", unsafe_allow_html=True)
    st.info("信号雷达回答‘当前发现了什么’：评分衡量结构强弱，方向表示候选交易方向，HTF 表示 1H 环境是否允许该方向；最终状态仍以执行风控为准。")
    if latest.empty:
        st.info("暂无可展示的最新信号")
    else:
        view_cols = [c for c in ["ts", "symbol", "direction", "approved", "state", "long_score", "short_score", "edge", "regime", "htf_allowed", "adx", "volume_ratio", "reason"] if c in latest]
        st.dataframe(translated_signal_frame(latest[view_cols]), use_container_width=True, hide_index=True)
        with st.expander("展开逐条判断", expanded=True):
            for _, signal_row in latest.iterrows():
                st.markdown(f"**{signal_row.get('symbol', '未知品种')}**：{signal_explanation(signal_row)}")
        chart = latest.melt(id_vars=["symbol"], value_vars=[c for c in ["long_score", "short_score"] if c in latest], var_name="方向", value_name="分数")
        st.plotly_chart(px.bar(chart, x="symbol", y="分数", color="方向", barmode="group", height=380), use_container_width=True)
    st.stop()

if active_view == "◌ 风控审计":
    st.markdown("<div class='section'>风控审计 · 质量门 / 高周期 / 期望值</div>", unsafe_allow_html=True)
    st.info("风控审计回答‘为什么没有执行’：它记录排重、反馈回路、质量门、高周期方向和期望值等阻断层，拒绝并不等于系统故障。")
    if snapshot["audit"]:
        audit_rows = snapshot["audit"]
        reason_counts = Counter(AUDIT_TEXT_ZH.get(str(item.get("gate", item.get("reason", "UNKNOWN"))), "未知原因") for item in audit_rows)
        audit_df = pd.DataFrame([{"原因": k, "次数": v} for k, v in reason_counts.most_common(12)])
        st.plotly_chart(px.bar(audit_df, x="次数", y="原因", orientation="h", height=380), use_container_width=True)
        st.dataframe(translated_audit_frame(audit_rows), use_container_width=True, hide_index=True)
    else:
        st.info("暂无拒绝审计记录")
    st.stop()

if active_view == "◫ 绩效复盘":
    st.markdown("<div class='section'>绩效复盘 · PnL / EV</div>", unsafe_allow_html=True)
    st.info("绩效复盘回答‘过去执行得怎么样’：累计 PnL 看方向，胜率看命中，回撤看风险，样本数量决定结论可信度。")
    if trades.empty:
        st.info("暂无交易记录")
    else:
        chart_df = trades.copy()
        chart_df["累计 PnL (R)"] = chart_df["pnl_r"].cumsum()
        st.plotly_chart(px.line(chart_df, y="累计 PnL (R)", markers=True, height=420), use_container_width=True)
        st.dataframe(trades.tail(100), use_container_width=True, hide_index=True)
    st.stop()

if active_view == "✦ AI 分析":
    st.markdown("<div class='section'>AI 分析 · 只读决策助手</div>", unsafe_allow_html=True)
    st.info("AI 只读取当前快照进行分析，不具备下单权限。它适合解释信号与审计记录，不会绕过 HTF、EV 或风控规则。")
    question = st.text_input("分析问题", placeholder="为什么最近没有开单？当前是否存在 HTF 或 EV 阻断？")
    if st.button("生成 AI 分析", type="primary"):
        with st.spinner("AI 正在分析当前快照..."):
            st.markdown(AIClient().analyze(make_context(snapshot), question))
    st.json(ai_settings())
    st.stop()

st.markdown("<div class='section'>行情结构 · 价格行为</div>", unsafe_allow_html=True)
ohlcv = load_ohlcv(chart_symbol)
if ohlcv.empty:
    st.info(f"暂无 {chart_symbol} 本地 K 线数据")
else:
    ohlcv["EMA20"] = ohlcv["close"].ewm(span=20, adjust=False).mean()
    ohlcv["EMA50"] = ohlcv["close"].ewm(span=50, adjust=False).mean()
    data_age = pd.Timestamp.now() - pd.Timestamp(ohlcv["datetime"].iloc[-1])
    source_label = "实时行情" if data_age.total_seconds() < 3600 else "本地缓存（可能过期）"
    price_chart = go.Figure()
    price_chart.add_trace(go.Candlestick(
        x=ohlcv["datetime"], open=ohlcv["open"], high=ohlcv["high"],
        low=ohlcv["low"], close=ohlcv["close"], name=chart_symbol,
        increasing_line_color="#2dd49a", decreasing_line_color="#ff5c68",
    ))
    price_chart.add_trace(go.Scatter(x=ohlcv["datetime"], y=ohlcv["EMA20"], name="EMA20", line={"color": "#4d8dff", "width": 1.2}))
    price_chart.add_trace(go.Scatter(x=ohlcv["datetime"], y=ohlcv["EMA50"], name="EMA50", line={"color": "#f2a93b", "width": 1.2}))
    price_chart.update_layout(
        height=420, margin={"l": 10, "r": 10, "t": 10, "b": 10},
        paper_bgcolor="#ffffff", plot_bgcolor="#ffffff", font={"color": "#475467"},
        xaxis_rangeslider_visible=False, legend={"orientation": "h", "y": 1.02},
    )
    st.plotly_chart(price_chart, use_container_width=True)
    st.caption(f"数据来源：{source_label} · 最后一根 K 线：{ohlcv['datetime'].iloc[-1].strftime('%Y-%m-%d %H:%M:%S')}")
    insight = market_insight(ohlcv)
    st.markdown("<div class='section'>结构解读 · 形态与关键位</div>", unsafe_allow_html=True)
    insight_cols = st.columns(4)
    for insight_col, (label, value) in zip(insight_cols, list(insight.items())[:4]):
        insight_col.metric(label, value)
    st.markdown(f"**多形态结论：** {insight['突破']}；{insight['成交量']}；{insight['波动']}。")
    st.warning(f"**当前倾向：** {insight['建议']}")
    advice_long, advice_short = st.columns(2)
    advice_long.info(f"**做多建议**\n\n{insight['做多建议']}")
    advice_short.info(f"**做空建议**\n\n{insight['做空建议']}")
    st.caption("以上为可解释的行情结构提示，仅供研究参考；最终是否交易仍由策略、高周期方向、期望值与风控共同决定。")

st.divider()
left, right = st.columns([1.6, 1])
with left:
    st.subheader("最新信号雷达")
    st.caption("这里展示最近一次扫描得到的候选。评分越高代表结构信号越强，但不是开单承诺；HTF、EV 和执行风控仍然拥有否决权。")
    if latest.empty:
        st.info("暂无可展示的最新信号")
    else:
        view_cols = [c for c in ["ts", "symbol", "direction", "approved", "state", "long_score", "short_score", "edge", "regime", "htf_allowed", "adx", "volume_ratio", "reason"] if c in latest]
        st.dataframe(translated_signal_frame(latest[view_cols]), use_container_width=True, hide_index=True)
        with st.expander("查看最新信号逐条解析", expanded=True):
            for _, signal_row in latest.iterrows():
                st.markdown(f"**{signal_row.get('symbol', '未知品种')}**  ")
                st.write(signal_explanation(signal_row))
with right:
    st.subheader("信号强度")
    if not latest.empty:
        chart = latest.melt(id_vars=["symbol"], value_vars=[c for c in ["long_score", "short_score"] if c in latest], var_name="方向", value_name="分数")
        st.plotly_chart(px.bar(chart, x="symbol", y="分数", color="方向", barmode="group", height=290), use_container_width=True)
    else:
        st.info("等待扫描数据")

st.subheader("风控与拒绝审计")
st.caption("审计记录解释系统为什么没有继续进入执行链路。高周期方向相反属于方向保护，期望值或反馈拒绝属于收益风险保护，排重记录则表示系统主动避免重复处理。")
audit_rows = snapshot["audit"]
if audit_rows:
    st.info(audit_explanation(audit_rows))
    reason_counts = Counter(AUDIT_TEXT_ZH.get(str(item.get("gate", item.get("reason", "UNKNOWN"))), "未知原因") for item in audit_rows)
    audit_df = pd.DataFrame([{"原因": k, "次数": v} for k, v in reason_counts.most_common(12)])
    a, b = st.columns([1, 1.3])
    with a:
        st.plotly_chart(px.bar(audit_df, x="次数", y="原因", orientation="h", height=330), use_container_width=True)
    with b:
        st.dataframe(translated_audit_frame(audit_rows), use_container_width=True, hide_index=True)
else:
    st.info("暂无拒绝审计记录")

pnl_tab, ev_tab, ai_tab = st.tabs(["绩效与交易", "EV 诊断", "AI 分析"])
with pnl_tab:
    st.caption("绩效复盘用于检查策略实际结果，不用单笔盈利证明策略有效；重点观察累计 PnL、胜率、退出原因和样本数量。")
    if trades.empty:
        st.info("暂无交易记录")
    else:
        chart_df = trades.copy()
        chart_df["累计 PnL (R)"] = chart_df["pnl_r"].cumsum()
        st.plotly_chart(px.line(chart_df, y="累计 PnL (R)", markers=True, height=330), use_container_width=True)
        st.dataframe(trades.tail(50), use_container_width=True, hide_index=True)
with ev_tab:
    st.caption("EV 是期望值估计，实际结果会受到滑点、成交、样本量和市场状态影响。EV 为正也不代表下一笔必然盈利。")
    e1, e2, e3 = st.columns(3)
    e1.metric("平均 EV", f"{avg_ev:+.4f}")
    e2.metric("已实现 PnL (R)", f"{float(closed['pnl_r'].mean()) if len(closed) else 0:+.4f}")
    e3.metric("EV 样本", len(trades))
    if not trades.empty and "ev" in trades:
        st.plotly_chart(px.scatter(trades, x="ev", y="pnl_r", hover_data=[c for c in ["symbol", "direction", "exit_reason"] if c in trades], height=330), use_container_width=True)
with ai_tab:
    st.caption("AI 会读取当前信号、交易和审计快照，输出解释与风险提示；它没有交易所权限，不会代替 HTF、EV 或风控做决定。")
    st.markdown("AI 只读取当前快照进行分析，不具备下单权限。")
    question = st.text_input("分析问题", placeholder="为什么最近没有开单？当前是否存在 HTF 或 EV 阻断？")
    if st.button("生成 AI 分析", type="primary"):
        with st.spinner("AI 正在分析当前快照..."):
            st.markdown(AIClient().analyze(make_context(snapshot), question))
    st.json(ai_settings())
