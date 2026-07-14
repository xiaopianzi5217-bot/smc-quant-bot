# -*- coding: utf-8 -*-
"""3处改动完成反馈闭环"""
with open('hf_auto_trader.py', 'r', encoding='utf-8') as f:
    text = f.read()

# ============= 改动1: scan_and_decide return 前加入 calibrator =============
old1 = '''    # ===== 【优化5 - Statistical EV】混合历史EV =====
    _blended_ev = get_statistical_ev().blend(model_ev=ev, features=_features)
    if _blended_ev != ev:
        print(f"[{symbol}] Statistical EV: model={ev:.4f} -> blended={_blended_ev:.4f}")

    # 构建兼容返回格式'''

new1 = '''    # ===== 【优化5 - Statistical EV】混合历史EV =====
    _blended_ev = get_statistical_ev().blend(model_ev=ev, features=_features)
    if _blended_ev != ev:
        print(f"[{symbol}] Statistical EV: model={ev:.4f} -> blended={_blended_ev:.4f}")

    # ===== 【闭环】ProbabilityCalibrator 校准评分 → Confidence =====
    _calibrated_prob = _calibrator.get_prob(score)
    _calibrated_prob = round(_calibrated_prob, 4)
    print(f"[{symbol}] ProbabilityCalibrator: score={score:.1f} -> confidence={_calibrated_prob:.3f}")

    # 构建兼容返回格式'''

if old1 in text:
    text = text.replace(old1, new1)
    print("改动1 OK")
else:
    print("改动1 FAILED: 字符串不匹配")
    # Debug
    idx = text.find('_blended_ev != ev')
    if idx > 0:
        print(repr(text[idx:idx+300]))

# ============= 改动2: scan_and_decide return dict 中加入 confidence =============
old2 = '''        "grade_result": None,  # check_and_open 中填充
        "feature_penalty": 0.0,  # check_and_open 中填充'''
new2 = '''        "confidence": _calibrated_prob,  # 【闭环】校准后的信心分数
        "grade_result": None,  # check_and_open 中填充
        "feature_penalty": 0.0,  # check_and_open 中填充'''

if old2 in text:
    text = text.replace(old2, new2)
    print("改动2 OK")
else:
    print("改动2 FAILED")
    idx = text.find('"grade_result"')
    if idx > 0:
        print(repr(text[idx:idx+200]))

# ============= 改动3: check_and_open 中加权分数影响决策 + Confidence拦截 + 存入open_score =============
old3 = '''    if _raw_feature_scores:
        _weighted_score = _weighter.get_weighted_score(_raw_feature_scores)
        print(f"[{symbol}] AdaptiveWeighter: 原始特征分数={_raw_feature_scores}, 加权后={_weighted_score:.2f}")
        # 加权分数写入V37 ctx（不影响主流程决策，仅做记录）
        result["weighted_score"] = _weighted_score'''

new3 = '''    if _raw_feature_scores:
        _weighted_score = _weighter.get_weighted_score(_raw_feature_scores)
        print(f"[{symbol}] AdaptiveWeighter: 原始特征分数={_raw_feature_scores}, 加权后={_weighted_score:.2f}")
        # 【闭环】AdaptiveWeighter 加权分数影响 30% 决策
        score = score * 0.7 + _weighted_score * 0.3
        print(f"[{symbol}] AdaptiveWeighter: 混合后 score={score:.1f}")
        result["score"] = score
        result["weighted_score"] = _weighted_score'''

if old3 in text:
    text = text.replace(old3, new3)
    print("改动3 OK")
else:
    print("改动3 FAILED")
    idx = text.find('_raw_feature_scores:')
    if idx > 0:
        print(repr(text[idx-50:idx+200]))

# ============= 改动4: Confidence 拦截（在ScoreGrade之后、V37 Gate之前加） =============
old4 = '''        print(f"[{symbol}] ScoreGrade 通过: score={score:.1f} ev={ev:.4f} grade={_grade_result['grade']}")
    
    # ===== 【优化4 - Feature Penalty】特征重叠惩罚 ====='''

new4 = '''        print(f"[{symbol}] ScoreGrade 通过: score={score:.1f} ev={ev:.4f} grade={_grade_result['grade']}")
    
    # ===== 【闭环】Confidence 拦截 =====
    _conf = result.get("confidence", 0.5)
    if _conf < 0.45:
        print(f"[{symbol}] Confidence={_conf:.3f}<0.45, 拒绝开单")
        return False
    if _conf > 0.65:
        # 高置信度放大仓位
        result["size"] = result.get("size", 0.05) * 1.5
        print(f"[{symbol}] Confidence={_conf:.3f}>0.65, 仓位放大1.5x")
    
    # ===== 【优化4 - Feature Penalty】特征重叠惩罚 ====='''

if old4 in text:
    text = text.replace(old4, new4)
    print("改动4 OK")
else:
    print("改动4 FAILED")
    idx = text.find('ScoreGrade 通过:')
    if idx > 0:
        print(repr(text[idx:idx+200]))

# ============= 改动5: position_manager.update 中加入 open_score =============
old5 = '''    position_manager.update(symbol, {
        "direction": direction,
        "entry": entry,
        "current_sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "stage": 0,
                "sl_hit": False,
        "last_sl_msg": "",
    })'''

