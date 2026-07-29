import pandas as pd

def generate_table():
    df = pd.read_csv('data/backtest_trades.csv')
    
    # 1. 先打印所有列名，确保我们没写错
    print("当前 CSV 中的列名有：")
    print(df.columns.tolist())
    print("-" * 30)

    # 2. 这里是关键：请根据上面打印出的列名，手动替换下面列表里的字段
    # 如果你的 CSV 里叫 'setup_type' 而不是 'smc_state'，就改这里
    # 如果你的 CSV 里叫 'div_dir' 而不是 'div_state'，就改这里
    group_cols = ['regime', 'setup_type', 'direction'] # 比如这里，请根据上面的打印结果调整
    
    try:
        stats = df.groupby(group_cols)['pnl_r'].mean()
        print("=== 自动生成的期望收益表 (Expectancy Table) ===")
        print(stats)
    except KeyError as e:
        print(f"\n错误：找不到对应的列 {e}。请检查上面的列名列表，修改 group_cols 中的字段名。")

if __name__ == "__main__":
    generate_table()