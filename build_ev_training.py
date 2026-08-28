"""
将回测CSV转换为EV训练数据 + 训练EV模型
使用现有的 backtest_v56_5.csv / stable / production 三个文件
"""
import pandas as pd
import numpy as np
import sqlite3
import json
import os
from datetime import datetime

class BacktestToEV:
    def __init__(self, db_path="data/ev_training.db"):
        self.conn = sqlite3.connect(db_path)
        self.init_db()
    
    def init_db(self):
        """初始化EV训练数据库"""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS ev_samples (
                id TEXT PRIMARY KEY,
                source TEXT,
                datetime TEXT,
                direction TEXT,
                setup_type TEXT,
                entry REAL,
                exit_price REAL,
                sl REAL,
                tp1 REAL,
                tp2 REAL,
                tp3 REAL,
                features TEXT,
                regime TEXT,
                exit_reason TEXT,
                pnl_r REAL,
                realized_ev REAL,
                win_prob REAL,
                estimated_rr REAL,
                expected_value REAL,
                bars_held INTEGER,
                gate_passed INTEGER,
                v56_5_stable INTEGER,
                tier INTEGER,
                bucket_ev REAL
            )
        """)
        self.conn.commit()
    
    def process_csv(self, csv_path, source):
        """处理单个回测CSV"""
        df = pd.read_csv(csv_path)
        print(f"处理 {source}: {len(df)} 条记录")
        
        # 定义特征列（用于训练）
        feature_cols = [
            'rsi', 'trend_strength', 'vol_z', 'body_pct', 'hour', 'dow',
            'tier', 'regime_factor', 'session_factor', 'win_prob_model',
            'expected_rr_model', 'model_ev', 'decision_score', 'bucket_ev',
            'cluster_score', 'size_scale'
        ]
        
        count = 0
        for idx, row in df.iterrows():
            # 构建ID
            record_id = f"{source}_{idx}"
            
            # 提取特征
            features = {}
            for col in feature_cols:
                if col in df.columns:
                    val = row[col]
                    features[col] = float(val) if pd.notna(val) else None
            
            # 额外特征
            features['score'] = float(row['score']) if 'score' in df.columns and pd.notna(row['score']) else None
            features['rank_score'] = float(row['rank_score']) if 'rank_score' in df.columns and pd.notna(row['rank_score']) else None
            
            # 入场特征
            entry_features = {}
            if 'reasons' in df.columns:
                entry_features['reasons'] = str(row['reasons'])
            
            features_json = json.dumps(features)
            
            # 处理布尔值
            gate_passed = int(row['gate_passed']) if 'gate_passed' in df.columns and pd.notna(row['gate_passed']) else 1
            stable_flag = int(row['v56_5_stable']) if 'v56_5_stable' in df.columns and pd.notna(row['v56_5_stable']) else 0
            
            # 插入记录
            self.conn.execute("""
                INSERT OR REPLACE INTO ev_samples 
                (id, source, datetime, direction, setup_type, entry, exit_price,
                 sl, tp1, tp2, tp3, features, regime, exit_reason, pnl_r,
                 realized_ev, win_prob, estimated_rr, expected_value, bars_held,
                 gate_passed, v56_5_stable, tier, bucket_ev)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record_id, source,
                str(row.get('datetime', '')), 
                str(row.get('direction', '')),
                str(row.get('setup_type', '')),
                float(row['entry']) if pd.notna(row.get('entry')) else None,
                float(row['exit_price']) if pd.notna(row.get('exit_price')) else None,
                float(row['initial_sl']) if pd.notna(row.get('initial_sl')) else None,
                float(row['tp1']) if pd.notna(row.get('tp1')) else None,
                float(row['tp2']) if pd.notna(row.get('tp2')) else None,
                float(row['tp3']) if pd.notna(row.get('tp3')) else None,
                features_json,
                str(row.get('regime', '')),
                str(row.get('exit_reason', '')),
                float(row['pnl_r']) if pd.notna(row.get('pnl_r')) else None,
                float(row['realized_ev']) if pd.notna(row.get('realized_ev')) else None,
                float(row['win_prob']) if pd.notna(row.get('win_prob')) else None,
                float(row['estimated_rr']) if pd.notna(row.get('estimated_rr')) else None,
                float(row['expected_value']) if pd.notna(row.get('expected_value')) else None,
                int(row['bars_held']) if pd.notna(row.get('bars_held')) else None,
                gate_passed,
                stable_flag,
                int(row['tier']) if pd.notna(row.get('tier')) else None,
                float(row['bucket_ev']) if pd.notna(row.get('bucket_ev')) else None
            ))
            count += 1
        
        self.conn.commit()
        print(f"  ✅ {source}: 导入 {count} 条")
        return count
    
    def process_all(self):
        """处理所有回测文件"""
        total = 0
        files = {
            'data/backtest_v56_5.csv': 'v56_5',
            'data/backtest_v56_5_stable.csv': 'v56_5_stable',
            'data/backtest_v56_production.csv': 'v56_production'
        }
        
        for path, source in files.items():
            if os.path.exists(path):
                total += self.process_csv(path, source)
            else:
                print(f"⚠️  文件不存在: {path}")
        
        print(f"\n📊 总计导入 {total} 条样本")
        
        # 输出统计
        self.print_stats()
    
    def print_stats(self):
        """打印数据库统计"""
        stats = self.conn.execute("""
            SELECT 
                COUNT(*) as total,
                ROUND(AVG(pnl_r), 4) as avg_pnl,
                ROUND(AVG(realized_ev), 4) as avg_ev,
                ROUND(SUM(CASE WHEN pnl_r > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*), 4) as win_rate
            FROM ev_samples
        """).fetchone()
        
        print(f"\n=== EV训练数据统计 ===")
        print(f"总样本数: {stats[0]}")
        print(f"平均pnl_r: {stats[1]}")
        print(f"平均realized_ev: {stats[2]}")
        print(f"胜率: {stats[3]}")
        
        # 按regime分布
        regime_stats = self.conn.execute("""
            SELECT regime, COUNT(*) as cnt, 
                   ROUND(AVG(pnl_r), 4) as avg_pnl,
                   ROUND(SUM(CASE WHEN pnl_r > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*), 4) as win_rate
            FROM ev_samples
            GROUP BY regime
        """).fetchall()
        
        print("\n按环境分布:")
        for regime, cnt, avg_pnl, wr in regime_stats:
            print(f"  {regime}: {cnt} 样本, 平均R={avg_pnl:.4f}, 胜率={wr:.3f}")


if __name__ == "__main__":
    builder = BacktestToEV()
    builder.process_all()
