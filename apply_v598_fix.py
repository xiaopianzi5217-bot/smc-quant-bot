# -*- coding: utf-8 -*-
"""V59.8 平仓信号修复补丁

1. state/position_manager.py: 添加 close() 方法
   - 移除持仓
   - 记录 trade_journal.close_trade()
   - 触发每日快照 + 持久化

2. hf_auto_trader.py 两处开仓: 保存 initial_risk 字段
   确保保本后 risk=0 时仍能用 initial_risk 计算 R
"""
import io
import sys

sys.stdout.reconfigure(encoding="utf-8")


def patch_position_manager():
    """给 PositionManager 添加 close 方法"""
    path = "state/position_manager.py"
    lines = io.open(path, "r", encoding="utf-8").readlines()

    # 在 remove 方法后插入 close 方法
    insert_after = None
    for i, l in enumerate(lines):
        if l.strip() == "def remove(self, symbol: str):":
            # 找到 remove 方法的结束（下一个 def 或文件末尾）
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if lines[j].strip().startswith("def ") and not lines[j][0].isspace():
                    end = j
                    break
            insert_after = end - 1  # 在 remove 方法最后一行后插入
            break

    if insert_after is None:
        print("❌ 未找到 remove 方法")
        return False

    close_method = """
    def close(self, symbol: str, pnl_r=0.0, exit_reason="", exit_price=None):
        """
        平仓：移除持仓 + 记录 trade_journal.close_trade()。
        同时把 pnl_r/exit_reason/close_price 写入 trade_journal 形成 CLOSE 行。
        """
        with self._lock:
            pos = self._positions.pop(symbol, None)
            if pos is None:
                return
            self._mark_dirty()

        # 记录交易日志（延迟导入避免循环依赖）
        if pos:
            try:
                from state.trade_journal import journal as _tj
                _order_id = pos.get("order_id") or ""
                if _order_id:
                    _tj.close_trade(
                        order_id=_order_id,
                        close_price=float(exit_price or 0.0),
                        pnl_r=float(pnl_r or 0.0),
                        exit_reason=str(exit_reason or ""),
                    )
                    slog.info(f"[PositionManager.close] trade_journal CLOSE 已写入: {_order_id} reason={exit_reason} pnl_r={pnl_r:.2f}")
            except Exception as e:
                slog.error(f"[PositionManager.close] trade_journal 写入失败: {e}")

        self._daily_snapshot()
        self._save()

"""

    new_lines = lines[:insert_after + 1] + [close_method] + lines[insert_after + 1:]
    with io.open(path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    print(f"✅ {path}: close() 方法已添加")
    return True


def patch_hf_auto_trader_initial_risk():
    """在开仓写入 position_manager 时添加 initial_risk 字段"""
    path = "hf_auto_trader.py"
    lines = io.open(path, "r", encoding="utf-8").readlines()
    text = "".join(lines)

    # 第一处: V6 路由 (check_and_open_v6_with_routing)
    # 找到 "\"current_sl\": sl," 后添加 initial_risk
    old_1 = "\"current_sl\": sl,\n            \"tp1\": tp1,"
    new_1 = "\"current_sl\": sl,\n            \"initial_risk\": abs(entry - sl) if sl and entry else 0.0,\n            \"tp1\": tp1,"

    # 第二处: check_and_open 普通路径
    old_2 = "\"current_sl\": sl,\n        \"tp1\": tp1,"
    new_2 = "\"current_sl\": sl,\n        \"initial_risk\": abs(entry - sl) if sl and entry else 0.0,\n        \"tp1\": tp1,"

    count = 0
    if old_1 in text:
        text = text.replace(old_1, new_1, 1)
        count += 1
        print(f"✅ V6 路由开仓 initial_risk 已添加")
    else:
        print(f"⚠️ V6 路由开仓模板未匹配，尝试其他方式")

    if old_2 in text:
        text = text.replace(old_2, new_2, 1)
        count += 1
        print(f"✅ check_and_open 普通开仓 initial_risk 已添加")
    else:
        print(f"⚠️ check_and_open 普通开仓模板未匹配，尝试其他方式")

    # 如果未匹配，用更宽松的搜索
    if count < 2:
        # 查找所有 current_sl 开仓模板
        import re
        # 找到 position_manager.update(symbol 的位置
        lines_list = text.split('\n')
        for i, l in enumerate(lines_list):
            if 'position_manager.update(symbol' in l:
                # 打印上下文
                snippet = '\n'.join(lines_list[i:i+10])
                print(f"\n  line {i+1}: {l.strip()}")

    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"✅ {path}: initial_risk 修改完成 ({count} 处)")


# 执行
print("=" * 50)
print("V59.8 平仓信号修复补丁")
print("=" * 50)

ok1 = patch_position_manager()
ok2 = patch_hf_auto_trader_initial_risk()

print()
print("补丁执行完成!")
print(f"  position_manager.close(): {'✅' if ok1 else '❌'}")
print(f"  hf_auto_trader initial_risk: {'✅' if ok2 else '❌'}")
EOF