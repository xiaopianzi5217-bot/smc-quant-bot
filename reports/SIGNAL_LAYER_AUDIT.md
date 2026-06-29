# Signal Layer Audit / Refactor Notes

## Current target architecture

- Observer layer: sends only market-structure changes. It never approves or recommends a trade.
- Strategy layer: sends only executable opportunities after the decision kernel approves the signal.
- Execution layer: sends only position/order lifecycle events such as position opened, reduced, closed, TP/SL, cooldown, sizing block, portfolio block.

## Findings before this patch

1. The SQZMOM/SMC source-derived logic was still present:
   - `indicators/basic.py` computes `xtl_val`, `sz`, `lowsqz`, `midsqz`, `highsqz`.
   - `strategy/smc.py` computes BSL/SSL, sweep, OB/FVG, XTL color state, and SQZMOM divergence using `sz` pivots.
2. Notification dispatch was only partially layered:
   - Observer and open-signal routes existed, but the boundary was not explicit enough.
   - `app.py` dry-run still contained simplified Telegram pushing paths.
   - Execution notifications were sent as generic text, not as a controlled Execution-layer lifecycle event.
3. `runner/v7_live_runner.py` imported `send_telegram_message`, but the actual module exposes `send_telegram`.

## Changes in this patch

1. Added `notifier/layers.py` as the single place for layer rules.
2. Rewrote `notifier/manager.py`:
   - `dispatch_observer_snapshot()` only sends structural changes.
   - `dispatch_strategy_decision()` only sends when the central decision approves.
   - `dispatch_execution_event()` only sends allowed lifecycle events.
3. Updated `notifier/observer/signal_formatter.py` with explicit layer labels.
4. Updated `notifier/observer/realtime_scanner.py`:
   - `scan_symbol_observer_direct()` => Observer layer.
   - `scan_symbol_strategy_signal()` => Strategy layer.
   - `scan_symbol_open_via_center()` kept as backward-compatible Strategy alias.
5. Updated `app.py`:
   - Dry-run no longer directly pushes Telegram messages.
   - Signal Tools tab now has Observer / Strategy / Execution buttons.
6. Updated Execution:
   - `execution/live_engine.py` emits `POSITION_OPENED` through Execution layer.
   - `execution/lifecycle_manager.py` emits TP/SL/position lifecycle events through Execution layer.
7. Fixed `runner/v7_live_runner.py` Telegram import to `send_telegram`.

## Remaining engineering improvements recommended

1. Add de-duplication per layer, for example `(layer, symbol, timeframe, event_type, candle_time)`.
2. Make `near_buyside/near_sellside` threshold configurable by symbol/timeframe.
3. Add closed-candle enforcement in realtime scanner before building snapshots to avoid repaint.
4. Persist raw snapshots and decisions to JSONL for post-trade review.
5. Add unit tests for:
   - XTL color transition.
   - SQZMOM squeeze dot mapping.
   - BSL/SSL proximity and sweep.
   - Strategy approval gating.
   - Execution layer rejecting non-lifecycle events.
6. Remove old duplicate module folder `notifier/notifier/` if it is not used by deployment.
