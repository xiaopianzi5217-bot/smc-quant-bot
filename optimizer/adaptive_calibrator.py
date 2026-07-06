# -*- coding: utf-8 -*-
"""
自适应参数调优器
从历史交易结果学习最优的 score_threshold 和 min_rr

工作原理：
1. 读取 feature_store 中已平仓的交易记录
2. 按不同 threshold / min_rr 组合模拟过滤
3. 找到 Sharpe 比率最高的参数组合
4. 写入 JSON 配置文件
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


# =============================================================
# 配置路径
# =============================================================
FEATURE_STORE_PATH = Path("data/features/trades_features.csv")
CONFIG_PATH = Path("config/v11_full_config.json")
STATE_PATH = Path("state/adaptive_params.json")


# =============================================================
# 1. 读取历史交易
# =============================================================
def load_trades(csv_path: Optional[Path] = None) -> pd.DataFrame:
    """从 feature_store 加载已平仓交易"""
    p = csv_path or FEATURE_STORE_PATH
    if not p.exists():
        print(f"[自适应] 未找到交易记录: {p}")
        return pd.DataFrame()
    
    df = pd.read_csv(p, encoding="utf-8")
    if df.empty:
        return df
    
    # 只保留已平仓交易 (exit_reason != OPEN)
    if "exit_reason" in df.columns:
        df = df[df["exit_reason"] != "OPEN"].copy()
    
    # 只保留有盈亏的交易
    if "pnl_r" in df.columns:
        df = df[df["pnl_r"].notna() & (df["pnl_r"] != 0)].copy()
    
    print(f"[自适应] 加载 {len(df)} 笔已平仓交易")
    return df


# =============================================================
# 2. 参数适应
# =============================================================
def win_rate(series: pd.Series) -> float:
    """胜率"""
    if len(series) == 0:
        return 0.0
    return float((series > 0).sum() / len(series))


def avg_r(series: pd.Series) -> float:
    """平均 R 值"""
    if len(series) == 0:
        return 0.0
    return float(series.mean())


def sharpe_ratio(series: pd.Series) -> float:
    """R 值的 Sharpe 比率（年化）"""
    if len(series) < 3:
        return 0.0
    mean_r = float(series.mean())
    std_r = float(series.std())
    if std_r == 0 or math.isnan(std_r):
        return 0.0
    # 假设每笔交易平均耗时 4 小时 → 一年约 2190 笔
    return (mean_r / std_r) * math.sqrt(2190)


def calc_score(df: pd.DataFrame) -> Dict[str, float]:
    """计算一组交易的综合得分"""
    if df.empty or len(df) < 3:
        return {"sharpe": 0.0, "win_rate": 0.0, "avg_r": 0.0, "n": 0}
    
    rr_vals = df["pnl_r"].values
    sr = sharpe_ratio(df["pnl_r"])
    wr = win_rate(df["pnl_r"])
    avg = avg_r(df["pnl_r"])
    
    return {
        "sharpe": round(sr, 4),
        "win_rate": round(wr, 4),
        "avg_r": round(avg, 4),
        "n": len(df),
    }


# =============================================================
# 3. 核心：自适应调参
# =============================================================
def auto_calibrate(
    df: pd.DataFrame,
    current_threshold: float = 20.0,
    current_min_rr: float = 1.35,
    min_trades: int = 10,
) -> Dict[str, Any]:
    """
    从历史交易记录中找到最优的 threshold 和 min_rr
    
    参数:
        df: 历史交易 DataFrame
        current_threshold: 当前 score 阈值
        current_min_rr: 当前最小 RR
        min_trades: 最少交易笔数（不足时不调整）
    
    返回:
        {"threshold": float, "min_rr": float, "current_score": {}, "optimized_score": {}}
    """
    if df.empty:
        return {
            "threshold": current_threshold,
            "min_rr": current_min_rr,
            "note": "NO_DATA",
            "current_score": {"sharpe": 0, "win_rate": 0, "avg_r": 0, "n": 0},
            "optimized_score": {"sharpe": 0, "win_rate": 0, "avg_r": 0, "n": 0},
        }
    
    # 需要 score 和 pnl_r 字段
    if "score" not in df.columns or "pnl_r" not in df.columns:
        print(f"[自适应] 缺少 score/pnl_r 字段，无法校准")
        return {
            "threshold": current_threshold,
            "min_rr": current_min_rr,
            "note": "MISSING_FIELDS",
            "current_score": calc_score(df),
            "optimized_score": calc_score(df),
        }
    
    # 当前参数的得分
    current_mask = (df["score"] >= current_threshold)
    if current_min_rr > 0 and "rr" in df.columns:
        current_mask = current_mask & (df["rr"] >= current_min_rr)
    current_df = df[current_mask]
    current_result = calc_score(current_df)
    
    if len(df) < min_trades:
        print(f"[自适应] 样本不足 ({len(df)} < {min_trades})，使用当前参数")
        return {
            "threshold": current_threshold,
            "min_rr": current_min_rr,
            "note": f"INSUFFICIENT_SAMPLES_{len(df)}",
            "current_score": current_result,
            "optimized_score": current_result,
        }
    
    # 网格搜索
    best_sharpe = -999.0
    best_params = {"threshold": current_threshold, "min_rr": current_min_rr}
    best_result = current_result
    
    threshold_range = [5, 10, 15, 20, 25, 30, 35, 40, 50]
    rr_range = [0.8, 1.0, 1.2, 1.35, 1.5, 1.8, 2.0]
    
    for th in threshold_range:
        for rr_val in rr_range:
            mask = (df["score"] >= th)
            if "rr" in df.columns:
                mask = mask & (df["rr"] >= rr_val)
            
            subset = df[mask]
            if len(subset) < 3:
                continue
            
            result = calc_score(subset)
            if result["sharpe"] > best_sharpe:
                best_sharpe = result["sharpe"]
                best_params = {"threshold": th, "min_rr": rr_val}
                best_result = result
    
    print(f"[自适应] 当前: threshold={current_threshold} min_rr={current_min_rr} "
          f"Sharpe={current_result['sharpe']} n={current_result['n']}")
    print(f"[自适应] 最优: threshold={best_params['threshold']} min_rr={best_params['min_rr']} "
          f"Sharpe={best_result['sharpe']} n={best_result['n']}")
    
    return {
        "threshold": best_params["threshold"],
        "min_rr": best_params["min_rr"],
        "note": "CALIBRATED",
        "current_score": current_result,
        "optimized_score": best_result,
        "improvement": {
            "sharpe_delta": round(best_result["sharpe"] - current_result["sharpe"], 4),
            "win_rate_delta": round(best_result["win_rate"] - current_result["win_rate"], 4),
            "avg_r_delta": round(best_result["avg_r"] - current_result["avg_r"], 4),
        },
    }


# =============================================================
# 4. 应用到配置
# =============================================================
def apply_params(params: Dict[str, Any], config_path: Optional[Path] = None) -> bool:
    """将最优参数写入配置文件"""
    p = config_path or CONFIG_PATH
    if not p.exists():
        print(f"[自适应] 配置文件不存在: {p}")
        return False
    
    try:
        with open(p, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        
        # 更新 score_base_threshold
        if "strategy_params" not in cfg:
            cfg["strategy_params"] = {}
        cfg["strategy_params"]["score_base_threshold"] = params["threshold"]
        
        # 更新 min_rr
        if "risk" not in cfg:
            cfg["risk"] = {}
        cfg["risk"]["min_rr"] = params["min_rr"]
        
        # 保存配置
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        
        # 保存状态快照
        state = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "params": params,
        }
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        
        print(f"[自适应] 参数已更新: threshold={params['threshold']}, min_rr={params['min_rr']}")
        return True
    
    except Exception as e:
        print(f"[自适应] 参数写入失败: {e}")
        return False


# =============================================================
# 5. 一键运行
# =============================================================
def run_auto_calibrate(
    trades_csv: Optional[str] = None,
    config_path: Optional[str] = None,
    apply: bool = True,
) -> Dict[str, Any]:
    """
    一键运行自适应调参
    
    参数:
        trades_csv: 自定义交易 CSV 路径
        config_path: 自定义配置 JSON 路径
        apply: 是否将最优参数写入配置
    
    返回:
        调参结果字典
    """
    csv_p = Path(trades_csv) if trades_csv else FEATURE_STORE_PATH
    cfg_p = Path(config_path) if config_path else CONFIG_PATH
    
    # 读取当前配置
    try:
        with open(cfg_p, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    
    current_threshold = cfg.get("strategy_params", {}).get("score_base_threshold", 20.0)
    current_min_rr = cfg.get("risk", {}).get("min_rr", 1.35)
    
    # 加载交易数据
    df = load_trades(csv_p)
    
    # 调参
    result = auto_calibrate(df, current_threshold, current_min_rr)
    
    # 应用
    if apply and result["note"] == "CALIBRATED":
        apply_params(result, cfg_p)
    
    return result


# =============================================================
# 主入口
# =============================================================
if __name__ == "__main__":
    result = run_auto_calibrate()
    print(f"\n=== 自适应调参结果 ===")
    print(f"score_threshold: {result['current_score']['n']}笔 → {result['optimized_score']['n']}笔")
    print(f"当前: th={result.get('threshold','?')} min_rr={result.get('min_rr','?')}")
    if "improvement" in result:
        imp = result["improvement"]
        print(f"提升: Sharpe {imp['sharpe_delta']:+.4f} | WinRate {imp['win_rate_delta']:+.4f} | AvgR {imp['avg_r_delta']:+.4f}")
