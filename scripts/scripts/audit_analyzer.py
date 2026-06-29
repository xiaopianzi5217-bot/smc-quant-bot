# -*- coding: utf-8 -*-
"""
SMC BOT - Strategy Filter Audit Analyzer
用途：自动分析过滤日志，找出导致错失好单或者频繁死卡的“罪魁祸首”规则。
运行方式: python audit_analyzer.py
"""
import pandas as pd
from pathlib import Path

def analyze_audit_log(file_path="state/strategy_filter_audit.csv"):
    p = Path(file_path)
    if not p.exists():
        print(f"❌ 找不到审计日志文件: {file_path}")
        print("请确保 SMC-BOT 已经运行并产生了开仓/过滤日志。")
        return
        
    try:
        df = pd.read_csv(p)
    except Exception as e:
        print(f"❌ 读取 CSV 失败: {e}")
        return
        
    total_signals = len(df)
    passed_signals = len(df[df['allowed'] == True])
    blocked_signals = total_signals - passed_signals
    pass_rate = (passed_signals / total_signals) * 100 if total_signals > 0 else 0
    
    print("="*50)
    print(" 📊 SMC-BOT 策略审计复盘报告")
    print("="*50)
    print(f"总计扫描信号 : {total_signals}")
    print(f"成功放行信号 : {passed_signals} (放行率: {pass_rate:.2f}%)")
    print(f"拦截垃圾信号 : {blocked_signals}")
    print("-" * 50)
    
    if blocked_signals > 0:
        print("🛑 导致信号被拦截的 Top 5 原因:")
        blocked_df = df[df['allowed'] == False]
        
        # 因为 reasons 是用 '|' 分隔的，我们需要把它们拆开统计
        all_reasons = []
        for r_str in blocked_df['reasons'].dropna():
            for r in str(r_str).split('|'):
                # 提取出前面的主干部分，比如把 "处于筹码密集区..." 提出来，忽略后面的数值
                core_reason = r.split(':')[0] if ':' in r else r
                core_reason = core_reason.split('=')[0] if '=' in core_reason else core_reason
                all_reasons.append(core_reason.strip())
                
        reason_counts = pd.Series(all_reasons).value_counts()
        for idx, (reason, count) in enumerate(reason_counts.head(5).items(), 1):
            pct = (count / blocked_signals) * 100
            print(f" {idx}. {reason} -> 触发了 {count} 次 ({pct:.1f}%)")
            
        print("-" * 50)
        print("💡 调优建议:")
        top_reason = reason_counts.index[0]
        if "1H大級別看" in top_reason:
            print(" 👉 你的 15m 信号经常与 1H 大周期背离，说明当前市场处于宽幅震荡。建议休息，或在 config.py 中放宽多空双开。")
        elif "ATR" in top_reason or "极端波动" in top_reason:
            print(" 👉 市场当前波动率极大（可能是新闻导致），BOT 成功为你躲过了插针风险。保持当前设置。")
        elif "成交量" in top_reason or "筹码密集区" in top_reason:
            print(" 👉 经常在成交密集区被卡死，说明你喜欢在盘整区尝试突破单。如果你觉得漏单太多，请去 trade_filters.py 稍微降低 min_volume_ratio。")
            
    print("="*50)

if __name__ == "__main__":
    analyze_audit_log()
