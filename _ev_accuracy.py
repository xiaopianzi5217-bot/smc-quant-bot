"""EV 精准度评估：回放 + 对比预测 vs 实际"""
import sys; sys.path.insert(0,'.')
import time, json, pandas as pd
import numpy as np
from pathlib import Path
from indicators.basic import add_all_indicators
from strategy.smc import build_macro_context, build_exec_context
from strategy.scoring import adaptive_signal_score
from strategy.risk import calculate_dynamic_tp_sl
from strategy.trade_filters import check_strategy_filters
from strategy.intelligence_engine import estimate_expected_value, ev_learner
from notifier.observer.risk_plan import build_rr_plan
from config import SYMBOL_STRATEGY
from utils.symbols import load_symbol_strategy
from utils.ctx_builder import _enrich_common_fields, build_directional_contexts
from decision.v9_decision_kernel import V9DecisionKernel
from backtest.v11_backtest_engine import BacktestPosition, CONFIG_PATH, DEFAULT_15M_CSV, DEFAULT_1H_CSV

# 加载数据
with open(CONFIG_PATH,"r",encoding="utf-8") as f: cfg=json.load(f)
df_exec=pd.read_csv(DEFAULT_15M_CSV); df_macro=pd.read_csv(DEFAULT_1H_CSV)
df_exec["datetime"]=pd.to_datetime(df_exec["datetime"])
df_macro["datetime"]=pd.to_datetime(pd.to_numeric(df_macro["ts"],errors="coerce"),unit="ms")
df_exec=df_exec.sort_values("datetime").reset_index(drop=True)
df_macro=df_macro.sort_values("datetime").reset_index(drop=True)

wvf=cfg.get("strategy_params",{}).get("wvf_std_mult",2.0)
df_exec=add_all_indicators(df_exec,wvf); df_macro=add_all_indicators(df_macro,wvf)
smc=build_exec_context(df_exec)

# 1H regime 列表
print("预计算 regime 列表...", flush=True)
regime_1h = []
for i in range(len(df_macro)):
    ms = df_macro.iloc[:i+1]
    if len(ms)>=20:
        from strategy.regime import detect_market_regime
        try: regime_1h.append(detect_market_regime(ms).get("regime","unknown"))
        except: regime_1h.append("unknown")
    else: regime_1h.append("unknown")

t_1h = df_macro["datetime"].values.astype(np.int64)
t_15m = df_exec["datetime"].values.astype(np.int64)
idx_map = np.clip(np.searchsorted(t_1h, t_15m, side="right")-1, 0, len(regime_1h)-1)

# 回测 & 记录 EV
positions=[]; trades=[]; SCAN_STEP=4; scans=0
ev_records = []  # (predicted_ev, actual_r, regime, setup)

