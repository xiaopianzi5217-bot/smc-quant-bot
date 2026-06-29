# -*- coding: utf-8 -*-
"""dashboard.py - SMC Bot 交易仪表盘 V38.5

用法：
  streamlit run dashboard.py

依赖：
  pip install streamlit pandas plotly
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 path 中
_root = Path(__file__).parent.absolute()
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime

from feature_store import feature_store

# ====================== 页面配置 ======================
st.set_page_config(
    page_title="SMC Bot 仪表盘",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🚀 SMC Bot 交易仪表盘 V38.5")
st.caption(f"数据更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# ====================== 加载数据 ======================
df = feature_store.load_history()

if len(df) == 0:
    st.warning("暂无交易数据，请先运行机器人")
    st.stop()

# 类型转换
df["pnl_r"] = pd.to_numeric(df["pnl_r"], errors="coerce")
df["ev"] = pd.to_numeric(df["ev"], errors="coerce")
df["mfe"] = pd.to_numeric(df["mfe"], errors="coerce").fillna(0)
df["mae"] = pd.to_numeric(df["mae"], errors="coerce").fillna(0)
df["max_r"] = pd.to_numeric(df["max_r"], errors="coerce").fillna(0)

# 区分已平仓和持仓中
closed = df[df["exit_reason"].isin(["SL", "TP1", "TP2", "TP3", "TRAIL"])].copy()
open_trades = df[df["exit_reason"] == "OPEN"].copy()

# ====================== 侧边栏 ======================
st.sidebar.header("📊 整体统计")

total = len(df)
closed_count = len(closed)
win_rate = (closed["pnl_r"] > 0).mean() if len(closed) > 0 else 0

# Profit Factor
win_sum = closed[closed["pnl_r"] > 0]["pnl_r"].sum()
loss_sum = closed[closed["pnl_r"] < 0]["pnl_r"].sum()
pf = win_sum / abs(loss_sum) if loss_sum != 0 else float("inf")

avg_ev = df["ev"].mean()
avg_realized = closed["pnl_r"].mean() if len(closed) > 0 else 0
ev_bias = avg_realized - avg_ev

col_s1, col_s2 = st.sidebar.columns(2)
col_s1.metric("总交易", total)
col_s2.metric("已平仓", closed_count)
col_s3, col_s4 = st.sidebar.columns(2)
col_s3.metric("胜率", f"{win_rate:.1%}")
col_s4.metric("Profit Factor", f"{pf:.2f}" if pf != float("inf") else "∞")

st.sidebar.divider()
st.sidebar.metric("📉 最大回撤 (R)", f"{closed['pnl_r'].min():.2f}" if len(closed) > 0 else "N/A")
st.sidebar.metric("📈 最大收益 (R)", f"{closed['pnl_r'].max():.2f}" if len(closed) > 0 else "N/A")

# ====================== EV 准确性分析 ======================
st.header("📊 EV 准确性分析（规则EV vs 真实表现）")
c1, c2, c3, c4 = st.columns(4)
c1.metric("平均规则 EV", f"{avg_ev:.4f}")
c2.metric("平均真实 PnL (R)", f"{avg_realized:.4f}")
c3.metric("EV 偏差", f"{ev_bias:+.4f}", delta=ev_bias)
c4.metric("EV 校准建议",
          "EV偏高" if ev_bias < -0.1 else ("EV偏低" if ev_bias > 0.1 else "校准良好"),
          delta_color="off" if abs(ev_bias) < 0.1 else "inverse")

# EV 散点图
fig_ev = px.scatter(
    closed, x="ev", y="pnl_r",
    color="regime",
    hover_data=["symbol", "direction", "exit_reason"],
    title="规则EV vs 真实PnL（按 Regime 着色）",
    labels={"ev": "规则 EV", "pnl_r": "真实 PnL (R)"},
    trendline="ols",
)
st.plotly_chart(fig_ev, use_container_width=True)

# ====================== 三个 Tab ======================
tab1, tab2, tab3, tab4 = st.tabs(["📈 绩效分布", "🎯 Regime 分析", "📋 交易记录", "⚙️ EV 统计"])

# ---- Tab1: 绩效分布 ----
with tab1:
    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("PnL R 分布")
        fig = px.histogram(
            closed, x="pnl_r", nbins=30,
            color="exit_reason",
            title="已平仓交易 PnL 分布",
            labels={"pnl_r": "利润 (R)", "count": "交易次数"},
        )
        fig.add_vline(x=0, line_color="red", line_dash="dash")
        st.plotly_chart(fig, use_container_width=True)

    with col_b:
        st.subheader("MFE vs MAE")
        fig2 = px.scatter(
            closed, x="mfe", y="mae",
            color="exit_reason",
            hover_data=["symbol", "pnl_r"],
            title="最大有利波动 (MFE) vs 最大不利波动 (MAE)",
            labels={"mfe": "MFE (R)", "mae": "MAE (R)"},
        )
        fig2.add_vline(x=0, line_color="gray", line_dash="dash")
        fig2.add_hline(y=0, line_color="gray", line_dash="dash")
        st.plotly_chart(fig2, use_container_width=True)

    # 统计表格
    st.subheader("退出方式统计")
    exit_stats = closed.groupby("exit_reason").agg(
        次数=("pnl_r", "count"),
        平均盈亏=("pnl_r", "mean"),
        胜率=("pnl_r", lambda x: (x > 0).mean()),
        总盈亏=("pnl_r", "sum"),
    ).round(4)
    st.dataframe(exit_stats, use_container_width=True)

# ---- Tab2: Regime 分析 ----
with tab2:
    st.subheader("不同 Regime 表现")
    if "regime" in closed.columns:
        regime_cols = ["regime"]
        if "regime2" in closed.columns and closed["regime2"].notna().any():
            regime_cols.append("regime2")

        regime_stats = closed.groupby(regime_cols).agg(
            交易次数=("pnl_r", "count"),
            平均盈亏=("pnl_r", "mean"),
            胜率=("pnl_r", lambda x: (x > 0).mean()),
            平均EV=("ev", "mean"),
            总盈亏=("pnl_r", "sum"),
        ).round(4).sort_values("交易次数", ascending=False)
        st.dataframe(regime_stats, use_container_width=True)

        # Regime 盈亏柱状图
        fig_reg = px.bar(
            regime_stats.reset_index(),
            x="regime", y="平均盈亏",
            color="平均盈亏",
            title="各 Regime 平均盈亏",
            labels={"平均盈亏": "平均 PnL (R)"},
            text_auto=".3f",
        )
        st.plotly_chart(fig_reg, use_container_width=True)
    else:
        st.info("暂无 Regime 数据")

# ---- Tab3: 交易记录 ----
with tab3:
    st.subheader("最近交易记录")
    display_cols = [
        "timestamp", "symbol", "direction", "entry", "sl",
        "ev", "pnl_r", "mfe", "mae", "max_r",
        "exit_reason", "regime", "regime2", "score", "rr",
    ]
    available_cols = [c for c in display_cols if c in df.columns]
    st.dataframe(
        df[available_cols].tail(50).sort_values("timestamp", ascending=False),
        use_container_width=True,
        hide_index=True,
    )

# ---- Tab4: EV 统计 ----
with tab4:
    st.subheader("EV 校准统计")

    # 加载 JSON 统计
    ev_stats_path = Path("data/features/ev_statistics.json")
    if ev_stats_path.exists():
        import json
        with open(ev_stats_path, encoding="utf-8") as f:
            ev_stats = json.load(f)
        cols = st.columns(4)
        cols[0].metric("总交易", ev_stats.get("total_trades", "?"))
        cols[1].metric("已平仓", ev_stats.get("closed_trades", "?"))
        cols[2].metric("全局胜率", f"{ev_stats.get('win_rate', 0):.1%}")
        cols[3].metric("Realized EV", f"{ev_stats.get('realized_ev', 0):.4f}")

        if "by_regime" in ev_stats and ev_stats["by_regime"]:
            st.subheader("分 Regime 表现")
            regime_df = pd.DataFrame([
                {"regime": k, "pnl_r": v}
                for k, v in ev_stats["by_regime"].items()
            ])
            st.dataframe(regime_df, use_container_width=True, hide_index=True)
    else:
        st.info("EV 统计尚未生成，请等待机器人运行至少完成几笔交易")
        if st.button("🔄 立即生成统计"):
            feature_store.update_ev_statistics()
            st.rerun()

# ====================== 底部刷新 ======================
st.divider()
if st.button("🔄 刷新数据"):
    st.rerun()