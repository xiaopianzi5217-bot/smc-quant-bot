# -*- coding: utf-8 -*-

def _to_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def calc_rr(direction, entry, sl, tp):
    entry = _to_float(entry)
    sl = _to_float(sl)
    tp = _to_float(tp)
    if entry is None or sl is None or tp is None:
        return "N/A"
    risk = abs(entry - sl)
    if risk <= 0:
        return "N/A"
    reward = tp - entry if direction == "Long" else entry - tp
    return round(reward / risk, 2)


def build_rr_plan(direction, entry, sl, tp1, tp2, tp3):
    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "rr1": calc_rr(direction, entry, sl, tp1),
        "rr2": calc_rr(direction, entry, sl, tp2),
        "rr3": calc_rr(direction, entry, sl, tp3),
        "rr": calc_rr(direction, entry, sl, tp2),
    }
