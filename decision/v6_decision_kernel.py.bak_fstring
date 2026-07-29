# -*- coding: utf-8 -*-
from decision.v6_param_loader import load_v6_params
from decision.v6_regime_gate import V6RegimeGate
from decision.v6_signal_gate import V6SignalGate
from decision.v6_priority_engine import V6PriorityEngine
from observer.v6_signal_observer import V6SignalObserver
from risk.v6_risk_engine import V6RiskEngine
from execution.v6_execution_guard import V6ExecutionGuard

class V6DecisionKernel:
    """v6 统一总入口：实盘、回测、TG观察都只能从这里出信号。"""
    def __init__(self, param_path=None):
        self.params=load_v6_params(param_path)
        self.regime_gate=V6RegimeGate(self.params.get('regime_gate',{}))
        self.signal_gate=V6SignalGate()
        self.priority=V6PriorityEngine(self.params.get('priority_weights',{}), self.params.get('signal_class',{}))
        self.observer=V6SignalObserver(self.params.get('regime_gate',{}))
        self.risk=V6RiskEngine(self.params.get('risk',{}))
        self.execution_guard=V6ExecutionGuard(self.params.get('regime_gate',{}))

    def decide(self, curr, macro_ctx, exec_ctx, long_score, long_threshold, long_reasons, short_score, short_threshold, short_reasons, symbol='BTC/USDT:USDT', timeframe='15m', equity=None, recent_trades=None, bar_index=None):
        regime=self.regime_gate.evaluate(macro_ctx, exec_ctx)
        observer_items=self.observer.collect(curr, exec_ctx)

        # 所有提醒都带 TP/SL：按多空各生成一个观察计划
        observer_plans=[]
        if observer_items:
            if macro_ctx.get('allowed_direction','Both') in ['Long','Both']:
                observer_plans.append(self.risk.build_observer_plan('Long', curr, exec_ctx))
            if macro_ctx.get('allowed_direction','Both') in ['Short','Both']:
                observer_plans.append(self.risk.build_observer_plan('Short', curr, exec_ctx))

        if not regime['allowed']:
            return {'approved':False,'state':'BLOCKED','reason_cn':'行情过滤阻止开单','regime':regime,'observer_items':observer_items,'observer_plans':observer_plans,'candidates':[],'primary':None,'risk_plan':None}

        candidates=self.signal_gate.collect_candidates(macro_ctx, exec_ctx, long_score, short_score, long_threshold, short_threshold, long_reasons, short_reasons)
        ranked=self.priority.rank(candidates, observer_items, regime)
        primary=self.priority.choose_primary(ranked)

        if regime.get('observe_only'):
            return {'approved':False,'state':'OBSERVE','reason_cn':'行情只允许观察，不允许开单','regime':regime,'observer_items':observer_items,'observer_plans':observer_plans,'candidates':ranked,'primary':primary,'risk_plan':None}
        if primary is None:
            return {'approved':False,'state':'OBSERVE','reason_cn':'没有达到开单级别的信号','regime':regime,'observer_items':observer_items,'observer_plans':observer_plans,'candidates':ranked,'primary':None,'risk_plan':None}

        exe=self.execution_guard.check(curr, primary['direction'], recent_trades or [], bar_index)
        if not exe['allowed']:
            return {'approved':False,'state':'REJECTED','reason_cn':exe['reason_cn'],'regime':regime,'observer_items':observer_items,'observer_plans':observer_plans,'candidates':ranked,'primary':primary,'risk_plan':None,'execution':exe}

        risk_plan=self.risk.build_plan(primary['direction'], curr, exec_ctx, equity=equity, level=primary.get('level','A'))
        if risk_plan.get('position') and not risk_plan['position'].get('allowed', True):
            return {'approved':False,'state':'REJECTED','reason_cn':risk_plan['position'].get('reason_cn'),'regime':regime,'observer_items':observer_items,'observer_plans':observer_plans,'candidates':ranked,'primary':primary,'risk_plan':risk_plan,'execution':exe}

        return {'approved':True,'state':'APPROVED','reason_cn':'通过v6统一决策内核','regime':regime,'observer_items':observer_items,'observer_plans':observer_plans,'candidates':ranked,'primary':primary,'risk_plan':risk_plan,'execution':exe}
