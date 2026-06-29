# Institutional Restructure Patch

## What changed

This patch converts the backtest path into a single-core decision flow:

```text
data/features -> InstitutionalDecisionKernel -> PreTradeRiskEngine -> execution simulation -> reports
```

## New modules

- `core/decision_kernel.py`
  - Owns the single authoritative `InstitutionalDecisionKernel.decide()` entry point.
  - Wraps the V37 master signal/risk engine through core-owned decision logic.
  - Moves Alpha Cluster Guard out of `backtest/runner.py` and into the decision core.

- `core/alpha_master_engine.py`
  - Moved from `backtest/v37_master_engine.py` so backtest no longer owns strategy decision logic.
  - `backtest/v37_master_engine.py` remains as a compatibility wrapper for old imports.

- `core/risk_engine.py`
  - Owns deterministic pre-trade execution-cost compression/rejection.
  - Keeps the previous cost math, but removes it from the runner so backtest is no longer a second decision engine.

## Modified modules

- `backtest/runner.py`
  - Uses `InstitutionalDecisionKernel.from_kwargs()` instead of directly instantiating `V37MasterEngine`.
  - No longer runs Alpha Cluster Guard inline.
  - Uses `PreTradeRiskEngine` for cost gating.
  - Keeps the original entry resolution, TP/SL normalization, trade exit simulation, and reporting compatibility.

- `decision/decision_kernel.py`
  - Exposes `InstitutionalDecisionKernel` for discovery while preserving the old V9-compatible `DecisionKernel` signature.

- `scripts/smoke_check.py`
  - Skips accidental nested duplicate repositories and broken scratch test files during compilation.

## Cleanup performed before packaging

- Removed nested duplicate `SMC_Bot/SMC_Bot` repository copy.
- Removed `.git` metadata and `__pycache__` bytecode caches.
- Removed broken scratch file `_test_final.py`.

## Validation performed

- Python syntax compilation for runtime project files.
- Direct import of `backtest.runner` and new core modules.
- Quick backtest on bundled BTCUSDT data with `max_rows=350`.
