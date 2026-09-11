# -*- coding: utf-8 -*-
"""ai_advisor.py — DeepSeek 辅助入场分析（仅建议，不自动开仓）

Hugging Face Spaces:
  Settings → Repository secrets / Variables 中设置:
    DEEPSEEK_API_KEY=你的密钥
    DEEPSEEK_BASE_URL=https://api.deepseek.com   # 可选
    DEEPSEEK_MODEL=deepseek-chat                 # 可选

接入位置建议:
  1) Gradio「AI 入场顾问」Tab：手动点分析（推荐）
  2) scan_and_decide 之后：把 result 快照送给本模块，推送文字建议到微信/TG
  禁止: 把 AI 输出直接接到 create_order / check_and_open（避免幻觉下单）
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

try:
    from utils.structured_logger import slog
except Exception:  # pragma: no cover
    class _L:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
    slog = _L()


SYSTEM_PROMPT = """你是资深加密货币 SMC（Smart Money Concepts）交易顾问。
你的任务是根据系统给出的结构化快照，辅助交易者做「手动下单」决策。

硬性规则:
1. 只给分析与建议，不要声称已下单，不要编造未提供的价格/指标。
2. 快照含 markets（行情）与 system_signals（实盘扫描的 score/fused_ev/setup/OB/FVG/Sweep）。
3. 优先使用 system_signals；仅当某字段为 null 时写「信息不足」，有字段时必须引用具体数值。
4. 必须同时考虑: HTF、SMC、SQZMOM、RSI、量能、系统分数与 EV。
5. 若 1H 与入场方向冲突，标注「逆势」并更严或观望。
6. 输出简体中文：偏向(做多/做空/观望)、入场区、止损、TP1/TP2、信心1-10、风险。
"""


def _api_key() -> str:
    return (os.getenv("DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_KEY") or "").strip()


def _base_url() -> str:
    return (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")


def _model() -> str:
    return os.getenv("DEEPSEEK_MODEL") or "deepseek-chat"


def build_context_from_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """从 scan_and_decide / V56 结果提取给模型的精简上下文。"""
    if not result:
        return {}
    sqz = result.get("sqz_data") or {}
    htf = result.get("htf_state") or {}
    feats = result.get("features") or {}
    return {
        "symbol": result.get("symbol"),
        "direction": result.get("direction"),
        "setup_type": result.get("setup_type") or result.get("setup"),
        "score": result.get("final_score", result.get("score")),
        "orig_score": result.get("orig_score"),
        "expected_value": result.get("expected_value") or result.get("fused_ev"),
        "feedback_ev": result.get("_feedback_ev"),
        "fused_ev": result.get("fused_ev") or (result.get("_fusion") or {}).get("fused_ev") or (result.get("_fusion_result") or {}).get("fused_ev"),
        "confidence": result.get("confidence"),
        "entry": result.get("entry"),
        "sl": result.get("sl") or result.get("current_sl"),
        "tp1": result.get("tp1"),
        "tp2": result.get("tp2"),
        "tp3": result.get("tp3"),
        "atr": result.get("atr"),
        "rsi": result.get("rsi"),
        "adx": result.get("adx"),
        "volume_ratio": result.get("volume_ratio") or sqz.get("vol_ratio"),
        "regime": result.get("regime") or htf.get("regime"),
        "htf_blocked": result.get("htf_blocked"),
        "htf_allow_long": htf.get("allow_long"),
        "htf_allow_short": htf.get("allow_short"),
        "sqz_released": sqz.get("released"),
        "sqz_duration": sqz.get("duration"),
        "sqz_strength": sqz.get("strength"),
        "sqz_vol_ratio": sqz.get("vol_ratio"),
        "features": feats if isinstance(feats, dict) else str(feats),
        "action_route": result.get("action_route"),
        "v6_level": result.get("v6_level"),
        "rejected": result.get("rejected"),
    }


def format_user_prompt(ctx: Dict[str, Any], extra_note: str = "") -> str:
    return (
        "以下是交易系统当前快照（JSON）。请据此分析最佳手动入场方案。\n"
        "若数据不足请明确说「信息不足」，不要编造。\n\n"
        f"```json\n{json.dumps(ctx, ensure_ascii=False, indent=2, default=str)}\n```\n\n"
        f"交易者补充: {extra_note or '无'}\n\n"
        "请按下列结构回答:\n"
        "1) 市场结构与 HTF 结论\n"
        "2) SMC / SQZMOM / RSI / 量能要点\n"
        "3) 系统分数与 EV 是否可信\n"
        "4) 最终建议: 做多 / 做空 / 观望（三选一）\n"
        "5) 若可做: 入场区、止损、TP1/TP2、仓位建议(相对风险%)、信心分\n"
        "6) 主要失效条件与风险\n"
    )


def ask_deepseek(
    ctx: Dict[str, Any],
    extra_note: str = "",
    timeout: int = 60,
) -> Dict[str, Any]:
    """调用 DeepSeek Chat，返回 {ok, text, error, latency_ms}。"""
    key = _api_key()
    if not key:
        return {
            "ok": False,
            "text": "",
            "error": "未配置 DEEPSEEK_API_KEY（请在 HF Spaces Secrets 中设置，不要写进代码）",
            "latency_ms": 0,
        }
    if requests is None:
        return {"ok": False, "text": "", "error": "缺少 requests 库", "latency_ms": 0}

    url = f"{_base_url()}/v1/chat/completions"
    payload = {
        "model": _model(),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": format_user_prompt(ctx, extra_note)},
        ],
        "temperature": 0.3,
        "max_tokens": 1200,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    t0 = time.time()
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        latency = int((time.time() - t0) * 1000)
        if resp.status_code != 200:
            return {
                "ok": False,
                "text": "",
                "error": f"HTTP {resp.status_code}: {resp.text[:400]}",
                "latency_ms": latency,
            }
        data = resp.json()
        text = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        return {"ok": True, "text": text, "error": "", "latency_ms": latency, "raw": data}
    except Exception as e:
        return {
            "ok": False,
            "text": "",
            "error": str(e),
            "latency_ms": int((time.time() - t0) * 1000),
        }


def analyze_signal_result(result: Dict[str, Any], extra_note: str = "") -> Dict[str, Any]:
    ctx = build_context_from_result(result or {})
    out = ask_deepseek(ctx, extra_note=extra_note)
    out["context"] = ctx
    if out.get("ok"):
        slog.info(f"[AIAdvisor] {ctx.get('symbol')} ok latency={out.get('latency_ms')}ms")
    else:
        slog.warning(f"[AIAdvisor] fail: {out.get('error')}")
    return out


def analyze_symbol_quick(
    symbol: str,
    direction: str = "",
    score: float = 0,
    ev: float = 0,
    regime: str = "",
    setup: str = "",
    entry: float = 0,
    sl: float = 0,
    tp1: float = 0,
    rsi: float = 0,
    adx: float = 0,
    vol_ratio: float = 0,
    note: str = "",
) -> str:
    """供 Gradio 简单表单调用，返回纯文本。"""
    ctx = {
        "symbol": symbol,
        "direction": direction,
        "setup_type": setup,
        "score": score,
        "expected_value": ev,
        "regime": regime,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "rsi": rsi,
        "adx": adx,
        "volume_ratio": vol_ratio,
    }
    out = ask_deepseek(ctx, extra_note=note)
    if not out["ok"]:
        return f"❌ AI 分析失败: {out['error']}"
    return f"✅ DeepSeek 分析 ({out['latency_ms']}ms)\n\n{out['text']}"
