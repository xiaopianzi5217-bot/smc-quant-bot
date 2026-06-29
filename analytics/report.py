# -*- coding: utf-8 -*-
import json
from pathlib import Path
import pandas as pd


def load_journal(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    if "pnl_r" in df.columns:
        df["pnl_r"] = pd.to_numeric(df["pnl_r"], errors="coerce")
    return df


def closed_trades(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "event" not in df.columns:
        return pd.DataFrame()
    out = df[df["event"] == "CLOSE"].copy()
    if "pnl_r" in out.columns:
        out["pnl_r"] = pd.to_numeric(out["pnl_r"], errors="coerce").fillna(0.0)
    return out


def summarize_closed_trades(closes: pd.DataFrame) -> dict:
    if closes.empty:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "avg_r": 0.0,
            "total_r": 0.0,
            "best_trade_r": 0.0,
            "worst_trade_r": 0.0,
            "profit_factor": 0.0,
        }

    pnl = closes["pnl_r"].fillna(0.0)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    gross_win = float(wins.sum())
    gross_loss = abs(float(losses.sum()))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)

    return {
        "trades": int(len(closes)),
        "win_rate": round(float((pnl > 0).mean()), 4),
        "avg_r": round(float(pnl.mean()), 4),
        "total_r": round(float(pnl.sum()), 4),
        "best_trade_r": round(float(pnl.max()), 4),
        "worst_trade_r": round(float(pnl.min()), 4),
        "profit_factor": round(profit_factor, 4),
    }



def avs_summary_for_trades(closes: pd.DataFrame) -> dict:
    """Attach AVS diagnostics to report output without making analytics depend on trading code."""
    if closes.empty:
        return {}
    try:
        from alpha_validator.avs_engine import AlphaValidationEngine

        return AlphaValidationEngine(closes).run_full_assessment()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

def group_performance(closes: pd.DataFrame, by: str) -> pd.DataFrame:
    if closes.empty or by not in closes.columns:
        return pd.DataFrame(columns=[by, "trades", "win_rate", "avg_r", "total_r"])
    tmp = closes.copy()
    tmp[by] = tmp[by].fillna("UNKNOWN").replace("", "UNKNOWN")
    grouped = tmp.groupby(by)["pnl_r"].agg(
        trades="count",
        win_rate=lambda x: float((x > 0).mean()),
        avg_r="mean",
        total_r="sum",
    ).reset_index()
    grouped["win_rate"] = grouped["win_rate"].round(4)
    grouped["avg_r"] = grouped["avg_r"].round(4)
    grouped["total_r"] = grouped["total_r"].round(4)
    grouped = grouped.sort_values("total_r", ascending=False)
    return grouped


def parse_context_column(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "context_json" not in df.columns:
        return df
    out = df.copy()
    parsed = []
    for raw in out["context_json"].fillna(""):
        try:
            parsed.append(json.loads(raw) if raw else {})
        except Exception:
            parsed.append({})
    for key in ["strategy_name", "pivot_threshold", "min_rr", "macro_trend"]:
        out[key] = [item.get(key, "") for item in parsed]
    return out


def build_markdown_report(journal_path: str) -> str:
    df = load_journal(journal_path)
    if df.empty:
        return "暂无 journal 数据。先运行机器人产生 data/trade_journal.csv，或在页面上传 CSV。"

    df = parse_context_column(df)
    closes = closed_trades(df)
    stats = summarize_closed_trades(closes)

    lines = [
        "# SMC 量化闭环报告",
        "",
        "## 总览",
        f"- 交易次数: **{stats['trades']}**",
        f"- 胜率: **{stats['win_rate'] * 100:.2f}%**",
        f"- 平均 R: **{stats['avg_r']}**",
        f"- 总 R: **{stats['total_r']}**",
        f"- 最佳单笔 R: **{stats['best_trade_r']}**",
        f"- 最差单笔 R: **{stats['worst_trade_r']}**",
        f"- Profit Factor: **{stats['profit_factor']}**",
        "",
    ]

    avs = avs_summary_for_trades(closes)
    if avs:
        lines.extend([
            "## Alpha真实性 AVS",
            f"- AVS Score: **{avs.get('avs_score', 0.0)}**",
            f"- Overfit Score: **{avs.get('overfit_score', 0.0)}**",
            f"- Verdict: **{avs.get('verdict', 'UNKNOWN')}**",
            f"- True Edge Regimes: `{[x.get('regime') for x in avs.get('true_edge_regimes', [])]}`",
            f"- Fake Clusters: `{[x.get('cluster') for x in avs.get('fake_clusters', [])[:10]]}`",
            "",
        ])

    for col, title in [
        ("regime", "按市场状态 Regime"),
        ("volatility", "按波动率 Volatility"),
        ("squeeze", "按挤压状态 Squeeze"),
        ("direction", "按方向 Direction"),
        ("strategy_name", "按策略类型 Strategy"),
    ]:
        perf = group_performance(closes, col)
        if perf.empty:
            continue
        lines.append(f"## {title}")
        lines.append(perf.to_markdown(index=False))
        lines.append("")

    if not closes.empty:
        lines.extend([
            "## 初步结论",
            "- `total_r` 高的分组，代表该状态更适合当前策略。",
            "- `avg_r` 为负且交易次数较多的分组，应该降低权重或增加过滤。",
            "- `win_rate` 高但 `avg_r` 低，可能是盈亏比不足。",
            "- `profit_factor` 和 `total_r` 同时优秀的状态，优先保留。",
        ])

    return "\n".join(lines)
