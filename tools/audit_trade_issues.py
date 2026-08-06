"""排查四大问题的实际数据审计脚本"""
import pandas as pd
import re
import sqlite3
import os
from pathlib import Path

print("=" * 80)
print("问题 1 & 2 & 3 & 4 联合审计")
print("=" * 80)

# ============================================================
# 1. 检查 trade_journal.csv 中的实际交易数据
# ============================================================
if os.path.exists("logs/trade_journal.csv"):
    df = pd.read_csv("logs/trade_journal.csv")
    print(f"\n📊 交易总数: {len(df)}")
    print(f"\n=== 状态分布 ===")
    print(df["status"].value_counts().to_string())
    print(f"\n=== exit_reason 分布 ===")
    print(df["exit_reason"].fillna("OPEN").value_counts().to_string())

    # SL 交易分析
    sl_trades = df[df["exit_reason"] == "SL"]
    tp_trades = df[df["exit_reason"].fillna("").str.startswith("TP")]
    print(f"\n=== SL 次数: {len(sl_trades)} | TP 次数: {len(tp_trades)}")

    if len(sl_trades) > 0:
        print("\n=== SL 交易详情 (前5笔) ===")
        for _, r in sl_trades.head(5).iterrows():
            print(f"  {r['order_id']} | {r['direction']} | "
                  f"entry={r['open_price']:.2f} | sl={r['sl']:.2f} | "
                  f"close={r['close_price']:.2f} | "
                  f"pnl={r['pnl_r']:.2f}R | "
                  f"mae={r.get('mae_r', 'N/A')} | "
                  f"regime={r['regime']} | {r.get('note', '')[:80]}")

        print(f"\n=== 止损单 MAE 统计 ===")
        mae = sl_trades["mae_r"].dropna()
        if len(mae) > 0:
            print(f"  mean={mae.mean():.3f}R, median={mae.median():.3f}R, "
                  f"max={mae.max():.3f}R, min={mae.min():.3f}R")

        # 检查是否有 "刚打掉止损就反转" 的现象
        # 如果 MAE 接近 1.0 但 pnl 也是负的，说明恰好打了止损
        if "mae_r" in sl_trades.columns:
            near_sl = sl_trades[sl_trades["mae_r"] < 1.2]
            if len(near_sl) > 0:
                print(f"\n⚠️  有 {len(near_sl)} 笔止损单 MAE < 1.2R（刚扫损就反转）")
                rev_cnt = 0
                for _, r in near_sl.iterrows():
                    note = str(r.get("note", ""))
                    if "rev" in note.lower() or "reversal" in note.lower():
                        rev_cnt += 1
                print(f"   其中 {rev_cnt} 笔标注了反转")

    # regime 分析
    print(f"\n=== 所有交易 regime 分布 ===")
    print(df["regime"].value_counts().to_string())

    # ADX 分析
    adx_vals = []
    vol_vals = []
    for n in df["note"].fillna(""):
        m = re.search(r"adx=([\d.]+)", n)
        if m:
            adx_vals.append(float(m.group(1)))
        v = re.search(r"vol_ratio=([\d.]+)", n)
        if v:
            vol_vals.append(float(v.group(1)))

    if adx_vals:
        print(f"\n=== ADX 分析 ===")
        print(f"  count={len(adx_vals)}, mean={sum(adx_vals)/len(adx_vals):.1f}, "
              f"min={min(adx_vals):.1f}, max={max(adx_vals):.1f}")
        low_adx = [a for a in adx_vals if a < 20]
        print(f"  ADX < 20 比例: {len(low_adx)/len(adx_vals)*100:.1f}% ({len(low_adx)}/{len(adx_vals)})")
        very_low_adx = [a for a in adx_vals if a < 12]
        print(f"  ADX < 12 比例: {len(very_low_adx)/len(adx_vals)*100:.1f}% ({len(very_low_adx)}/{len(adx_vals)})")

    if vol_vals:
        print(f"\n=== Volume Ratio 分析 ===")
        print(f"  count={len(vol_vals)}, mean={sum(vol_vals)/len(vol_vals):.2f}, "
              f"min={min(vol_vals):.2f}, max={max(vol_vals):.2f}")
        low_vol = [v for v in vol_vals if v < 0.65]
        print(f"  vol < 0.65 比例: {len(low_vol)/len(vol_vals)*100:.1f}% ({len(low_vol)}/{len(vol_vals)})")

    # RR 分析
    if "rr" in df.columns:
        print(f"\n=== RR 分布 ===")
        print(df["rr"].describe().to_string())

    # SL 距离 (ATR 倍数) 分析
    if "open_price" in df.columns and "sl" in df.columns and "note" in df.columns:
        atr_vals = []
        for n in df["note"].fillna(""):
            m = re.search(r"atr=([\d.]+)", n)
            if m:
                atr_vals.append(float(m.group(1)))
        if len(atr_vals) == len(df) and atr_vals[0] > 0:
            df["sl_dist_atr"] = abs(df["open_price"] - df["sl"]) / atr_vals
            print(f"\n=== SL 距离 (ATR 倍数) ===")
            print(df["sl_dist_atr"].describe().to_string())
            print(f"\n⚠️  如果 SL 距离主要 < 0.5 ATR，说明止损设置过紧")

