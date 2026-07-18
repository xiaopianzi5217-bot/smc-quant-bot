# utils/signal_tracker.py
import json
import uuid
from datetime import datetime


class SignalTracker:
    """信号全生命周期追踪器。

    记录每笔信号的完整特征、入口价、SL/TP、状态变更。
    数据以 JSONL 格式持久化到磁盘，确保进程重启后数据不丢失。

    用法：
        tracker = SignalTracker()
        # 开单时记录
        signal_id = tracker.record_signal({
            "symbol": "BTC/USDT",
            "direction": "Long",
            "score": 72.5,
            "ev": 0.08,
            "features": {"OB": 10, "SQZMOM": 8},
            "entry_price": 65432.1,
            "sl": 65000.0,
            "tp": 66500.0,
        })
        # 平仓时更新
        tracker.update_outcome(signal_id, final_r=1.5, bars_5_r=0.8, bars_10_r=1.2)
    """

    def __init__(self, log_file="logs/signal_outcomes.jsonl"):
        self.log_file = log_file

    def record_signal(self, signal: dict) -> str:
        """记录一笔新信号，返回全局唯一 signal_id。

        Args:
            signal: 包含 symbol, direction, score, ev, features,
                    entry_price, sl, tp 等字段的字典

        Returns:
            signal_id (UUID 字符串)
        """
        signal_id = str(uuid.uuid4())
        record = {
            "id": signal_id,
            "ts": datetime.now().isoformat(),
            "symbol": signal.get("symbol"),
            "direction": signal.get("direction"),
            "score": signal.get("score"),
            "ev": signal.get("ev"),
            "features": signal.get("features", {}),
            "entry": signal.get("entry_price", signal.get("entry")),
            "sl": signal.get("sl"),
            "tp": signal.get("tp"),
            "tp1": signal.get("tp1"),
            "tp2": signal.get("tp2"),
            "tp3": signal.get("tp3"),
            "rr": signal.get("rr"),
            "regime": signal.get("regime"),
            "setup_type": signal.get("setup_type", signal.get("reason")),
            "book": signal.get("book"),
            "status": "open",
        }
        try:
            # 递归转换 features 中所有 numpy/bool 类型为原生 Python 类型
            def _to_native(o):
                import numpy as np
                if isinstance(o, (np.bool_, np.bool)):
                    return bool(o)
                if isinstance(o, (np.integer, np.int64, np.int32)):
                    return int(o)
                if isinstance(o, (np.floating, np.float64)):
                    return float(o)
                if isinstance(o, dict):
                    return {k: _to_native(v) for k, v in o.items()}
                if isinstance(o, (list, tuple)):
                    return [_to_native(v) for v in o]
                return o
            record = _to_native(record)
            with open(self.log_file, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[SignalTracker] 写入失败: {e}")
        return signal_id

    def update_outcome(self, signal_id: str, final_r: float,
                       bars_5_r: float = 0, bars_10_r: float = 0):
        """更新信号的平仓结果（追加写入）。

        Args:
            signal_id: 由 record_signal 返回的 ID
            final_r: 最终盈亏 R 倍数
            bars_5_r: 开单后 5 根 K 线后的 R（用于短周期评估）
            bars_10_r: 开单后 10 根 K 线后的 R（用于中周期评估）
        """
        outcome = {
            "id": signal_id,
            "type": "outcome",
            "ts": datetime.now().isoformat(),
            "final_r": round(final_r, 4),
            "bars_5_r": round(bars_5_r, 4),
            "bars_10_r": round(bars_10_r, 4),
        }
        try:
            with open(self.log_file, "a") as f:
                f.write(json.dumps(outcome, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[SignalTracker] 结果写入失败: {e}")
