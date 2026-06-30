import pandas as pd
import json
import requests
from datetime import datetime, timezone

def analyze_signals(csv_file='filter_audit.csv'):
    print("開始讀取信號並連線幣安獲取 K 線數據...")
    df = pd.read_csv(csv_file)
    # 過濾出被放行的信號
    approved = df[df['state'] == 'APPROVED'].copy()
    
    trades = []
    for _, row in approved.iterrows():
        try:
            j = json.loads(row['raw_json'])
            rp = j.get('risk_plan', {})
            trades.append({
                'time': row['timestamp'],
                'direction': rp.get('direction'),
                'entry': rp.get('entry'),
                'sl': rp.get('sl'),
                'tp1': rp.get('tp1'),
                'rr': rp.get('rr')
            })
        except:
            continue
            
    if not trades:
        print("沒有找到 APPROVED 的信號。")
        return
        
    # 獲取最早信號的時間並轉換為 UTC Timestamp (毫秒)
    start_time_str = trades[0]['time']
    start_ts = int(datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
    
    # 向幣安請求從該時間點開始的 1000 根 1 分鐘 K 線 (約 16 個小時的走勢)
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime={start_ts}&limit=1000"
    res = requests.get(url)
    klines = res.json()
    
    # 將 K 線整理成易於查詢的字典列表
    kd = [{'time': k[0], 'high': float(k[2]), 'low': float(k[3])} for k in klines]
    
    print("\n--- 逐筆信號模擬結果 ---")
    results = []
    for t in trades:
        # 將信號時間轉為 Timestamp，過濾出信號發生之後的 K 線
        t_ts = int(datetime.strptime(t['time'], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
        future_k = [k for k in kd if k['time'] >= t_ts]
        
        outcome = "PENDING (未觸發)"
        profit_r = 0
        for k in future_k:
            if t['direction'] == 'Short': # 觸發空頭信號 (看跌)
                if k['high'] >= t['sl']:
                    outcome = "止損 (SL)"
                    profit_r = -1
                    break
                elif k['low'] <= t['tp1']:
                    outcome = "止盈 (TP1)"
                    profit_r = t['rr']
                    break
            elif t['direction'] == 'Long': # 觸發多頭信號
                if k['low'] <= t['sl']:
                    outcome = "止損 (SL)"
                    profit_r = -1
                    break
                elif k['high'] >= t['tp1']:
                    outcome = "止盈 (TP1)"
                    profit_r = t['rr']
                    break
                    
        t['outcome'] = outcome
        t['profit_r'] = profit_r
        results.append(t)
        
        print(f"時間: {t['time']} | 方向: {t['direction']} | 入場: {t['entry']:.1f} | TP1: {t['tp1']:.1f} | SL: {t['sl']:.1f} => 結果: {outcome}")
        
    # 計算統計數據
    wins = [r for r in results if r['outcome'] == '止盈 (TP1)']
    losses = [r for r in results if r['outcome'] == '止損 (SL)']
    
    gross_profit = sum(r['profit_r'] for r in wins)
    gross_loss = abs(sum(r['profit_r'] for r in losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    win_rate = len(wins) / len(results) if results else 0
    
    print("\n--- 總結 (Summary) ---")
    print(f"總放行信號 (APPROVED): {len(results)} 單")
    print(f"打到止盈 (Wins): {len(wins)} 單")
    print(f"打到止損 (Losses): {len(losses)} 單")
    print(f"勝率 (Win Rate): {win_rate:.2%}")
    print(f"盈利因子 (Profit Factor): {pf:.2f}")

if __name__ == "__main__":
    analyze_signals()