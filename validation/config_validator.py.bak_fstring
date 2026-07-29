# -*- coding: utf-8 -*-
"""
config_validator.py — 配置验证器（启动时深度检测）

在 bot 启动时检查配置完整性、类型正确性和值合法性。
避免运行时才暴露配置问题（如缺少字段、类型错误）。

用法：
    from validation.config_validator import validate_config, ConfigReport

    report = validate_config(cfg)
    if not report.valid:
        for err in report.errors:
            print(f"配置错误: {err}")
        sys.exit(1)
    
    for warn in report.warnings:
        print(f"配置警告: {warn}")
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Set, Tuple


# ── sentinel ──────────────────────────────────────────────
class _Missing:
    def __repr__(self):
        return "MISSING"
_MISSING = _Missing()


# ── 验证返回 ──────────────────────────────────────────────
class ConfigReport:
    """配置验证结果"""
    
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.checked_fields: int = 0
        self.extra_info: Dict[str, Any] = {}
    
    @property
    def valid(self) -> bool:
        return len(self.errors) == 0
    
    def __repr__(self) -> str:
        status = "PASS" if self.valid else f"FAIL({len(self.errors)} errors)"
        return f"ConfigReport({status}, {len(self.warnings)} warnings, {self.checked_fields} fields checked)"


# ── 核心验证器 ────────────────────────────────────────────
class ConfigValidator:
    """
    配置验证器
    
    支持：
    - 必需字段检查（must-exist）
    - 类型检查（type）
    - 值范围检查（min/max/in）
    - 依赖字段检查（require）
    - 嵌套字段路径（path）
    """
    
    def __init__(self, config: dict):
        self._cfg = config
        self._report = ConfigReport()
    
    def validate(self) -> ConfigReport:
        """执行全部验证规则"""
        self._check_version()
        self._check_symbols()
        self._check_risk()
        self._check_strategy_params()
        self._check_strategy_filters()
        self._check_execution()
        self._check_smc_quality()
        self._check_telegram()
        self._check_position_sizing()
        return self._report
    
    # ── 辅助方法 ──────────────────────────────────────
    
    def _get(self, path: str, default: Any = _MISSING) -> Any:
        """通过点号路径获取嵌套值"""
        keys = path.split(".")
        val = self._cfg
        for k in keys:
            if not isinstance(val, dict):
                if default is _MISSING:
                    self._report.errors.append(f"路径 '{path}' 中断于 '{k}'：不是字典")
                return default
            if k not in val:
                if default is _MISSING:
                    self._report.errors.append(f"缺少必需字段 '{path}'")
                return default
            val = val[k]
        self._report.checked_fields += 1
        return val
    
    def _check_type(self, path: str, expected_type: type, optional: bool = False):
        val = self._get(path, _MISSING)
        if val is _MISSING:
            if not optional:
                self._report.errors.append(f"字段 '{path}' 类型检查失败：值不存在")
            return
        if not isinstance(val, expected_type):
            self._report.errors.append(
                f"字段 '{path}' 应为 {expected_type.__name__}，实际为 {type(val).__name__} (值={val})"
            )
    
    def _check_range(self, path: str, min_val: float = None, max_val: float = None):
        val = self._get(path, None)
        if val is None:
            return
        if not isinstance(val, (int, float)):
            return
        if min_val is not None and val < min_val:
            self._report.errors.append(f"字段 '{path}' = {val} 小于最小值 {min_val}")
        if max_val is not None and val > max_val:
            self._report.errors.append(f"字段 '{path}' = {val} 大于最大值 {max_val}")
    
    def _check_in(self, path: str, allowed: set):
        val = self._get(path, _MISSING)
        if val is _MISSING:
            return
        if val not in allowed:
            self._report.errors.append(f"字段 '{path}' = '{val}' 不在允许值 {allowed} 中")
    
    def _warn_if(self, condition: bool, msg: str):
        if condition:
            self._report.warnings.append(msg)
    
    # ── 各模块检查 ──────────────────────────────────────
    
    def _check_version(self):
        v = self._get("version", "")
        self._warn_if(not v, "缺少 version 字段")
    
    def _check_symbols(self):
        symbols = self._get("symbols", [])
        if not isinstance(symbols, list) or len(symbols) == 0:
            self._report.errors.append("symbols 必须是非空列表")
            return
        for sym in symbols:
            if not isinstance(sym, str) or "/" not in sym:
                self._report.warnings.append(f"符号 '{sym}' 格式异常（建议格式: BTC/USDT）")
        self._check_type("exec_timeframe", str)
        self._check_type("macro_timeframe", str)
        self._check_in("data_mode", {"live", "sample_data", "backtest"})
    
    def _check_risk(self):
        risk = self._get("risk", {})
        if not isinstance(risk, dict):
            self._report.errors.append("risk 必须是字典")
            return
        for field, expected, lo, hi in [
            ("risk_per_trade", float, 0.001, 0.1),
            ("min_rr", float, 0.5, 10.0),
            ("max_open_positions", int, 1, 20),
            ("max_same_direction_positions", int, 1, 10),
            ("max_daily_loss_r", float, 0.5, 10.0),
            ("max_drawdown_pct", float, 0.01, 0.5),
            ("max_consecutive_losses", int, 1, 20),
            ("leverage", (int, float), 1, 100),
        ]:
            val = risk.get(field)
            if val is None:
                self._report.warnings.append(f"risk.{field} 未设置，使用默认值")
                continue
            if not isinstance(val, expected):
                self._report.warnings.append(f"risk.{field} 类型应为 {expected.__name__}，实际为 {type(val).__name__}")
            if isinstance(val, (int, float)):
                if val < lo:
                    self._report.warnings.append(f"risk.{field} = {val} 小于建议最小值 {lo}")
                if val > hi:
                    self._report.warnings.append(f"risk.{field} = {val} 大于建议最大值 {hi}")
    
    def _check_strategy_params(self):
        sp = self._get("strategy_params", {})
        if not isinstance(sp, dict):
            return
        self._check_range("strategy_params.wvf_std_mult", 0.5, 5.0)
        self._check_range("strategy_params.score_base_threshold", 0, 20)
        self._check_range("strategy_params.rsi_ob", 50, 100)
        self._check_range("strategy_params.rsi_os", 0, 50)
        self._check_range("strategy_params.min_setup_quality", 0, 100)
        self._check_range("strategy_params.strong_setup_quality", 0, 100)
        self._warn_if(
            sp.get("rsi_ob", 70) < sp.get("rsi_os", 30) + 15,
            "rsi_ob 应明显大于 rsi_os（建议差距 >= 20）"
        )
    
    def _check_strategy_filters(self):
        sf = self._get("strategy_filters", {})
        if not isinstance(sf, dict):
            return
        self._check_type("strategy_filters.enabled", bool, optional=True)
        if sf.get("multi_timeframe_trend", {}).get("enabled", False):
            htf = sf.get("multi_timeframe_trend", {})
            self._warn_if(
                htf.get("block_long_when_exec_red") and htf.get("block_short_when_exec_blue"),
                "趋势过滤器同时拦截多空可能无信号可开"
            )
        self._check_range("strategy_filters.atr_volatility.min_atr_pct", 0.0, 0.1)
        self._check_range("strategy_filters.atr_volatility.max_atr_pct", 0.001, 0.5)
        self._warn_if(
            sf.get("atr_volatility", {}).get("min_atr_pct", 0) >= sf.get("atr_volatility", {}).get("max_atr_pct", 1),
            "atr_volatility.min_atr_pct 应小于 max_atr_pct"
        )
    
    def _check_execution(self):
        exec_cfg = self._get("execution", {})
        if not isinstance(exec_cfg, dict):
            return
        self._check_in("execution.order_type", {"limit", "market"})
        self._check_in("execution.tp_target", {"structural", "fixed", "trailing"})
        self._check_range("execution.trail_atr_mult", 0.3, 5.0)
        self._check_range("execution.smart_sl_multiplier", 0.3, 1.5)
    
    def _check_smc_quality(self):
        sq = self._get("smc_quality", {})
        if not isinstance(sq, dict):
            return
        self._check_range("smc_quality.near_zone_atr_mult", 0.1, 3.0)
        self._check_range("smc_quality.sweep_lookback_bars", 1, 50)
        self._check_range("smc_quality.fvg_lookback_bars", 5, 200)
        self._check_range("smc_quality.ob_lookback_bars", 3, 100)
        self._check_range("smc_quality.min_quality_to_trade", 0, 100)
    
    def _check_telegram(self):
        tg = self._get("telegram", {})
        if not isinstance(tg, dict):
            return
        self._check_type("telegram.send_observer", bool, optional=True)
        self._check_type("telegram.send_observer_all", bool, optional=True)
        self._check_type("telegram.send_approved", bool, optional=True)
        # 检查环境变量
        import os
        has_token = bool(os.getenv("TG_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN"))
        has_chat_id = bool(os.getenv("TG_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID"))
        if tg.get("send_observer") or tg.get("send_approved"):
            self._warn_if(
                not has_token,
                "Telegram 启用了推送但未设置 TG_BOT_TOKEN 环境变量"
            )
            self._warn_if(
                not has_chat_id,
                "Telegram 启用了推送但未设置 TG_CHAT_ID 环境变量"
            )
    
    def _check_position_sizing(self):
        ps = self._get("position_sizing", {})
        if not isinstance(ps, dict) or not ps.get("enabled", False):
            return
        grades = ps.get("grade_risk_multiplier", {})
        if grades:
            for grade, mult in grades.items():
                if not isinstance(mult, (int, float)) or mult < 0:
                    self._report.warnings.append(
                        f"position_sizing.grade_risk_multiplier.{grade} 应为非负数，实际为 {mult}"
                    )
        observe = ps.get("observe_grades", [])
        for g in observe:
            if g not in grades:
                self._report.warnings.append(f"observe_grades 含 '{g}' 但 grade_risk_multiplier 中未定义")


# ── 便捷入口 ──────────────────────────────────────────────
def validate_config(config: dict) -> ConfigReport:
    """一键验证配置"""
    validator = ConfigValidator(config)
    return validator.validate()
