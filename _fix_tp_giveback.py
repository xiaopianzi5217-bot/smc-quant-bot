# Fix TP giveback_ratio
with open('hf_auto_trader.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the TP block: "V38.1: TP 到达时更新特征"
marker = "# V38.1: TP 到达时更新特征"
if marker not in content:
    print("ERROR: TP marker not found")
    exit(1)

# Insert mfe_val/giveback computation before feature_store.save_trade
old = """            # V38.1: TP 到达时更新特征
            try:
                profit_r = (new_sl - pos["entry"]) / pos["entry"]
                if pos["direction"] == "Short":
                    profit_r = (pos["entry"] - new_sl) / pos["entry"]
                feature_store.save_trade({
                    "symbol": symbol,
                    "direction": pos["direction"],
                    "exit_reason": stage_label,
                    "pnl_r": profit_r,
                    "mfe": pos.get("mfe", 0),
                    "mae": pos.get("mae", 0),
                    "max_r": pos.get("max_r", 0),
                })"""

new = """            # V38.1: TP 到达时更新特征（含giveback_ratio）
            try:
                profit_r = (new_sl - pos["entry"]) / pos["entry"]
                if pos["direction"] == "Short":
                    profit_r = (pos["entry"] - new_sl) / pos["entry"]
                mfe_val = pos.get("mfe", 0)
                giveback = 0.0
                if mfe_val > 0:
                    giveback = abs((mfe_val - profit_r) / mfe_val)
                feature_store.save_trade({
                    "symbol": symbol,
                    "direction": pos["direction"],
                    "exit_reason": stage_label,
                    "pnl_r": profit_r,
                    "mfe": mfe_val,
                    "mae": pos.get("mae", 0),
                    "max_r": pos.get("max_r", 0),
                    "giveback_ratio": round(giveback, 4),
                })"""

if old in content:
    content = content.replace(old, new)
    with open('hf_auto_trader.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("TP giveback_ratio applied successfully")
else:
    print("ERROR: TP block not found")
    print("Looking for exact match...")
    # Show what's around the marker
    idx = content.find(marker)
    if idx >= 0:
        print(content[idx:idx+600])
