# -*- coding: utf-8 -*-
"""position_reconciler.py — 本地持仓 vs 交易所持仓对账
能力:
1. startup_recover()  — 启动时：磁盘恢复 + 可选交易所对账
2. reconcile_once()   — 单次对账，返回差异报告
3. periodic_check()   — 供后台线程调用的定期巡查
dry_run / 无 API Key 时自动降级为「只读本地」。
"""
from __future__ import annotations
import os
import time
import traceback
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional

from state.position_manager import position_manager
from utils.structured_logger import slog


def _is_live_ready() -> bool:
    return bool(
        os.getenv("BITGET_API_KEY")
        or os.getenv("EXCHANGE_API_KEY")
    ) and not (
        os.getenv("SMC_MODE", "dry_run").lower() in {"dry_run", "probe", "paper"}
        and os.getenv("FORCE_LIVE_RECONCILE", "").lower() not in {"1", "true", "yes"}
    )


@dataclass
class ReconcileDiff:
    symbol: str
    kind: str  # local_only | exchange_only | direction_mismatch | size_mismatch | ok
    local: Optional[dict] = None
    exchange: Optional[dict] = None
    detail: str = ""


@dataclass
class ReconcileReport:
    ts: float = field(default_factory=time.time)
    mode: str = "local_only"
    local_count: int = 0
    exchange_count: int = 0
    diffs: List[ReconcileDiff] = field(default_factory=list)
    recovered_symbols: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "mode": self.mode,
            "local_count": self.local_count,
            "exchange_count": self.exchange_count,
            "recovered_symbols": self.recovered_symbols,
            "errors": self.errors,
            "diffs": [asdict(d) for d in self.diffs],
            "has_issues": any(d.kind != "ok" for d in self.diffs),
        }

    def summary_text(self) -> str:
        issues = [d for d in self.diffs if d.kind != "ok"]
        if not issues:
            return f"[Reconcile] OK mode={self.mode} local={self.local_count} exchange={self.exchange_count}"
        lines = [
            f"[Reconcile] ISSUES mode={self.mode} local={self.local_count} exchange={self.exchange_count}"
        ]
        for d in issues:
            lines.append(f"  - {d.kind}: {d.symbol} {d.detail}")
        for e in self.errors:
            lines.append(f"  ! error: {e}")
        return "\n".join(lines)


