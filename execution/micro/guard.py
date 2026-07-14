# -*- coding: utf-8 -*-
"""
微观执行卫士 (Micro Execution Guard)
==============================
主进程调用：通过 ZMQ SUB 非阻塞接收微观数据，在发单前做盘口验证。

核心逻辑：
    1. 非阻塞轮询 ZMQ 队列，只取最新帧（不堆积）
    2. verify_entry() 在 timeout 窗口内持续等待确认信号
    3. 如果微观环境（OBI + CVD delta）符合方向要求则放行

阈值说明（可调参数）：
    LONG:
        obi > OBI_LONG_THRESHOLD (0.20)   → 买盘挂单多
        cvd_delta > CVD_LONG_THRESHOLD (5.0)  → 主动买入量 > 5 BTC
    SHORT:
        obi < OBI_SHORT_THRESHOLD (-0.20)  → 卖盘挂单多
        cvd_delta < CVD_SHORT_THRESHOLD (-5.0)  → 主动卖出量 > 5 BTC

使用方式:
    >>> from execution.micro.guard import MicroExecutionGuard
    >>> guard = MicroExecutionGuard(symbol="BTCUSDT")
    >>> approved = guard.verify_entry(direction="LONG", timeout_seconds=60)
"""
from __future__ import annotations

import json
import time
from typing import Optional

try:
    import zmq
except ImportError:
    zmq = None  # type: ignore

# ============================================================
# 默认阈值
# ============================================================
DEFAULT_ZMQ_CONNECT = "tcp://127.0.0.1:5555"
OBI_LONG_THRESHOLD = 0.20       # 多头 OBI 门槛
OBI_SHORT_THRESHOLD = -0.20     # 空头 OBI 门槛
CVD_LONG_THRESHOLD = 5.0        # 多头 CVD 增量门槛 (BTC)
CVD_SHORT_THRESHOLD = -5.0      # 空头 CVD 增量门槛 (BTC)
POLL_INTERVAL = 0.5             # 轮询间隔 (秒)


