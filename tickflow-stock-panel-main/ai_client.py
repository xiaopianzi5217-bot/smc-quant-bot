"""OpenAI-compatible analysis client for the SMC panel."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import requests


class AIClient:
    """Small provider-neutral client; it never places orders."""

    def __init__(self) -> None:
        self.api_key = (
            os.getenv("AI_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("OPENAI_API_KEY", "")
        )
        deepseek_configured = bool(os.getenv("DEEPSEEK_API_KEY")) and not os.getenv("AI_API_BASE")
        self.base_url = (
            os.getenv("AI_API_BASE")
            or ("https://api.deepseek.com/v1" if deepseek_configured else "https://api.openai.com/v1")
        ).rstrip("/")
        self.model = os.getenv("AI_MODEL") or ("deepseek-chat" if deepseek_configured else "gpt-4o-mini")
        self.timeout = int(os.getenv("AI_TIMEOUT_SECONDS", "45"))

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def analyze(self, context: Dict[str, Any], question: str = "") -> str:
        if not self.configured:
            return "AI 未配置。请设置 AI_API_KEY（可选 AI_API_BASE、AI_MODEL）后重试。"

        prompt = (
            "你是量化交易监控助手。只做数据分析和风险提示，不提供确定性收益承诺，"
            "不执行下单。请基于以下 SMC Bot 实时快照，用中文给出：市场状态、信号质量、"
            "主要阻断因素、需要观察的条件。若数据不足请明确说明。\n\n"
            f"用户问题：{question or '请分析当前状态'}\n"
            f"快照：{context}"
        )
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": "你是严格、谨慎的交易系统分析助手。"},
                {"role": "user", "content": prompt},
            ],
        }
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            return str(data["choices"][0]["message"]["content"])
        except requests.RequestException as exc:
            return f"AI 请求失败：{exc}"
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return f"AI 返回格式异常：{exc}"


def ai_settings() -> Dict[str, Optional[str]]:
    client = AIClient()
    return {
        "configured": "是" if client.configured else "否",
        "base_url": client.base_url,
        "model": client.model,
    }
