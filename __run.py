import sys; sys.path.insert(0,'.')
from runner.v11_institutional_runner import run_once
from ops.env_config import load_runtime_config
cfg = load_runtime_config('config/v11_full_config.json')
results = run_once(cfg)
for r in results:
    dec = r.get('decision',{})
    print(f"  {r['symbol']:>10} | appr={r['approved']} | dir={dec.get('direction','?'):>5} | RR={dec.get('rr_calculated',dec.get('rr',0)):.2f}")
print("OK")
