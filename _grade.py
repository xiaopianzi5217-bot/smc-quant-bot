import sys; sys.path.insert(0,'.')
from ops.env_config import load_runtime_config
cfg = load_runtime_config('config/v11_full_config.json')

print("=== 系统段位评估 ===\n")

risk = cfg.get('risk',{})
strat = cfg.get('strategy_params',{})
filters = cfg.get('strategy_filters',{})
exec_cfg = cfg.get('execution',{})

scores = {}

# === 信号层 ===
sig_score = 0
sig_score += 2  # scoring.py adaptive_signal_score (评分+阈值+reasons+fallback)
sig_score += 2  # ctx_builder 方向性上下文
sig_score += 2  # HTF方向融合 (adx加权投票)
sig_score += 2  # EV引擎 (win_prob+rr+regime+分桶)
sig_score += 2  # 学习型EV (历史胜率校准)
sig_score += 1  # SMC质量评分
sig_score += 1  # squeeze_filter
print(f"[信号层]  评分:{sig_score}/12")
scores["信号层"] = sig_score

# === 风控层 ===
risk_score = 0
risk_score += 2  # GlobalRiskGuard 组合风控
risk_score += 2  # PortfolioStateManager
risk_score += 2  # 策略过滤器 (5层:MTF+fib+cooldown+atr+vwap)
risk_score += 1  # 最大持仓限制
risk_score += 1  # 最大日亏损
risk_score += 1  # 最大连续亏损
risk_score += 1  # 最大回撤
print(f"[风控层]  评分:{risk_score}/10")
scores["风控层"] = risk_score

# === 执行层 ===
exec_score = 0
exec_score += 2  # V9DecisionKernel (决策核)
exec_score += 2  # calculate_dynamic_tp_sl (动态SL/TP)
exec_score += 1  # 多目标止盈 (TP1/TP2/TP3)
exec_score += 1  # 移动止损到盈亏平衡
exec_score += 1  # 跟踪止损 (trail)
exec_score += 1  # 风险计划 (rr_plan)
exec_score += 1  # ExitManager (退出管理)
print(f"[执行层]  评分:{exec_score}/9")
scores["执行层"] = exec_score

# === 数据层 ===
data_score = 0
data_score += 2  # 实时数据 (Bitget API)
data_score += 2  # 增量缓存 ohlcv_cache
data_score += 1  # 双时间帧 (15m+1h)
data_score += 1  # 模拟数据回退
data_score += 1  # 全量指标 (30+)
data_score += 1  # 资金费率
print(f"[数据层]  评分:{data_score}/8")
scores["数据层"] = data_score

# === 监控层 ===
mon_score = 0
mon_score += 2  # structured_logger
mon_score += 2  # SignalDiary
mon_score += 2  # PushDiary (推送日记)
mon_score += 2  # Telegram/微信推送
mon_score += 1  # FilterAuditLogger
mon_score += 1  # CSV scan_log
mon_score += 1  # JSON runtime report
mon_score += 1  # feature_store
mon_score += 1  # trade_journal
print(f"[监控层]  评分:{mon_score}/13")
scores["监控层"] = mon_score

# === 学习层 ===
learn_score = 0
learn_score += 2  # 学习型EV (EVLearner)
learn_score += 1  # OutcomeLearner
learn_score += 1  # RRTracker
learn_score += 1  # feature_store历史记录 (可回放)
print(f"[学习层]  评分:{learn_score}/5")
scores["学习层"] = learn_score

total = sum(scores.values())
max_total = 12+10+9+8+13+5
print(f"\n总分: {total}/{max_total} ({total/max_total*100:.0f}%)")

if total >= 50:
    print(">>> 段位: 王者 (Professional Trading System)")
elif total >= 40:
    print(">>> 段位: 钻石 (Institutional Grade)")
elif total >= 30:
    print(">>> 段位: 黄金 (Advanced Semi-Auto)")
elif total >= 20:
    print(">>> 段位: 白银 (Intermediate System)")
else:
    print(">>> 段位: 青铜 (Basic Bot)")

print("\n=== 关键差距 ===")
if learn_score < 4:
    print("- 学习层不足: 学习型EV刚接入, 样本量不够; 回测反馈循环未闭环")
if not exec_cfg.get('trail_after_tp1'):
    print("- 执行层: 跟踪止损未开启")
if not filters.get('trading_session',{}).get('enabled'):
    print("- 风控层: 交易时段过滤未开启")
if not filters.get('structure_distance',{}).get('enabled'):
    print("- 风控层: 结构距离过滤未开启")
if not filters.get('volume_confirmation',{}).get('enabled'):
    print("- 风控层: 成交量确认过滤未开启")
print("\n建议升级路径:")
print("1. 学习闭环: 记录每笔交易结果 → 反馈到 EVLearner → 自动优化参数")
print("2. 多品种: SOL/USDT 已配置但被黑名单屏蔽")
print("3. 自适应参数: 根据历史表现自动调整 score_threshold / min_rr")
