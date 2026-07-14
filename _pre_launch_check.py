# -*- coding: utf-8 -*-
"""
上线前全面系统检查
"""
import sys
import os
import json
from pathlib import Path

errors = []
warnings = []
passed = []

def check(name, ok, detail=""):
    if ok:
        passed.append(f"  OK  {name}")
        print(f"  [PASS] {name}" + (f" - {detail}" if detail else ""))
    else:
        errors.append(f"  FAIL {name}: {detail}")
        print(f"  [FAIL] {name}: {detail}")

def warn(name, detail=""):
    warnings.append(f"  WARN {name}: {detail}")
    print(f"  [WARN] {name}: {detail}")

print("="*56)
print("[上线前系统检查]")
print("="*56)

# 1. 环境变量
print("\n--- 1. 环境变量 ---")
check("BITGET_API_KEY (或 BINANCE_API_KEY)",
      bool(os.getenv("BITGET_API_KEY")) or bool(os.getenv("BINANCE_API_KEY")),
      "BITGET="+("已" if os.getenv("BITGET_API_KEY") else "未")+
      " BINANCE="+("已" if os.getenv("BINANCE_API_KEY") else "未"))
if os.getenv("BITGET_API_KEY"):
    check("BITGET_SECRET", bool(os.getenv("BITGET_SECRET")))
    check("BITGET_PASSPHRASE", bool(os.getenv("BITGET_PASSPHRASE")))
if os.getenv("BINANCE_API_KEY"):
    check("BINANCE_SECRET", bool(os.getenv("BINANCE_SECRET")))
if os.getenv("BITGET_API_KEY"):
    warn("当前为 Bitget API，如需 Binance 需改环境变量名")
elif not os.getenv("BINANCE_API_KEY"):
    warn("未检测到任何 API 密钥")

# 2. 配置文件
print("\n--- 2. 配置文件 ---")
cfg_dir = Path("config")
if cfg_dir.exists():
    cfgs = list(cfg_dir.glob("*.json"))
    check("配置文件总数", len(cfgs)>0, f"{len(cfgs)} 个")
    for c in cfgs:
        try:
            json.loads(c.read_text(encoding="utf-8"))
            check(f"  {c.name} JSON 格式", True)
        except Exception as e:
            check(f"  {c.name} JSON", False, str(e))
else:
    check("config/ 目录", False, "不存在")

# 3. 语法检查
print("\n--- 3. 语法检查 ---")
import py_compile
key_files = [
    "app.py","hf_auto_trader.py","config.py",
    "notifier/telegram.py","notifier/manager.py",
    "state/position_manager.py","state/trade_journal.py",
    "utils/daily_panel.py","utils/feedback_loop.py",
    "utils/probability_calibrator.py","utils/adaptive_features.py",
    "utils/signal_tracker.py","utils/daily_risk_guard.py",
    "utils/signal_audit_log.py","utils/smart_position_sizer.py",
    "execution/order_tracker.py",
    "strategy/risk.py","strategy/smc.py","strategy/scoring.py",
    "strategy/v565_quality_gate.py",
    "decision/v37_gate.py",
    "final_forge/v56_5_stable_engine.py",
    "notifier/observer/funding.py","notifier/observer/signal_collector.py",
]
for f in key_files:
    p = Path(f)
    if p.exists():
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as e:
            errors.append(f"  FAIL {f}: 语法错误")
            print(f"  [FAIL] {f}: 语法错误")
    else:
        warn(f"  {f} 不存在(非必需)")

# 扫描全部 .py
all_py = sorted(Path(".").rglob("*.py"))
syntax_errs = 0
for f in all_py:
    if "__pycache__" in str(f) or ".git" in str(f):
        continue
    try:
        py_compile.compile(str(f), doraise=True)
    except py_compile.PyCompileError:
        syntax_errs += 1
        errors.append(f"  FAIL {f}: 语法错误")
        print(f"  [FAIL] {f}: 语法错误")
check(f"语法检查: {len(all_py)} 个 .py 文件", syntax_errs==0,
      f"{syntax_errs} 个错误" if syntax_errs else "全部通过")