for i in range(500, len(df_exec)):
    if i%10000==0: print(f"{i}/{len(df_exec)} scans={scans} trades={len(trades)}", flush=True)
    cb=df_exec.iloc[i]; h,l,cc=float(cb["high"]),float(cb["low"]),float(cb["close"]); ts=int(cb.get("timestamp",i))
    for p in positions[:]:
        if p.exit_reason!="OPEN": continue
        if p.update(h,l,cc):
            tr=p.to_dict(); tr["exit_time"]=ts; trades.append(tr)
            # 记录 EV 预测 vs 实际
            if hasattr(p, '_ev_record') and p._ev_record:
                ev_records.append({**p._ev_record, 'actual_r': p.pnl_r or 0})
            positions.remove(p)
    if i%SCAN_STEP!=0: continue
    if len(positions)>=2: continue
    macro_idx = int(idx_map[i])
    regime_str = regime_1h[macro_idx] if macro_idx < len(regime_1h) else "unknown"
    macro_ctx = {"allowed_direction":"Both","regime":regime_str,"vol_state":"NORMAL_VOL"}
    ctx=dict(smc); ctx["close"]=float(cb["close"]); ri=ctx.get("regime_info",{})
    try:
        p_=float(cb["close"]); av=df_exec["volume"].tail(20).mean(); vr=float(cb["volume"]/av) if av>0 else 0.0; is_v=vr>1.5
        _enrich_common_fields(ctx,cb,macro_ctx); lc,sc=build_directional_contexts(ctx,cb)
        ls,lt,lr=adaptive_signal_score(lc,macro_ctx,"Long",is_v); ss,st,sr=adaptive_signal_score(sc,macro_ctx,"Short",is_v)
        ha=str(macro_ctx.get("allowed_direction","Both")).strip(); hx=float(ctx.get("adx",0))
        hw=min(0.9,max(0.3,hx/45.0)) if hx>=18 else max(0.1,hx/40.0); se=abs(ls-ss); mw=min(0.9,max(0.3,se/30.0))
        hv=-1 if ha=="Short" else(1 if ha=="Long" else 0); mv=1 if ls>=ss else -1
        if ha=="Both" and se<5: d_="Long" if ls>=ss else "Short"
        else: tv=hv*hw+mv*mw; d_="Long" if tv>0.3 else("Short" if tv<-0.3 else("Long" if ls>=ss else "Short"))
        ss_=load_symbol_strategy("BTC/USDT",SYMBOL_STRATEGY); mr=ss_.get("min_rr",cfg.get("risk",{}).get("min_rr",2.0))
        sl,tp1,tp2,tp3,rr=calculate_dynamic_tp_sl(d_,cb,df_exec,ctx,mr,ss_)
        rs_=str(ri.get("regime",regime_str)); vs_=str(macro_ctx.get("vol_state","NORMAL_VOL"))
        lev=estimate_expected_value({"score_raw":ls,"score":ls,"smc":float(lr.get("smc",0)) if isinstance(lr,dict) else 0,"direction":"Long","entry_meta":lc,"estimated_rr":rr},rs_,vs_,lc).get("expected_value",0.0)
        sev=estimate_expected_value({"score_raw":ss,"score":ss,"smc":float(sr.get("smc",0)) if isinstance(sr,dict) else 0,"direction":"Short","entry_meta":sc,"estimated_rr":rr},rs_,vs_,sc).get("expected_value",0.0)
        kn=V9DecisionKernel(params=cfg)
        dc=kn.decide(curr=cb,macro_ctx=macro_ctx,exec_ctx=ctx,long_score=ls,long_threshold=lt,long_reasons=lr,short_score=ss,short_threshold=st,short_reasons=sr,symbol="BTC/USDT",cfg=cfg,min_rr=cfg.get("risk",{}).get("min_rr",2.0),rr=rr,direction=d_,entry=p_,sl=sl,tp1=tp1,tp2=tp2,tp3=tp3,long_ev=lev,short_ev=sev)
        dc["risk_plan"]=build_rr_plan(d_,p_,sl,tp1,tp2,tp3); dc["entry"]=p_; dc["stop_loss"]=sl; dc["score"]=ls if d_=="Long" else ss
        if dc.get("approved"):
            fr=check_strategy_filters({"symbol":"BTC/USDT","curr":cb,"macro_ctx":macro_ctx,"exec_ctx":ctx,"decision":dc,"cfg":cfg})
            if not fr.get("approved",fr.get("allowed",False)): dc["approved"]=False; dc["state"]="STRATEGY_FILTER_BLOCKED"
        if dc.get("approved"):
            # 开关：只记录 EV 但不实际开仓（避免影响已有持仓）
            # 这里获取预测 EV
            pred_ev = lev if d_ == "Long" else sev
            pos = BacktestPosition(entry_time=ts,entry_price=p_,direction=d_,sl=sl,tp1=tp1,tp2=tp2,tp3=tp3,rr=rr,score=ls if d_=="Long" else ss,regime=rs_ if rs_ else "unknown")
            pos._ev_record = {'pred_ev': pred_ev, 'regime': rs_, 'setup': 'V37_CORE', 'direction': d_, 'score': ls if d_=='Long' else ss}
            positions.append(pos)
        scans+=1
    except Exception as e:
        print(f"E {i}: {e}", flush=True)

# 平仓
lb=df_exec.iloc[-1]; lc_=float(lb["close"])
for p in positions:
    p.close(lc_,"END_OF_BACKTEST")
    tr=p.to_dict()
    if hasattr(p, '_ev_record') and p._ev_record:
        ev_records.append({**p._ev_record, 'actual_r': p.pnl_r or 0})

print(f"\n完成: {len(trades)+len(positions)} 笔交易, {len(ev_records)} 条 EV 记录", flush=True)

