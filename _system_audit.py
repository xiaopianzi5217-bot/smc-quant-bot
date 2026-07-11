# -*- coding: utf-8 -*-
"""系统审计：当前状态与待优化项"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.absolute()))

issues = []
ok = []

# 1. 核心文件体积
files_to_check = {
    "hf_auto_trader.py": "主交易循环",
    "app.py": "Web 应用入口",
    "main.py": "旧版启动入口",
    "final_forge/v56_5_stable_engine.py": "V56.5 引擎",
    "state/trade_journal.py": "交易日志",
    "state/position_manager.py": "持仓管理",
    "decision/v37_gate.py": "V37 最终闸门",
    "strategy/v565_quality_gate.py": "V56.5 Quality Gate",
    "notifier/telegram.py": "Telegram 推送",
}
for fp, desc in files_to_check.items():
    p = Path(fp)
    if p.exists():
        sz = p.stat().st_size
        lines = len(open(p, encoding="utf-8").readlines())
        ok.append(f"  OK  {desc} ({fp}): {sz:,} bytes, {lines} lines")
    else:
        issues.append(f"  MISS  {desc} ({fp}): 文件不存在")

# 2. hf_auto_trader.py 结构
code = open("hf_auto_trader.py", encoding="utf-8").read()
lines = code.split("\n")

# 检查函数定义
funcs = {
    "safe_send": "全局限流推送",
    "fetch_ohlcv": "数据获取",
    "scan_and_decide": "信号决策管线",
    "_detect_observer_events": "Observer事件检测",
    "_new_observer_events": "Observer去重",
    "_push_observer_event": "Observer推送",
    "check_and_open": "开单检查与推送",
    "check_trailing": "追踪止损",
    "_trigger_stop_loss": "止损触发",
    "main_loop": "主循环",
}
for fname, desc in funcs.items():
    found = False
    for i, line in enumerate(lines, 1):
        if f"def {fname}" in line:
            found = True
            ok.append(f"  OK  {desc} ({fname}) L{i}")
            break
    if not found:
        issues.append(f"  MISS  {desc} ({fname}) 未定义")

# 3. 检查关键参数定义
params = {
    "SCAN_INTERVAL": "扫描间隔",
    "MIN_EV_FOR_PUSH": "EV推送阈值",
    "MIN_SCORE_FOR_PUSH": "Score推送阈值",
    "MIN_SCORE_GAP": "多空分差阈值",
    "SIGNAL_COOLDOWN_SECONDS": "信号冷却时间",
    "TREND_END_PULLBACK_ATR": "趋势末端ATR限制",
}
for pname, desc in params.items():
    found = False
    for i, line in enumerate(lines, 1):
        if line.strip().startswith(pname):
            val = line.split("=")[-1].strip().rstrip(",")
            found = True
            ok.append(f"  OK  {desc} ({pname}) = {val}")
            break
    if not found:
        issues.append(f"  MISS  {desc} ({pname}) 未定义")

# 4. 检查已知问题
for i, line in enumerate(lines, 1):
    if "DataFreme" in line or "DataFreme" in line:
        issues.append(f"  TYPO L{i}: DataFreme -> DataFrame (不影响运行)")
        break

# 5. position_manager 是否正常
try:
    from state.position_manager import position_manager
    # 尝试初始化
    pos = position_manager.get()
    ok.append(f"  OK  position_manager.get() = {pos}")
except Exception as e:
    issues.append(f"  FAIL position_manager: {e}")

# 6. TradeJournal 是否正常
try:
    from state.trade_journal import journal
    j = journal.load_all()
    ok.append(f"  OK  TradeJournal: {'empty' if len(j)==0 else f'{len(j)} records'}")
except Exception as e:
    issues.append(f"  FAIL TradeJournal: {e}")

# 7. V37 Gate 是否可加载
try:
    from decision.v37_gate import v37_final_gate
    ok.append("  OK  v37_final_gate 可加载")
except Exception as e:
    issues.append(f"  FAIL v37_gate: {e}")

# 8. 检查 V56.5_Engine 是否可加载
try:
    from final_forge.v56_5_stable_engine import V56_5_Engine
    ok.append("  OK  V56_5_Engine 可加载")
except Exception as e:
    issues.append(f"  FAIL V56_5_Engine: {e}")

# 9. config 是否正常
try:
    import config
    ok.append(f"  OK  config.STRATEGY_PARAMS keys: {list(config.STRATEGY_PARAMS.keys())}")
except Exception as e:
    issues.append(f"  FAIL config: {e}")

# 10. check_and_open 中的已知空指针风险
risk_lines = []
for i, line in enumerate(lines, 1):
    if "result.get(" in line and 'or {}' not in line and 'or 0' not in line:
        if "result.get(\"decision\", {}).get(\"signal\", {}).get(\"signal_tier\")" in line:
            risk_lines.append(f"  WARN L{i}: chain .get().get() 无兜底默认值")

print("=" * 60)
print("  SMC Bot 系统审计报告")
print("=" * 60)

print(f"\n✅ 正常项 ({len(ok)}):")
for o in ok:
    print(o)

print(f"\n⚠️  问题/待优化 ({len(issues)}):")
for iss in issues:
    print(iss)

print("\n" + "=" * 60)
print("  待优化建议")
print("=" * 60)

suggestions = [
    "1. hf_auto_trader.py 没有 __main__ 入口，只能通过 app.py 启动",
    "2. scan_and_decide 内部 from final_forge... 存在局部 import 重名风险",
    "3. check_and_open 中大量 result.get() 链式访问，部分缺默认值或空指针保护",
    "4. main_loop 中 Observer 推送代码块过长（>150行），建议抽取为函数",
    "5. 缺少单元测试覆盖 scan_and_decide(check_and_open/trailing)",
    "6. signal_id 去重用 900s 时间槽，但 SIGNAL_COOLDOWN_SECONDS=900 是独立的，两者可能不一致",
    "7. trade_journal 的 open_trade/close_trade 写入无事务保护，高并发可能错乱",
    "8. _fetch_ticker_price 和 fetch_ohlcv 各用一套重试逻辑，可统一",
    "9. check_trailing 中的 PARTIAL_CLOSE 逻辑写在主循环中，未封装独立模块",
    "10. V56.5 Quality Gate 内部 pring(emoji) 在 GBK 终端报错，但好在不影响运行",
]
for s in suggestions:
    print(f"  {s}")
