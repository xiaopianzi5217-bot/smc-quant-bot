# -*- coding: utf-8 -*-
import inspect
from state.trade_journal import TradeJournal
from feature_store import FeatureStore

# --- TradeJournal.open_trade 签名 ---
sig_open = inspect.signature(TradeJournal.open_trade)
print('=== TradeJournal.open_trade 参数 ===')
for name, p in sig_open.parameters.items():
    if name == 'self': continue
    d = '(required)' if p.default is inspect.Parameter.empty else f'(default={p.default})'
    print(f'  {name}: {d}')

print()

# --- TradeJournal.close_trade 签名 ---
sig_close = inspect.signature(TradeJournal.close_trade)
print('=== TradeJournal.close_trade 参数 ===')
for name, p in sig_close.parameters.items():
    if name == 'self': continue
    d = '(required)' if p.default is inspect.Parameter.empty else f'(default={p.default})'
    print(f'  {name}: {d}')

print()

# --- FeatureStore.save_trade 签名 ---
sig_save = inspect.signature(FeatureStore.save_trade)
print('=== FeatureStore.save_trade 参数 ===')
for name, p in sig_save.parameters.items():
    if name == 'self': continue
    d = '(required)' if p.default is inspect.Parameter.empty else f'(default={p.default})'
    print(f'  {name}: {d}')
