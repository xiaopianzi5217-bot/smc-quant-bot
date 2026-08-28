"""
使用回测数据训练EV模型并输出评估报告
"""
import pandas as pd
import numpy as np
import sqlite3
import json
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
from sklearn.preprocessing import StandardScaler
import joblib
import os

# 1. 连接数据库加载数据
conn = sqlite3.connect('data/ev_training.db')
df = pd.read_sql_query("SELECT * FROM ev_samples", conn)
conn.close()

print(f"加载 {len(df)} 条样本")

# 2. 解析特征JSON
features_df = df['features'].apply(json.loads).apply(pd.Series)
print(f"\n特征列: {list(features_df.columns)}")

# 3. 构建X (特征) 和 y (目标)
# 目标1: 预测能否盈利 (pnl_r > 0)
# 模型输入: 特征向量 + 环境标签

# 合并特征与辅助信息
X = features_df.copy()

# 添加方向、环境等分类变量
X['direction'] = df['direction'].map({'long': 1, 'short': 0, 'LONG': 1, 'SHORT': 0, 'Long': 1, 'Short': 0}).fillna(0)
X['regime_trend'] = (df['regime'] == 'trend').astype(int)
X['regime_range'] = (df['regime'] == 'range').astype(int)
X['regime_mixed'] = (df['regime'] == 'mixed').astype(int)

# 添加entry级别信息
X['tier'] = df['tier'].fillna(0)
X['gate_passed'] = df['gate_passed'].fillna(1)
X['win_prob'] = df['win_prob'].fillna(0.5)
X['estimated_rr'] = df['estimated_rr'].fillna(0)
X['expected_value'] = df['expected_value'].fillna(0)

# 空缺值处理
X = X.fillna(0)

# 目标变量
# 目标1: 是否盈利
# 目标2: 预测EV值 (回归)
y_profit = (df['pnl_r'] > 0).astype(int)
y_ev = df['realized_ev'].fillna(0)
y_pnl_r = df['pnl_r'].fillna(0)

print(f"\n=== 数据概况 ===")
print(f"胜率 (pnl_r > 0): {y_profit.mean():.4f}")
print(f"平均EV: {y_ev.mean():.4f}")
print(f"特征维度: {X.shape}")

# 4. 划分训练/测试集 (按时间顺序)
train_ratio = 0.8
train_size = int(len(df) * train_ratio)

X_train, X_test = X.iloc[:train_size], X.iloc[train_size:]
y_train, y_test = y_profit.iloc[:train_size], y_profit.iloc[train_size:]
y_ev_train, y_ev_test = y_ev.iloc[:train_size], y_ev.iloc[train_size:]

y_pnl_train, y_pnl_test = y_pnl_r.iloc[:train_size], y_pnl_r.iloc[train_size:]

print(f"\n训练集: {len(X_train)} 样本, 测试集: {len(X_test)} 样本")

# 5. 训练盈利预测模型
print("\n=== 训练盈利预测模型 (随机森林) ===")

model_profit = RandomForestClassifier(
    n_estimators=300,
    max_depth=6,
    min_samples_split=15,
    min_samples_leaf=5,
    random_state=42,
    n_jobs=-1
)
model_profit.fit(X_train, y_train)

# 测试集评估
y_pred = model_profit.predict(X_test)
acc = accuracy_score(y_test, y_pred)
prec = precision_score(y_test, y_pred, zero_division=0)
rec = recall_score(y_test, y_pred)
f1 = f1_score(y_test, y_pred, zero_division=0)

print(f"准确率: {acc:.4f}")
print(f"精确率: {prec:.4f}")
print(f"召回率: {rec:.4f}")
print(f"F1分数: {f1:.4f}")

# 基线对比
baseline_acc = max(y_test.mean(), 1 - y_test.mean())
print(f"\n基线准确率 (多数类预测): {baseline_acc:.4f}")
print(f"提升: {acc - baseline_acc:.4f}")

# 6. 训练EV值回归模型
print("\n=== 训练EV回归模型 (梯度提升) ===")

from sklearn.ensemble import GradientBoostingRegressor

model_ev = GradientBoostingRegressor(
    n_estimators=200,
    max_depth=4,
    learning_rate=0.05,
    random_state=42
)
model_ev.fit(X_train, y_ev_train)

# 评估
from sklearn.metrics import mean_squared_error, r2_score

y_ev_pred = model_ev.predict(X_test)
rmse = np.sqrt(mean_squared_error(y_ev_test, y_ev_pred))
r2 = r2_score(y_ev_test, y_ev_pred)

print(f"RMSE: {rmse:.4f}")
print(f"R²: {r2:.4f}")

# 7. 特征重要性分析
print("\n=== 特征重要性分析 (盈利预测模型) ===")
importance = dict(zip(X.columns, model_profit.feature_importances_))
sorted_imp = sorted(importance.items(), key=lambda x: -x[1])

for feat, imp in sorted_imp[:15]:
    print(f"  {feat}: {imp:.4f}")

# 8. 分析预测与回报关系
print("\n=== 预测概率与平均R值关系 ===")

# 使用训练好的模型预测全部数据的概率
for prob_threshold in [0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7]:
    try:
        test_proba = model_profit.predict_proba(X_test)[:, 1]
        mask = test_proba >= prob_threshold
        if mask.sum() > 0:
            subset_pnl = y_pnl_test[mask]
            avg_pnl = subset_pnl.mean()
            win_rate = (subset_pnl > 0).mean()
            n_count = len(subset_pnl)
            print(f"  阈值 ≥{prob_threshold:.2f}: n={n_count}, 平均R={avg_pnl:.4f}, 胜率={win_rate:.3f}")
    except Exception as e:
        print(f"  threshold {prob_threshold}: {e}")

# 9. 保存模型
print("\n=== 保存模型 ===")
os.makedirs('models', exist_ok=True)
joblib.dump(model_profit, 'models/ev_profit_model.pkl')
joblib.dump(model_ev, 'models/ev_value_model.pkl')

# 保存特征列名
feature_cols = X.columns.tolist()
with open('models/ev_feature_cols.json', 'w') as f:
    import json as j
    j.dump(feature_cols, f)

print("✅ 模型已保存:")
print("  - models/ev_profit_model.pkl (盈利分类器)")
print("  - models/ev_value_model.pkl (EV回归器)")
print("  - models/ev_feature_cols.json (特征列名)")

# 10. 生成样本外评估
print("\n=== 样本外评估 (最后20%数据) ===")
print(f"测试集: {len(y_test)} 笔交易")

# 选择高概率信号
test_proba = model_profit.predict_proba(X_test)[:, 1]
for threshold in [0.5, 0.55, 0.6, 0.65]:
    selected = test_proba >= threshold
    if selected.sum() > 0:
        selected_pnl = y_pnl_test[selected]
        print(f"  阈值 {threshold:.2f}: 选择 {selected.sum()} 笔, 平均R={selected_pnl.mean():.4f}, 胜率={(selected_pnl>0).mean():.3f}")