class PositionReconciler:
    def __init__(
        self,
        adapter_factory: Optional[Callable[[], Any]] = None,
        notify: Optional[Callable[[str], Any]] = None,
        size_tolerance: float = 1e-8,
    ):
        self._adapter_factory = adapter_factory
        self._notify = notify
        self.size_tolerance = size_tolerance
        self._last_report: Optional[ReconcileReport] = None
        self._last_run_ts: float = 0.0

    def _get_adapter(self):
        if self._adapter_factory:
            return self._adapter_factory()
        try:
            from execution.exchange_adapter import ExchangeAdapter
            dry = not _is_live_ready()
            return ExchangeAdapter(exchange_name="bitget", dry_run=dry)
        except Exception as exc:
            slog.error(f"[Reconciler] adapter init failed: {exc}")
            return None

    def _notify_msg(self, msg: str) -> None:
        if not self._notify:
            return
        try:
            self._notify(msg)
        except Exception:
            pass

    def startup_recover(self, do_exchange: bool = True, sync_local_from_exchange: bool = True) -> ReconcileReport:
        report = ReconcileReport()
        try:
            recovered = position_manager.recover_from_disk()
            report.recovered_symbols = list(recovered or [])
            report.local_count = len(position_manager)
        except Exception as exc:
            report.errors.append(f"disk recover: {exc}")

        if do_exchange and _is_live_ready():
            # 启动恢复必须把「交易所有、本地无」的仓位真正导入 position_manager，
            # 否则重启后系统无法感知交易所真实持仓（如 BTC 开仓后服务器重启）
            sub = self.reconcile_once(sync_local_from_exchange=sync_local_from_exchange)
            report.mode = sub.mode
            report.exchange_count = sub.exchange_count
            report.diffs.extend(sub.diffs)
            report.errors.extend(sub.errors)
            if sync_local_from_exchange:
                # 导入成功的仓位（source=exchange_reconcile）计入恢复列表
                try:
                    for _sym, _pos in (position_manager.get() or {}).items():
                        if (_pos or {}).get("source") == "exchange_reconcile":
                            if _sym not in report.recovered_symbols:
                                report.recovered_symbols.append(_sym)
                except Exception:
                    pass
        else:
            report.mode = "local_only"
            for sym, pos in (position_manager.get() or {}).items():
                report.diffs.append(
                    ReconcileDiff(symbol=sym, kind="ok", local=pos, detail="dry/local-only")
                )

        self._last_report = report
        self._last_run_ts = time.time()

        if report.recovered_symbols:
            self._notify_msg(
                f"🔁 持仓恢复: {len(report.recovered_symbols)} 个 — {report.recovered_symbols}"
            )
        if any(d.kind != "ok" for d in report.diffs):
            self._notify_msg(report.summary_text())
        print(report.summary_text())
        return report
    def reconcile_once(
        self,
        symbols: Optional[List[str]] = None,
        sync_local_from_exchange: bool = False,
    ) -> ReconcileReport:
        report = ReconcileReport()
        local_map: Dict[str, dict] = position_manager.get() or {}
        report.local_count = len(local_map)

        if not _is_live_ready():
            report.mode = "local_only"
            for sym, pos in local_map.items():
                report.diffs.append(
                    ReconcileDiff(symbol=sym, kind="ok", local=pos, detail="dry/local-only")
                )
            self._last_report = report
            self._last_run_ts = time.time()
            return report

        adapter = self._get_adapter()
        if adapter is None or getattr(adapter, "dry_run", True):
            report.mode = "local_only"
            report.errors.append("adapter unavailable or dry_run")
            self._last_report = report
            self._last_run_ts = time.time()
            return report

        report.mode = "live"
        try:
            fetch_syms = symbols
            if fetch_syms is None:
                fetch_syms = list(local_map.keys()) or None
            exchange_positions = adapter.fetch_positions(fetch_syms)
        except Exception as exc:
            report.errors.append(f"fetch_positions: {exc}")
            traceback.print_exc()
            self._last_report = report
            self._last_run_ts = time.time()
            return report

        report.exchange_count = len(exchange_positions)
        exch_map: Dict[str, dict] = {}
        for ep in exchange_positions:
            sym = ep.get("symbol") or ""
            if not sym:
                continue
            exch_map[sym] = ep
            raw = ep.get("symbol_raw") or ""
            if raw and raw != sym:
                exch_map.setdefault(raw, ep)

        all_symbols = set(local_map.keys()) | {
            ep.get("symbol") for ep in exchange_positions if ep.get("symbol")
        }

        for sym in sorted(s for s in all_symbols if s):
            local = local_map.get(sym)
            exch = exch_map.get(sym)

            if local and not exch:
                report.diffs.append(
                    ReconcileDiff(
                        symbol=sym, kind="local_only", local=local,
                        detail="本地有仓、交易所无仓（可能已平或幽灵仓）",
                    )
                )
                continue
            if exch and not local:
                report.diffs.append(
                    ReconcileDiff(
                        symbol=sym, kind="exchange_only", exchange=exch,
                        detail="交易所有仓、本地无记录（可能漏记或外部开仓）",
                    )
                )
                if sync_local_from_exchange:
                    self._import_exchange_position(sym, exch)
                continue

            local_dir = (local or {}).get("direction")
            exch_dir = (exch or {}).get("direction")
            if local_dir and exch_dir and local_dir != exch_dir:
                report.diffs.append(
                    ReconcileDiff(
                        symbol=sym, kind="direction_mismatch",
                        local=local, exchange=exch,
                        detail=f"方向不一致 local={local_dir} exchange={exch_dir}",
                    )
                )
                continue

            local_size = float((local or {}).get("size") or (local or {}).get("volume") or 0)
            exch_size = float((exch or {}).get("size") or 0)
            if local_size > 0 and exch_size > 0:
                if abs(local_size - exch_size) > self.size_tolerance * max(local_size, exch_size, 1):
                    report.diffs.append(
                        ReconcileDiff(
                            symbol=sym, kind="size_mismatch",
                            local=local, exchange=exch,
                            detail=f"数量不一致 local={local_size} exchange={exch_size}",
                        )
                    )
                    continue

            report.diffs.append(
                ReconcileDiff(symbol=sym, kind="ok", local=local, exchange=exch, detail="一致")
            )

        self._last_report = report
        self._last_run_ts = time.time()
        return report

    def _import_exchange_position(self, symbol: str, exch: dict) -> None:
        if position_manager.exists(symbol):
            return
        pos = {
            "direction": exch.get("direction", "Long"),
            "entry": float(exch.get("entry") or 0),
            "current_sl": 0.0,
            "tp1": 0.0,
            "tp2": 0.0,
            "tp3": 0.0,
            "stage": 0,
            "size": float(exch.get("size") or 0),
            "source": "exchange_reconcile",
            "unrealized_pnl": float(exch.get("unrealized_pnl") or 0),
        }
        try:
            position_manager.update(symbol, pos)
            slog.info(f"[Reconciler] imported exchange position: {symbol} {pos['direction']}")
        except Exception as exc:
            slog.error(f"[Reconciler] import failed {symbol}: {exc}")

    def periodic_check(self, min_interval_sec: float = 60.0) -> Optional[ReconcileReport]:
        now = time.time()
        if now - self._last_run_ts < min_interval_sec:
            return None
        report = self.reconcile_once(sync_local_from_exchange=False)
        if any(d.kind != "ok" for d in report.diffs) or report.errors:
            self._notify_msg(report.summary_text())
            print(report.summary_text())
        return report

    @property
    def last_report(self) -> Optional[ReconcileReport]:
        return self._last_report


def _default_notify(msg: str) -> None:
    try:
        from notifier.telegram import send_telegram
        send_telegram(msg)
    except Exception:
        print(msg)


position_reconciler = PositionReconciler(notify=_default_notify)
