from __future__ import annotations
import os, requests
from typing import Any, Dict
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TG_BOT_TOKEN") or ""
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TG_CHAT_ID") or ""
TG_API_BASE = os.getenv("TG_API_BASE", "https://api.telegram.org")

def send_telegram(message: str) -> bool:
    if not TOKEN or not CHAT_ID:
        print("[TG] TELEGRAM_BOT_TOKEN/TG_BOT_TOKEN or TELEGRAM_CHAT_ID/TG_CHAT_ID missing"); return False
    for base in dict.fromkeys([str(TG_API_BASE).rstrip("/"), "https://api.telegram.org"]):
        try:
            resp = requests.post(f"{base}/bot{TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": str(message), "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=8)
            if resp.status_code == 200 and resp.json().get("ok"): return True
            print(f"[TG] send failed: {resp.status_code} {resp.text[:200]}")
        except Exception as exc: print(f"[TG] send error via {base}: {exc}")
    return False

def safe_send_telegram(message: str) -> bool:
    try: return send_telegram(message)
    except Exception as exc: print(f"[TG] safe_send_telegram error: {exc}"); return False

def notify_trade_decision(symbol: str, direction: str, decision: Dict[str, Any]) -> bool:
    msg = f"ðŸ“Œ <b>Trade Decision</b>\nSymbol: {symbol}\nDirection: {direction}\nAllow: {decision.get('allow')}\nScore: {decision.get('score')}\nModel: {decision.get('model')}\nReason: {decision.get('reason')}\nDivState: {decision.get('div_state')}\nChanPos: {decision.get('chan_pos')}\n1H Regime: {decision.get('regime_1h')}"
    return safe_send_telegram(msg)