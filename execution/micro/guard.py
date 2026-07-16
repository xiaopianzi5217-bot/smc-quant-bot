# -*- coding: utf-8 -*-
"""
微观执行卫士 (Micro Execution Guard) — 无 ZMQ 版
===============================================
直接读取 MicroFeeder.state 字典，非阻塞检查盘口环境。

用法:
    feeder = MicroFeeder("BTCUSDT")
    guard = MicroExecutionGuard(feeder.state)
    ok = guard.check_entry_immediate("LONG")     # 瞬时
    ok = guard.verify_entry("LONG", timeout=30)  # 阻塞等待
"""
from __future__ import annotations

import time
from typing import Dict, Optional

# ============================================================
# 默认阈值
# ============================================================
OBI_LONG_THRESHOLD = 0.20
OBI_SHORT_THRESHOLD = -0.20
CVD_LONG_THRESHOLD = 5.0
CVD_SHORT_THRESHOLD = -5.0
POLL_INTERVAL = 0.5


class MicroExecutionGuard:
    """微观执行卫士 — 直接读取共享 state 字典

    属性:
        latest_state (dict): 当前读取到的微观状态快照
    """

    def __init__(
        self,
        shared_state: Dict,
        obi_long: float = OBI_LONG_THRESHOLD,
        obi_short: float = OBI_SHORT_THRESHOLD,
        cvd_long: float = CVD_LONG_THRESHOLD,
        cvd_short: float = CVD_SHORT_THRESHOLD,
    ):
        self._state = shared_state
        self.obi_long_threshold = obi_long
        self.obi_short_threshold = obi_short
        self.cvd_long_threshold = cvd_long
        self.cvd_short_threshold = cvd_short

    # ------------------------------------------------------------------
    # 外部接口
    # ------------------------------------------------------------------
    @property
    def latest_state(self) -> Dict:
        """返回当前状态快照（拷贝，防止外部修改）"""
        return dict(self._state)

    def get_snapshot(self) -> Dict:
        """获取最新的微观状态快照"""
        return dict(self._state)

    def is_alive(self, max_age: float = 5.0) -> bool:
        """检查 Feeder 是否仍在推送数据"""
        ts = self._state.get("ts", 0.0)
        return (time.time() - ts) < max_age

    def check_entry_immediate(self, direction: str) -> Optional[bool]:
        """瞬时检查当前微观状态（非阻塞）

        Args:
            direction: "LONG" 或 "SHORT"

        Returns:
            True  → 微观环境支持
            False → 微观环境不支持
            None  → 数据不足（超过 5 秒未更新/脏数据/未初始化）
        """
        # ---- 新增：检查脏数据标志 ----
        if self._state.get("is_stale", True):
            return None

        obi = self._state.get("obi", 0.0)
        ts = self._state.get("ts", 0.0)

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
        """微观验证逻辑 — 在 timeout 窗口内等待 OBI + CVD 确认

        Args:
            direction: "LONG" / "SHORT"
            timeout_seconds: 最长等待确认时间（秒）
            require_cvd: 是否要求 CVD 变化也达标（默认 True）

        Returns:
            True  → 放行
            False → 超时/不满足，拦截

        ⚠️ 此方法是阻塞的！请在业务线程中调用。
        """
        direction_upper = direction.upper()
        print(
            f"[MicroGuard] 正在为 {direction_upper} 确认微观环境 "
            f"(timeout={timeout_seconds}s)..."
        )

        start_time = time.time()
        initial_cvd = self._state.get("cvd", 0.0)

        while time.time() - start_time < timeout_seconds:
            obi = self._state.get("obi", 0.0)
            cvd_delta = self._state.get("cvd", 0.0) - initial_cvd

            if direction_upper == "LONG":
                obi_ok = obi > self.obi_long_threshold
                cvd_ok = cvd_delta > self.cvd_long_threshold if require_cvd else True
                if obi_ok and cvd_ok:
                    print(
                        f"[MicroGuard] ✅ LONG 放行! "
                        f"OBI={obi:.4f} CVDΔ={cvd_delta:.2f}"
                    )
                    return True

            elif direction_upper == "SHORT":
                obi_ok = obi < self.obi_short_threshold
                cvd_ok = cvd_delta < self.cvd_short_threshold if require_cvd else True
                if obi_ok and cvd_ok:
                    print(
                        f"[MicroGuard] ✅ SHORT 放行! "
                        f"OBI={obi:.4f} CVDΔ={cvd_delta:.2f}"
                    )
                    return True

            print(
                f"[MicroGuard] 等待中: OBI={obi:.4f} "
                f"CVDΔ={cvd_delta:.2f} "
                f"elapsed={time.time()-start_time:.1f}s"
            )
            time.sleep(POLL_INTERVAL)

        print(f"[MicroGuard] ❌ 微观确认超时 ({timeout_seconds}s)，拦截")
        return False
