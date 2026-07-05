import sys; sys.path.insert(0,'.')
from runner.v11_institutional_runner import run_once
from ops.env_config import load_runtime_config
cfg = load_runtime_config('config/v11_full_config.json')
results = run_once(cfg)
for r in results:
    dec = r.get('decision',{})
    d = dec.get('direction','?')
    le = dec.get('long_ev', 0)
    se = dec.get('short_ev', 0)
    rr = dec.get('rr_calculated', dec.get('rr', 0))
    ls = dec.get('long_score', 0)
    ss = dec.get('short_score', 0)
    print(f"  {r['symbol']:>12} | appr={r['approved']} | dir={d} | RR={rr:.2f} | EV(L)={le:.4f} EV(S)={se:.4f} | score={ls:.1f}/{ss:.1f} | state={r.get('state','?')}")
    # 也打印诊断行中的 EV