else:
    print("❌ 未找到 logs/trade_journal.csv")

# ============================================================
# 2. 检查 v6_research.db
# ============================================================
print("\n" + "=" * 80)
print("检查 v6_research.db 数据库")
print("=" * 80)
db_path = Path("data/v6_research.db")
if db_path.exists():
    print(f"数据库大小: {db_path.stat().st_size} bytes")
    if db_path.stat().st_size == 0:
        print("⚠️  数据库文件是空的！说明系统从未实际记录过交易快照")
    else:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cur.fetchall()
        print(f"表: {tables}")

        for t in tables:
            tname = t[0]
            try:
                cur.execute(f"SELECT COUNT(*) FROM {tname}")
                cnt = cur.fetchone()[0]
                print(f"  {tname}: {cnt} 行")
            except Exception as e:
                print(f"  {tname}: 查询失败 - {e}")

        # 检查 smc_structure_tracker
        if any(t[0] == "smc_structure_tracker" for t in tables):
            cur.execute("SELECT COUNT(*) FROM smc_structure_tracker")
            cnt = cur.fetchone()[0]
            if cnt == 0:
                print("\n⚠️  smc_structure_tracker 表为空！")
                print("   → SMC 结构历史胜率从未被记录")
                print("   → get_historical_smc_success_rate() 永远返回默认值 0.48")
                print("   → 结构质量评分缺少统计支撑")
        conn.close()
else:
    print("❌ 未找到 data/v6_research.db")

# ============================================================
# 3. 检查 smc_structure_tracker 是否有任何写入代码
# ============================================================
print("\n" + "=" * 80)
print("检查 smc_structure_tracker 写入逻辑")
print("=" * 80)
found_write = False
for root, dirs, files in os.walk("."):
    if ".git" in root or "__pycache__" in root or "node_modules" in root:
        continue
    for f in files:
        if f.endswith(".py"):
            path = os.path.join(root, f)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    content = fh.read()
                if "INSERT INTO smc_structure_tracker" in content or \
                   "INSERT OR REPLACE INTO smc_structure_tracker" in content or \
                   "UPDATE smc_structure_tracker" in content:
                    print(f"  ⚠️  找到写入逻辑: {path}")
                    found_write = True
            except Exception:
                pass

if not found_write:
    print("❌ 未找到任何对 smc_structure_tracker 的写入逻辑！")
    print("   → 表只创建，从未填充数据")
    print("   → get_historical_smc_success_rate() 永远查不到数据，返回 0.48 默认值")
    print("   → smc_module_v2 和 smc_impulse_engine 中的历史胜率映射永远不会生效")

# ============================================================
# 4. 检查 HTF 过滤是否有效
# ============================================================
print("\n" + "=" * 80)
print("检查 HTF (1H) Regime Filter 生效情况")
print("=" * 80)
if os.path.exists("logs/trade_journal.csv"):
    df = pd.read_csv("logs/trade_journal.csv")
    if "regime" in df.columns:
        print("\n=== 实盘 trades 的 regime 分布 ===")
        for r, cnt in df["regime"].value_counts().items():
            print(f"  {r}: {cnt}")
        
        # 看是否有 mud/transition 中的交易
        weak = df[df["regime"].astype(str).str.contains("mud|transition|RANGE", case=False, na=False)]
        if len(weak) > 0:
            print(f"\n⚠️  有 {len(weak)} 笔交易在弱趋势/震荡环境中开仓:")
            print(f"  占全部交易比例: {len(weak)/len(df)*100:.1f}%")

print("\n" + "=" * 80)
print("审计完成")
print("=" * 80)