new5 = '''    position_manager.update(symbol, {
        "direction": direction,
        "entry": entry,
        "current_sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "stage": 0,
        "sl_hit": False,
        "last_sl_msg": "",
        "open_score": score,  # 【闭环】用于平仓时回传 Calibrator
        "open_confidence": result.get("confidence", 0.5),  # 【闭环】
    })'''

if old5 in text:
    text = text.replace(old5, new5)
    print("改动5 OK")
else:
    print("改动5 FAILED")
    idx = text.find('"last_sl_msg"')
    if idx > 0:
        print(repr(text[idx-100:idx+100]))

# ============= 改动6: check_trailing 中平仓时更新 Calibrator =============
old6 = '''                # ===== 【新增20260723】SignalTracker outcome + DailyRiskGuard + AdaptiveWeighter =====
                try:
                    _ts_id = pos.get("tracker_signal_id", "")
                    if _ts_id:
                        _tracker.update_outcome(signal_id=_ts_id, final_r=profit_r2)
                    _risk_guard.on_trade_closed(r=profit_r2)
                    # AdaptiveFeatureWeighter：提取特征列表更新
                    _feat_list = []
                    if "OB" in str(pos.get("last_sl_msg", "")):
                        _feat_list.append("OB")
                    if profit_r2 > 0:
                        _feat_list.append("CHOCH")
                    if _feat_list:
                        _weighter.update(features=_feat_list, outcome_r=profit_r2)
                except Exception as _new_tools_e:
                    print(f"[NewTools] TP平仓更新异常: {_new_tools_e}")'''

new6 = '''                # ===== 【闭环】Calibrator + SignalTracker + RiskGuard + Weighter =====
                try:
                    _ts_id = pos.get("tracker_signal_id", "")
                    if _ts_id:
                        _tracker.update_outcome(signal_id=_ts_id, final_r=profit_r2)
                    _risk_guard.on_trade_closed(r=profit_r2)
                    # ProbabilityCalibrator 回传更新
                    _open_score = pos.get("open_score", 0)
                    if _open_score > 0:
                        _calibrator.update(score=_open_score, is_win=(profit_r2 > 0))
                    # AdaptiveFeatureWeighter：提取特征列表更新
                    _feat_list = []
                    if "OB" in str(pos.get("last_sl_msg", "")):
                        _feat_list.append("OB")
                    if profit_r2 > 0:
                        _feat_list.append("CHOCH")
                    if _feat_list:
                        _weighter.update(features=_feat_list, outcome_r=profit_r2)
                except Exception as _new_tools_e:
                    print(f"[NewTools] TP平仓更新异常: {_new_tools_e}")'''

if old6 in text:
    text = text.replace(old6, new6)
    print("改动6 OK")
else:
    print("改动6 FAILED")
    idx = text.find('SignalTracker outcome + DailyRiskGuard')
    if idx > 0:
        print(repr(text[idx-50:idx+300]))

# ============= 改动7: _trigger_stop_loss 中也加入 Calibrator 更新 =============
old7 = '''    # ===== 【新增20260723】止损时更新 SignalTracker / RiskGuard / Weighter =====
    try:
        _ts_id = pos.get("tracker_signal_id", "")
        if _ts_id:
            _tracker.update_outcome(signal_id=_ts_id, final_r=profit_r)
        _risk_guard.on_trade_closed(r=profit_r)
        # 止损特征学习：无论盈亏都记录
        _weighter.update(features=["CHOCH"], outcome_r=profit_r)
    except Exception as _sl_new_e:
        print(f"[NewTools] 止损更新异常: {_sl_new_e}")'''

new7 = '''    # ===== 【闭环】止损时更新 Calibrator / SignalTracker / RiskGuard / Weighter =====
    try:
        _ts_id = pos.get("tracker_signal_id", "")
        if _ts_id:
            _tracker.update_outcome(signal_id=_ts_id, final_r=profit_r)
        _risk_guard.on_trade_closed(r=profit_r)
        # ProbabilityCalibrator 回传更新（止损无论盈亏都记录）
        _open_score = pos.get("open_score", 0)
        if _open_score > 0:
            _calibrator.update(score=_open_score, is_win=(profit_r > 0))
        # 止损特征学习：无论盈亏都记录
        _weighter.update(features=["CHOCH"], outcome_r=profit_r)
    except Exception as _sl_new_e:
        print(f"[NewTools] 止损更新异常: {_sl_new_e}")'''

if old7 in text:
    text = text.replace(old7, new7)
    print("改动7 OK")
else:
    print("改动7 FAILED")
    idx = text.find('止损时更新 SignalTracker / RiskGuard / Weighter')
    if idx > 0:
        print(repr(text[idx-50:idx+200]))

# ============= 写入 =============
with open('hf_auto_trader.py', 'w', encoding='utf-8') as f:
    f.write(text)

import py_compile
try:
    py_compile.compile('hf_auto_trader.py', doraise=True)
    print("\n✅ 所有改动完成，语法OK！")
except py_compile.PyCompileError as e:
    print(f"\n❌ 语法错误: {e}")