class MicroExecutionGuard:
    """微观执行卫士

    属性:
        latest_state (dict): 最新的微观状态快照
            { "obi": float, "cvd": float, "ts": float, "price": float, ... }
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        connect_addr: str = DEFAULT_ZMQ_CONNECT,
        obi_long: float = OBI_LONG_THRESHOLD,
        obi_short: float = OBI_SHORT_THRESHOLD,
        cvd_long: float = CVD_LONG_THRESHOLD,
        cvd_short: float = CVD_SHORT_THRESHOLD,
    ):
        if zmq is None:
            raise ImportError("pyzmq is required. pip install pyzmq>=25.0.0")

        self.symbol = symbol.upper()
        self.obi_long_threshold = obi_long
        self.obi_short_threshold = obi_short
        self.cvd_long_threshold = cvd_long
        self.cvd_short_threshold = cvd_short

        # ---------- ZMQ Subscriber ----------
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.SUB)
        self.socket.connect(connect_addr)
        # 只订阅当前交易对的 topic
        self.socket.setsockopt_string(zmq.SUBSCRIBE, self.symbol)
        # 设置接收超时，防止 socket 阻塞过久
        self.socket.setsockopt(zmq.RCVTIMEO, 200)  # 200ms

        # ---------- 最新状态快照 ----------
        self.latest_state: dict = {
            "obi": 0.0,
            "cvd": 0.0,
            "ts": 0.0,
            "price": 0.0,
            "bids": 0.0,
            "asks": 0.0,
            "tick_count": 0,
        }
        self._last_success_ts: float = 0.0  # 上次成功收到数据的时间

        # 初始填充一次（阻塞等待第一帧）
        self._try_drain()

    # ------------------------------------------------------------------
    # 内部：非阻塞接收
    # ------------------------------------------------------------------
    def _try_drain(self):
        """非阻塞地抽干 ZMQ 消息队列，只保留最新一帧

        由于 ZMQ PUB/SUB 的机制，如果消费者慢了，消息会在 socket
        缓冲区堆积。这个方法通过 NOBLOCK 标志循环读取直到队列为空，
        最终 self.latest_state 中保存的是最新的那一帧。
        """
        updated = False
        try:
            while True:
                msg = self.socket.recv_string(flags=zmq.NOBLOCK)
                topic, payload_str = msg.split(" ", 1)
                data = json.loads(payload_str)
                if data.get("symbol") == self.symbol:
                    self.latest_state.update(data)
                    self._last_success_ts = data.get("ts", time.time())
                    updated = True
        except zmq.Again:
            pass  # 队列已空，当前 self.latest_state 是最新帧
        except (ValueError, json.JSONDecodeError) as exc:
            # 消息格式异常，丢弃
            print(f"[MicroGuard] 消息解析异常: {exc}")
        return updated

    # ------------------------------------------------------------------
    # 外部接口
    # ------------------------------------------------------------------
    def get_snapshot(self) -> dict:
        """获取最新的微观状态快照（非阻塞）"""
        self._try_drain()
        return dict(self.latest_state)

    def is_alive(self, max_age: float = 5.0) -> bool:
        """检查 Feeder 是否存活

        Args:
            max_age: 如果最后一次收到数据超过 max_age 秒，认为 Feeder 死掉

        Returns:
            True 如果 Feeder 仍在发送数据
        """
        self._try_drain()
        return (time.time() - self._last_success_ts) < max_age

    def check_entry_immediate(self, direction: str) -> Optional[bool]:
        """瞬时检查当前微观状态（非阻塞，不等待）

        Args:
            direction: "LONG" 或 "SHORT"

        Returns:
            True  → 微观环境支持
            False → 微观环境不支持
            None  → 数据不足，无法判断
        """
        self._try_drain()

        state = self.latest_state
        obi = state.get("obi", 0.0)
        ts = state.get("ts", 0.0)

        # 数据太老（超过 5 秒），不可靠
        if time.time() - ts > 5.0:
            return None

        if direction.upper() == "LONG":
            return obi > self.obi_long_threshold
        elif direction.upper() == "SHORT":
            return obi < self.obi_short_threshold
        else:
            return None

    def verify_entry(
        self,
        direction: str,
        timeout_seconds: float = 60.0,
        require_cvd: bool = True,
    ) -> bool:
        """微观验证逻辑 - 在 timeout 窗口内等待 OBI + CVD 确认

        Args:
            direction: "LONG" / "SHORT"
            timeout_seconds: 最长等待确认时间（秒）
            require_cvd: 是否要求 CVD 变化也达标（默认 True）

        Returns:
            True  → 微观环境确认，放行
            False → 超时或微观环境不满足，拦截

        ⚠️ 此方法是阻塞的！最长会挂起 timeout_seconds 秒。
           建议在主进程的业务线程或协程中调用。
        """
        direction_upper = direction.upper()
        print(
            f"[MicroGuard] 正在为 {direction_upper} 订单进行微观环境确认 "
            f"(timeout={timeout_seconds}s)..."
        )

        start_time = time.time()
        start_ts = self.latest_state.get("ts", start_time)

        # 记录初始 CVD 以计算相对变化
        self._try_drain()
        initial_cvd = self.latest_state.get("cvd", 0.0)

        while time.time() - start_time < timeout_seconds:
            self._try_drain()

            state = self.latest_state
            current_obi = state.get("obi", 0.0)
            current_cvd = state.get("cvd", 0.0)
            cvd_delta = current_cvd - initial_cvd

            # ---- 判断逻辑 ----
            if direction_upper == "LONG":
                obi_ok = current_obi > self.obi_long_threshold
                cvd_ok = cvd_delta > self.cvd_long_threshold if require_cvd else True
                if obi_ok and cvd_ok:
                    print(
                        f"[MicroGuard] ✅ LONG 放行! "
                        f"OBI={current_obi:.4f} (>{self.obi_long_threshold}), "
                        f"CVDΔ={cvd_delta:.2f} (>{self.cvd_long_threshold})"
                    )
                    return True

            elif direction_upper == "SHORT":
                obi_ok = current_obi < self.obi_short_threshold
                cvd_ok = cvd_delta < self.cvd_short_threshold if require_cvd else True
                if obi_ok and cvd_ok:
                    print(
                        f"[MicroGuard] ✅ SHORT 放行! "
                        f"OBI={current_obi:.4f} (<{self.obi_short_threshold}), "
                        f"CVDΔ={cvd_delta:.2f} (<{self.cvd_short_threshold})"
                    )
                    return True

            # 每次循环输出当前状态（调试用）
            print(
                f"[MicroGuard] 等待确认: OBI={current_obi:.4f} "
                f"CVDΔ={cvd_delta:.2f} "
                f"elapsed={time.time() - start_time:.1f}s"
            )

            time.sleep(POLL_INTERVAL)

        # 超时，未确认
        print(
            f"[MicroGuard] ❌ 微观确认超时 ({timeout_seconds}s)，"
            f"取消 {direction_upper} 开仓。"
        )
        return False

    def close(self):
        """清理 ZMQ 资源"""
        try:
            self.socket.close()
            self.context.term()
        except Exception:
            pass


# ============================================================
# 演示/测试
# ============================================================
if __name__ == "__main__":
    import sys

    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    direction = sys.argv[2] if len(sys.argv) > 2 else "LONG"

    print(f"[MicroGuard] 测试模式: symbol={symbol}, direction={direction}")
    guard = MicroExecutionGuard(symbol=symbol)

    # 先看瞬时快照
    snapshot = guard.get_snapshot()
    print(f"[MicroGuard] 当前快照: {snapshot}")

    # 执行验证（阻塞）
    result = guard.verify_entry(direction=direction, timeout_seconds=30.0)
    print(f"[MicroGuard] 验证结果: {'✅ 放行' if result else '❌ 拦截'}")
    guard.close()
