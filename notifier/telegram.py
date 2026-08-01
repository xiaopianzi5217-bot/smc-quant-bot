# -*- coding: utf-8 -*-
import os
import requests
from pathlib import Path
from utils.time_utils import now_bj_str
from utils.structured_logger import slog

# 自动加载 .env 文件（如果存在）
_env_path = Path(__file__).resolve().parents[1] / ".env"
if _env_path.exists():
    with open(_env_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _val = _line.split("=", 1)
            _key = _key.strip()
            _val = _val.strip().strip("\"'")
            if _key and not os.environ.get(_key):
                os.environ[_key] = _val


def _env(*names, default=""):
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return default


def _telegram_config():
    token = _env("TG_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
    chat_id = _env("TG_CHAT_ID", "TELEGRAM_CHAT_ID")
    api_base = _env(
        "TG_API_BASE",
        "TELEGRAM_API_BASE",
        default="https://lingering-breeze-e789.xiaopianzi5217.workers.dev",
    ).rstrip("/")
    return token, chat_id, api_base


def _resolve_wechat_token(token_file: Path | None = None) -> str:
    """兼容旧版 PUSHPLUS_TOKEN 和部署常见的 WX_BOT_KEY。"""
    file_path = token_file or Path(__file__).resolve().parents[1] / "config" / "pushplus_token.txt"
    token = ""
    if file_path.exists():
        try:
            token = file_path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    if not token:
        token = _env("PUSHPLUS_TOKEN", "WX_BOT_KEY", "WX_TOKEN", default="")
    if not token:
        token = _env("TG_BOT_TOKEN", default="")
    return token.strip().strip("\"'")


def _post(url, data, timeout=30):
    return requests.post(
        url,
        data=data,
        timeout=(10, timeout),
        headers={"User-Agent": "smc-quant-bot/1.0"},
    )


def _get(url, timeout=30):
    return requests.get(
        url,
        timeout=(10, timeout),
        headers={"User-Agent": "smc-quant-bot/1.0"},
    )


def send_telegram(message: str) -> str:
    # --- 微信双发代码开始 (已扁平化防手机缩进报错) ---
    wechat_token = _resolve_wechat_token()
    if wechat_token:
        try:
            resp = requests.post(
                "https://www.pushplus.plus/send",
                data={"token": wechat_token, "title": "SMC量化通知", "content": str(message), "template": "html"},
                timeout=(5, 10),
            )
            print("[DEBUG] 微信推送:", resp.text)
        except Exception as e:
            print(f"[DEBUG] 微信异常: {e}")
    else:
        slog.warning("[DEBUG] 微信跳过: 未找到 PushPlus Token")
    # --- 微信双发代码结束 ---

    token, chat_id, api_base = _telegram_config()
    if not token or not chat_id:
        return "Telegram 未配置：缺少 TG_BOT_TOKEN/TG_CHAT_ID 或 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID"

    if message is None or str(message).strip() == "":
        return "Telegram 未发送：消息为空，已阻止发送 None"

    text = f"🕒 北京时间 {now_bj_str()}\n\n{str(message)}"
    payload = {
        "chat_id": chat_id,
        "text": text[:3900],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    bases = [api_base]
    official = "https://api.telegram.org"
    if api_base != official:
        bases.append(official)

    errors = []
    for base in bases:
        url = f"{base}/bot{token}/sendMessage"
        try:
            resp = _post(url, data=payload, timeout=30)
        except requests.exceptions.ConnectTimeout as e:
            errors.append(f"{base} 连接超时：{e}")
            continue
        except requests.exceptions.ReadTimeout as e:
            errors.append(f"{base} 读取超时：{e}")
            continue
        except requests.exceptions.SSLError as e:
            errors.append(f"{base} SSL/443 失败：{e}")
            continue
        except requests.exceptions.RequestException as e:
            errors.append(f"{base} 请求失败：{e}")
            continue

        if resp.status_code != 200:
            errors.append(f"{base} HTTP {resp.status_code}｜{resp.text[:500]}")
            continue

        try:
            js = resp.json()
            if js.get("ok") is False:
                errors.append(f"{base} Telegram API 返回 ok=false｜{js}")
                continue
        except Exception:
            pass
        return "Telegram 已发送"

    return "Telegram 发送失败：" + "；".join(errors)


def test_telegram() -> str:
    # --- 微信测试代码开始 (已扁平化防手机缩进报错) ---
    wechat_token = _resolve_wechat_token()
    if wechat_token:
        try:
            resp = requests.post(
                "https://www.pushplus.plus/send",
                data={"token": wechat_token, "title": "SMC系统测试", "content": "测试联通成功！微信与Telegram均已激活。", "template": "html"},
                timeout=(5, 10),
            )
            print("[DEBUG] 微信测试:", resp.text)
        except Exception as e:
            print(f"[DEBUG] 微信测试异常: {e}")
    else:
        slog.warning("[DEBUG] 微信跳过: 未找到 PushPlus Token")
    # --- 微信测试代码结束 ---

    token, chat_id, api_base = _telegram_config()
    if not token or not chat_id:
        return "Telegram 未配置：缺少 TG_BOT_TOKEN/TG_CHAT_ID 或 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID"

    bases = [api_base]
    official = "https://api.telegram.org"
    if api_base != official:
        bases.append(official)
    errors = []
    for base in bases:
        try:
            me = _get(f"{base}/bot{token}/getMe", timeout=30)
            if me.status_code == 200:
                return send_telegram("SMC Telegram 测试成功")
            errors.append(f"{base} getMe HTTP {me.status_code}｜{me.text[:500]}")
        except requests.exceptions.RequestException as e:
            errors.append(f"{base} getMe 请求失败：{e}")
    return "Telegram getMe 失败：" + "；".join(errors)
