"""
优化版EV模型训练 - 解决数据不平衡和时间序列问题
"""
import pandas as pd
import numpy as np
import sqlite3
import json
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier, GradientBoostingRegressor
from sklearn.model_selection import StratifiedKFold, train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score
from sklearn.preprocessing import StandardScaler
import joblib
import os
import warnings
warnings.filterwarnings('ignore')

# ========== 1. 加载数据 ==========
conn = sqlite3.connect('data/ev_training.db')
df = pd.read_sql_query("SELECT * FROM ev_samples", conn)
conn.close()

print(f"加载 {len(df)} 条样本")

# 清理数据 - 移除异常的realized_ev
# v56_production的realized_ev为nan，用pnl_r填充
df['realized_ev'] = df['realized_ev'].fillna(df['pnl_r'])

# ========== 2. 解析特征 ==========
features_df = df['features'].apply(json.loads).apply(pd.Series)

# 构建完整特征矩阵
X = features_df.copy()

# 添加基础分类特征
X['direction'] = df['direction'].map({'long': 1, 'short': 0, 'LONG': 1, 'SHORT': 0, 'Long': 1, 'Short': 0}).fillna(0)
X['regime_trend'] = (df['regime'] == 'trend').astype(int)
X['regime_range'] = (df['regime'] == 'range').astype(int)
X['regime_mixed'] = (df['regime'] == 'mixed').astype(int)
X['tier'] = df['tier'].fillna(0)
X['gate_passed'] = df['gate_passed'].fillna(1)
X['win_prob'] = df['win_prob'].fillna(0.5)
X['estimated_rr'] = df['estimated_rr'].fillna(0)
X['expected_value'] = df['expected_value'].fillna(0)
X['bucket_ev'] = df['bucket_ev'].fillna(0)

# 填充缺失值
X = X.fillna(0)

# ========== 3. 目标变量 ==========
y_profit = (df['pnl_r'] > 0).astype(int)
y_ev = df['realized_ev'].fillna(0)

print(f"\n=== 数据概况 ===")
print(f"特征维度: {X.shape}")
print(f"整体胜率: {y_profit.mean():.4f}")
print(f"平均pnl_r: {df['pnl_r'].mean():.4f}")
print(f"平均EV: {y_ev.mean():.4f}")

# ========== 4. 分层交叉验证（解决数据不平衡） ==========
print("\n=== 使用分层K折交叉验证 ===")

skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

acc_scores = []
prec_scores = []
rec_scores = []
f1_scores = []
roc_scores = []

# 收集所有预测结果
all_y_true = []
all_y_pred = []
all_y_proba = []
all_pnl_r = []

for fold, (train_idx, test_idx) in enumerate(skf.split(X, y_profit)):
    X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
    y_train, y_test = y_profit.iloc[train_idx], y_profit.iloc[test_idx]
    pnl_test = df['pnl_r'].iloc[test_idx].values
    
    # 训练模型
    model = RandomForestClassifier(
        n_estimators=250,
        max_depth=5,
        min_samples_split=15,
        min_samples_leaf=5,
        max_features='sqrt',
        class_weight='balanced',
        random_state=42,
        n_jobs=-1
    )
    model.fit(X_train, y_train)
    
    # 预测
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]
    
    # 记录
    all_y_true.extend(y_test.tolist())
    all_y_pred.extend(y_pred.tolist())
    all_y_proba.extend(y_proba.tolist())
    all_pnl_r.extend(pnl_test.tolist())
    
    # 计算指标
    acc = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred, zero_division=0)
    rec = recall_score(y_test, y_pred, zero_division=0)
    f1 = f1_score(y_test, y_pred, zero_division=0)
    roc = roc_auc_score(y_test, y_proba)
    
    acc_scores.append(acc)
    prec_scores.append(prec)
    rec_scores.append(rec)
    f1_scores.append(f1)
    roc_scores.append(roc)
    
    print(f"Fold {fold+1}: acc={acc:.4f}, prec={prec:.4f}, rec={rec:.4f}, f1={f1:.4f}, roc={roc:.4f}")

print(f"\n=== 平均性能 ===")
print(f"准确率: {np.mean(acc_scores):.4f} ± {np.std(acc_scores):.4f}")
print(f"精确率: {np.mean(prec_scores):.4f}")
print(f"召回率: {np.mean(rec_scores):.4f}")
print(f"F1分数: {np.mean(f1_scores):.4f}")
print(f"AUC: {np.mean(roc_scores):.4f}")

# ========== 5. 概率阈值与收益关系 ==========
print("\n=== 预测概率与平均R值关系 (基于全部样本) ===")

all_y_true = np.array(all_y_true)
all_y_proba = np.array(all_y_proba)
all_pnl_r = np.array(all_pnl_r)