# === EV 精准度分析 ===
if ev_records:
    df_ev = pd.DataFrame(ev_records)
    df_ev = df_ev[df_ev['actual_r'].notna()].copy()
    df_ev['r_bucket'] = pd.cut(df_ev['actual_r'], 
        bins=[-float('inf'), -1.5, -0.8, -0.1, 0.1, 0.8, 1.5, float('inf')],
        labels=['LOSS_BIG', 'LOSS_MED', 'LOSS_SMALL', 'FLAT', 'WIN_SMALL', 'WIN_MED', 'WIN_BIG'])
    df_ev['ev_bucket'] = pd.cut(df_ev['pred_ev'],
        bins=[-float('inf'), -0.05, 0, 0.05, 0.15, float('inf')],
        labels=['NEG_EV', 'ZERO_EV', 'LOW_EV', 'MID_EV', 'HIGH_EV'])
    
    print(f"\n{'='*60}")
    print(f"  EV 精准度评估")
    print(f"{'='*60}")
    print(f"总样本: {len(df_ev)} 笔交易")
    
    # 1. 整体相关性
    corr = df_ev['pred_ev'].corr(df_ev['actual_r'])
    print(f"\n1. 预测 EV vs 实际 R 的相关系数: {corr:.4f}")
    print(f"   > 0.30 = 好, 0.20~0.30 = 可接受, <0.20 = 弱")
    
    # 2. EV 分桶的实际胜率
    print(f"\n2. EV 分桶实际表现:")
    print(f"   {'EV 桶':>12s} {'样本':>6s} {'胜率':>8s} {'平均R':>8s}")
    print(f"   {'-'*36}")
    for bucket in ['NEG_EV', 'ZERO_EV', 'LOW_EV', 'MID_EV', 'HIGH_EV']:
        subset = df_ev[df_ev['ev_bucket'] == bucket]
        if len(subset) > 0:
            wr = (subset['actual_r'] > 0).mean() * 100
            avg_r = subset['actual_r'].mean()
            print(f"   {bucket:>12s} {len(subset):>6d} {wr:>7.1f}% {avg_r:>8.4f}")
    
    # 3. 等级胜率
    print(f"\n3. 各 regime 胜率:")
    for regime in df_ev['regime'].unique():
        subset = df_ev[df_ev['regime'] == regime]
        wr = (subset['actual_r'] > 0).mean() * 100
        avg_ev = subset['pred_ev'].mean()
        avg_r = subset['actual_r'].mean()
        print(f"   {regime:>12s}: {len(subset):>4d} 笔, 胜率 {wr:>5.1f}%, avg_ev={avg_ev:>.4f}, avg_r={avg_r:>.4f}")
    
    # 4. 校准曲线
    print(f"\n4. EV 校准误差:")
    df_ev['ev_decile'] = pd.qcut(df_ev['pred_ev'], q=5, labels=False, duplicates='drop')
    mae_by_decile = []
    for d in sorted(df_ev['ev_decile'].unique()):
        subset = df_ev[df_ev['ev_decile'] == d]
        avg_pred = subset['pred_ev'].mean()
        avg_actual = subset['actual_r'].mean()
        error = avg_pred - avg_actual
        mae_by_decile.append(error)
        print(f'   分位 {d}: pred={avg_pred:.4f}, actual={avg_actual:.4f}, 误差={error:+.4f}')
    
    # 5. 综合 MAE/RMSE
    mae = abs(df_ev['pred_ev'] - df_ev['actual_r']).mean()
    rmse = np.sqrt(((df_ev['pred_ev'] - df_ev['actual_r'])**2).mean())
    print(f"\n5. 整体误差指标:")
    print(f"   MAE (平均绝对误差): {mae:.4f}")
    print(f"   RMSE (均方根误差):  {rmse:.4f}")
    print(f"   avgR 范围: {df_ev['actual_r'].min():.2f} ~ {df_ev['actual_r'].max():.2f}")
    print(f"   EV 范围:   {df_ev['pred_ev'].min():.4f} ~ {df_ev['pred_ev'].max():.4f}")
    
    # 6. 结论
    print(f"\n{'='*60}")
    print(f"  结论:")
    corr_grade = "优秀" if corr > 0.30 else ("可接受" if corr > 0.20 else "弱" if corr > 0.10 else "差")
    print(f"  - 相关系数 {corr:.3f} → {corr_grade}")
    print(f"  - MAE={mae:.4f} (avgR≈{df_ev['actual_r'].mean():.4f}, 相对误差 {mae/abs(df_ev['actual_r'].mean())*100:.0f}%)")
    print(f"  - EV 排序能力: {'✅ HIGH_EV 桶胜率显著高于 NEG_EV' if len(df_ev[df_ev['ev_bucket']=='HIGH_EV']) > 0 and len(df_ev[df_ev['ev_bucket']=='NEG_EV']) > 0 and (df_ev[df_ev['ev_bucket']=='HIGH_EV']['actual_r'] > 0).mean() > (df_ev[df_ev['ev_bucket']=='NEG_EV']['actual_r'] > 0).mean() else '❌ 排序能力不足'}")
else:
    print("无交易数据")
