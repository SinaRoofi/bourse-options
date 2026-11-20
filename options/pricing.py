# -*- coding: utf-8 -*-
"""بلک-شولز اروپایی برای پوت، بدون وابستگی به scipy (فقط math)."""
from math import erf, exp, log, sqrt, pi
from typing import Optional


def _N(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _n(x: float) -> float:
    return exp(-0.5 * x * x) / sqrt(2.0 * pi)


def _d1(S, K, T, r, sg):
    return (log(S / K) + (r + 0.5 * sg * sg) * T) / (sg * sqrt(T))


def put_price(S, K, T, r, sg) -> float:
    d1 = _d1(S, K, T, r, sg)
    d2 = d1 - sg * sqrt(T)
    return max(K * exp(-r * T) * _N(-d2) - S * _N(-d1), 0.0)


def put_iv(P, S, K, T, r) -> Optional[float]:
    """نوسان ضمنی پوت. کف/سقف بدون‌آربیتراژ با تنزیل: [K e^-rT - S, K e^-rT)."""
    if min(P, S, K, T) <= 0:
        return None
    dk = K * exp(-r * T)
    if P < max(dk - S, 0.0) * 0.999 or P >= dk:
        return None
    lo, hi = 0.001, 5.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if put_price(S, K, T, r, mid) > P:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-6:
            break
    return (lo + hi) / 2


def put_greeks(S, K, T, r, sg) -> dict:
    d1 = _d1(S, K, T, r, sg)
    d2 = d1 - sg * sqrt(T)
    theta = (-(S * _n(d1) * sg) / (2 * sqrt(T)) + r * K * exp(-r * T) * _N(-d2)) / 365.0
    return {"delta": _N(d1) - 1.0, "theta_day": theta, "vega_1pct": S * _n(d1) * sqrt(T) * 0.01}