for threshold in [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]:
    mask = all_y_proba >= threshold
    if mask.sum() > 0:
        subset_pnl = all_pnl_r[mask]
        print(f"  阈值 ≥{threshold:.2f}: n={len(subset_pnl)}, 平均R={subset_pnl.mean():.4f}, 胜率={(subset_pnl>0).mean():.3f}")

# ========== 6. 训练最终模型（全量数据） ==========
print("\n=== 训练最终模型（全量数据） ===")

final_model = RandomForestClassifier(
    n_estimators=300,
    max_depth=6,
    min_samples_split=10,
    min_samples_leaf=5,
    max_features='sqrt',
    class_weight='balanced',
    random_state=42,
    n_jobs=-1
)
final_model.fit(X, y_profit)

# ========== 7. 训练EV回归模型 ==========
print("\n=== 训练EV回归模型 ===")

ev_model = GradientBoostingRegressor(
    n_estimators=150,
    max_depth=4,
    learning_rate=0.05,
    min_samples_leaf=10,
    random_state=42
)
ev_model.fit(X, y_ev)

# 评估回归模型（交叉验证）
from sklearn.model_selection import KFold
from sklearn.metrics import mean_squared_error, r2_score

kf = KFold(n_splits=5, shuffle=True, random_state=42)
rmse_scores = []
r2_scores = []

for train_idx, test_idx in kf.split(X):
    X_tr, X_te = X.iloc[train_idx], X.iloc[test_idx]
    y_tr, y_te = y_ev.iloc[train_idx], y_ev.iloc[test_idx]
    
    m = GradientBoostingRegressor(
        n_estimators=100, max_depth=3, learning_rate=0.05,
        random_state=42
    )
    m.fit(X_tr, y_tr)
    pred = m.predict(X_te)
    rmse_scores.append(np.sqrt(mean_squared_error(y_te, pred)))
    r2_scores.append(r2_score(y_te, pred))

print(f"EV回归 RMSE: {np.mean(rmse_scores):.4f} ± {np.std(rmse_scores):.4f}")
print(f"EV回归 R²: {np.mean(r2_scores):.4f}")

# ========== 8. 特征重要性 ==========
print("\n=== 特征重要性分析 ===")
importance = dict(zip(X.columns, final_model.feature_importances_))
sorted_imp = sorted(importance.items(), key=lambda x: -x[1])

for feat, imp in sorted_imp[:15]:
    print(f"  {feat}: {imp:.4f}")

# ========== 9. 保存模型 ==========
print("\n=== 保存模型 ===")
os.makedirs('models', exist_ok=True)

joblib.dump(final_model, 'models/ev_profit_model.pkl')
joblib.dump(ev_model, 'models/ev_value_model.pkl')

# 保存特征列名和元数据
feature_cols = X.columns.tolist()
metadata = {
    'feature_cols': feature_cols,
    'trained_samples': len(df),
    'baseline_win_rate': float(y_profit.mean()),
    'avg_pnl_r': float(df['pnl_r'].mean()),
    'avg_ev': float(y_ev.mean()),
    'model_type': 'random_forest',
    'features_used': 'engineered_v2'
}

with open('models/ev_model_metadata.json', 'w') as f:
    json.dump(metadata, f, indent=2)

print("\n✅ 模型已保存:")
print("  - models/ev_profit_model.pkl (盈利分类器)")
print("  - models/ev_value_model.pkl (EV回归器)")
print("  - models/ev_model_metadata.json (元数据)")

# ========== 10. 生成交易决策建议 ==========
print("\n=== 模型应用建议 ===")

# 找到最优阈值
print("基于交叉验证的盈利信号建议:")
for threshold in [0.5, 0.55, 0.6, 0.65]:
    mask = all_y_proba >= threshold
    if mask.sum() > 0:
        pnl = all_pnl_r[mask]
        exp = pnl.mean()
        n_trades = len(pnl)
        if exp > 0:
            print(f"  ✅ 建议使用阈值 {threshold:.2f}: 预期R={exp:.4f}, 样本量={n_trades}")
        else:
            print(f"  ⚠️  阈值 {threshold:.2f}: 预期R={exp:.4f} (不建议), 样本量={n_trades}")

# 找出最佳EV回归分界
print("\nEV回归预测值与实际R值关系:")
ev_pred = ev_model.predict(X)
for ev_threshold in [0.1, 0.3, 0.5, 0.7, 1.0]:
    mask = ev_pred >= ev_threshold
    if mask.sum() > 5:
        pnl = df['pnl_r'].iloc[np.where(mask)[0]]
        print(f"  EV≥{ev_threshold:.1f}: n={len(pnl)}, 平均R={pnl.mean():.4f}, 胜率={(pnl>0).mean():.3f}")