# 4. 依赖库
print("\n--- 4. 依赖库 ---")
for lib_name in ["pandas","numpy","requests","asyncio","gradio"]:
    try:
        __import__(lib_name)
        check(f"  {lib_name}", True)
    except ImportError:
        check(f"  {lib_name}", False, "未安装")

# 关键模块导入
print("\n  项目内部模块导入:")
try:
    from config import STRATEGY_PARAMS; check("config.STRATEGY_PARAMS", True)
except Exception as e: check("config 导入", False, str(e))
try:
    from state.position_manager import position_manager; check("position_manager", position_manager is not None)
except Exception as e: check("position_manager", False, str(e))
try:
    from state.trade_journal import journal; check("trade_journal", journal is not None)
except Exception as e: check("trade_journal", False, str(e))
try:
    from strategy.risk import calculate_dynamic_tp_sl; check("risk.calculate_dynamic_tp_sl", True)
except Exception as e: check("risk", False, str(e))
try:
    from strategy.smc import build_macro_context, build_exec_context; check("smc", True)
except Exception as e: check("smc", False, str(e))
try:
    from notifier.telegram import send_telegram; check("telegram.send_telegram", send_telegram is not None)
except Exception as e: check("telegram", False, str(e))
try:
    from utils.daily_panel import get_daily_panel; check("daily_panel", True)
except Exception as e: check("daily_panel", False, str(e))

# 5. 目录
print("\n--- 5. 目录结构 ---")
for d in ["config","data","logs","state","reports","notifier/observer","execution"]:
    check(f"  {d}/", Path(d).exists())

# 6. API 连通性
print("\n--- 6. Bitget API 连通性 ---")
import requests as rq
for sym in ["BTCUSDT","ETHUSDT"]:
    try:
        resp = rq.get("https://api.bitget.com/api/v2/mix/market/candles",
                      params={"symbol":sym,"productType":"umcbl","granularity":"1m","limit":1},
                      timeout=15)
        if resp.status_code==200:
            data = resp.json()
            if data.get("code")=="00000" and data.get("data"):
                price = float(data["data"][0][4])
                check(f"  Bitget API {sym}", True, f"正常, 现价 {price:.2f}")
            else: check(f"  Bitget API {sym}", False, data.get("msg","?"))
        else: check(f"  Bitget API {sym}", False, f"HTTP {resp.status_code}")
    except Exception as e: check(f"  Bitget API {sym}", False, str(e))

# 7. 文件写入
print("\n--- 7. 文件写入权限 ---")
for d in ["data","logs","state"]:
    p = Path(d)/"_test_write.tmp"
    try:
        p.write_text("test", encoding="utf-8"); p.unlink()
        check(f"  {d}/ 可写", True)
    except Exception as e: check(f"  {d}/ 可写", False, str(e))

# 8. hf_auto_trader 关键函数
print("\n--- 8. hf_auto_trader 关键函数 ---")
try:
    import inspect, hf_auto_trader as hf
    check("scan_and_decide async", inspect.iscoroutinefunction(hf.scan_and_decide))
    check("main_loop async", inspect.iscoroutinefunction(hf.main_loop))
    check("check_and_open callable", callable(hf.check_and_open))
except Exception as e:
    check("hf_auto_trader 模块", False, str(e))

# 9. Binance 硬编码检查
print("\n--- 9. Bitget 硬编码引用检查 ---")
bitget_refs = 0
for f in ["hf_auto_trader.py","notifier/observer/funding.py"]:
    p=Path(f)
    if p.exists():
        code=p.read_text(encoding="utf-8")
        if "api.bitget.com" in code:
            bitget_refs += 1
if bitget_refs>0:
    warn(f"{bitget_refs} 个文件引用 Bitget API (api.bitget.com)",
         "如需 Binance 需要修改 API 端点")
else: check("无 Bitget 硬编码", True)

# 总结
print("\n"+"="*56)
print("[检查结果]")
print("="*56)
print(f"  通过: {len(passed)}")
print(f"  警告: {len(warnings)}")
print(f"  错误: {len(errors)}")
print()
if errors:
    print("需修复:")
    for e in errors: print(f"  {e}")
if warnings:
    print("注意:")
    for w in warnings: print(f"  {w}")
print()
if not errors:
    print("全部通过，可上线连接 API。")
else:
    print(f"有 {len(errors)} 个错误需修复。")
print("="*56)
