# -*- coding: utf-8 -*-
"""
مدل هستون (اروپایی) با نرخ بهره‌ی خودمون (rate_percent تو config.json) + کالیبراسیون روی زنجیره‌ی آپشن‌های همان پایه.
فرمول: Heston (1993) با فرم پایدار «little trap» (Albrecher 2007)، انتگرال با Gauss-Legendre؛ فقط numpy.
  C = S·P1 − K·e^(−rT)·P2 ،  P_j = ½ + (1/π) ∫₀^∞ Re[e^(−iφ ln K) f_j(φ) / (iφ)] dφ ،  پوت از پاریتی.
"""
import math
import time
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .pricing import put_iv  # noqa: F401  (برای استفاده‌ی احتمالی بیرونی)

_GL_CACHE: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}

BOUNDS = {"v0": (0.0025, 9.0), "kappa": (0.1, 25.0), "theta": (0.0025, 9.0), "sigma": (0.05, 6.0), "rho": (-0.99, 0.99)}
PARAM_NAMES = ["v0", "kappa", "theta", "sigma", "rho"]


def _gl(n: int):
    if n not in _GL_CACHE:
        _GL_CACHE[n] = np.polynomial.legendre.leggauss(n)
    return _GL_CACHE[n]


def heston_call(S: float, K, T: float, r: float, v0: float, kappa: float, theta: float, sigma: float,
                rho: float, n: int = 400) -> np.ndarray:
    """قیمت کال اروپایی هستون برای آرایه‌ای از strikeها (سررسید و پارامترها ثابت)."""
    K = np.atleast_1d(np.asarray(K, dtype=float))
    sigma = max(sigma, 1e-4)
    top = min(max(10.0 / math.sqrt(max(v0, theta, 1e-4) * T), 40.0), 4000.0)
    x, w = _gl(n)
    phi = (0.5 * top * (x + 1.0))[None, :]
    wt = (0.5 * top * w)[None, :]
    lnK = np.log(K)[:, None]
    lnS = math.log(S)

    def P(j: int) -> np.ndarray:
        uj = 0.5 if j == 1 else -0.5
        b = kappa - rho * sigma if j == 1 else kappa
        rsi = rho * sigma * 1j * phi
        d = np.sqrt((rsi - b) ** 2 - sigma ** 2 * (2 * uj * 1j * phi - phi ** 2))
        g = (b - rsi - d) / (b - rsi + d)
        e = np.exp(-d * T)
        C = r * 1j * phi * T + kappa * theta / sigma ** 2 * ((b - rsi - d) * T - 2 * np.log((1 - g * e) / (1 - g)))
        D = (b - rsi - d) / sigma ** 2 * (1 - e) / (1 - g * e)
        f = np.exp(C + D * v0 + 1j * phi * lnS)
        integrand = np.real(np.exp(-1j * phi * lnK) * f / (1j * phi))
        return 0.5 + (integrand * wt).sum(axis=1) / math.pi

    disc = math.exp(-r * T)
    call = S * P(1) - K * disc * P(2)
    return np.maximum(call, 0.0)


def heston_put(S: float, K, T: float, r: float, *params, n: int = 400) -> np.ndarray:
    K = np.atleast_1d(np.asarray(K, dtype=float))
    call = heston_call(S, K, T, r, *params, n=n)
    return np.maximum(call - S + K * math.exp(-r * T), 0.0)


# ───────────────────────── کالیبراسیون ─────────────────────────
def _to_params(x: np.ndarray) -> List[float]:
    out = []
    for xi, name in zip(x, PARAM_NAMES):
        lo, hi = BOUNDS[name]
        out.append(lo + (hi - lo) / (1.0 + math.exp(-max(min(float(xi), 40.0), -40.0))))
    return out


def _from_params(p: Sequence[float]) -> np.ndarray:
    xs = []
    for v, name in zip(p, PARAM_NAMES):
        lo, hi = BOUNDS[name]
        t = min(max((v - lo) / (hi - lo), 1e-6), 1 - 1e-6)
        xs.append(math.log(t / (1 - t)))
    return np.array(xs)


def _nelder_mead(f, x0: np.ndarray, iters: int = 350, step: float = 0.6, tol: float = 1e-10) -> Tuple[np.ndarray, float]:
    n = len(x0)
    pts = [x0.copy()] + [x0 + step * np.eye(n)[i] for i in range(n)]
    vals = [f(p) for p in pts]
    for _ in range(iters):
        order = np.argsort(vals)
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) < tol:
            break
        cen = np.mean(pts[:-1], axis=0)
        xr = cen + (cen - pts[-1])
        fr = f(xr)
        if fr < vals[0]:
            xe = cen + 2 * (cen - pts[-1])
            fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = cen + 0.5 * ((xr if fr < vals[-1] else pts[-1]) - cen)
            fc = f(xc)
            if fc < min(fr, vals[-1]):
                pts[-1], vals[-1] = xc, fc
            else:
                pts = [pts[0]] + [pts[0] + 0.5 * (p - pts[0]) for p in pts[1:]]
                vals = [vals[0]] + [f(p) for p in pts[1:]]
    i = int(np.argmin(vals))
    return pts[i], float(vals[i])


def calibrate(S: float, r: float, groups: List[dict], x0: Optional[Sequence[float]] = None,
              n: int = 160, time_budget: float = 15.0) -> Optional[dict]:
    """
    groups: [{"T": سال، "strikes": [...], "kinds": ["call"/"put", ...], "prices": [...]}, ...]  (قیمت‌های بازار؛ ریال)
    خروجی: {"params": {...}, "rmse_rel": خطای نسبی میانگین‌مربعی، "n_points": ...} یا None
    """
    pts = sum(len(g["strikes"]) for g in groups)
    if pts < 5:
        return None
    start = time.time()
    atm = float(np.median(np.concatenate([np.asarray(g["prices"], float) for g in groups])))
    v_guess = 0.25
    prep = []
    for g in groups:
        K = np.asarray(g["strikes"], float)
        mkt = np.asarray(g["prices"], float)
        is_put = np.array([k == "put" for k in g["kinds"]])
        scale = np.maximum(mkt, 0.002 * S)
        prep.append((float(g["T"]), K, mkt, is_put, scale))

    def err(params, nn) -> float:
        tot, cnt = 0.0, 0
        for T, K, mkt, is_put, scale in prep:
            call = heston_call(S, K, T, r, *params, n=nn)
            model = np.where(is_put, np.maximum(call - S + K * math.exp(-r * T), 0.0), call)
            tot += float((((model - mkt) / scale) ** 2).sum())
            cnt += len(K)
        return tot / max(cnt, 1)

    starts = [list(x0)] if x0 else [[v_guess, 3.0, v_guess, 1.0, -0.3], [0.6, 1.5, 0.4, 2.0, -0.6]]
    best_x, best_f = None, float("inf")
    for s0 in starts:
        def f(x):
            if time.time() - start > time_budget:
                return 1e9
            try:
                val = err(_to_params(x), n)
            except (FloatingPointError, ValueError, ZeroDivisionError):
                return 1e9
            return val if math.isfinite(val) else 1e9
        x, fx = _nelder_mead(f, _from_params(s0))
        if fx < best_f:
            best_x, best_f = x, fx
    if best_x is None or best_f >= 1e8:
        return None
    params = _to_params(best_x)
    final = err(params, 400)
    return {"params": dict(zip(PARAM_NAMES, params)), "rmse_rel": math.sqrt(final), "n_points": pts}


# ───────────────────────── از زنجیره‌ی بازار تا قیمت هستون یک پوت ─────────────────────────
def _mid(q: dict) -> Optional[float]:
    bid, ask, last = q.get("bid") or 0, q.get("ask") or 0, q.get("last") or 0
    if bid > 0 and ask > 0 and ask <= 1.8 * bid:
        return (bid + ask) / 2
    if (q.get("volume") or 0) > 0 and last > 1:
        return float(last)
    return None


def build_groups(chain: List[dict], base_norm: str, S: float, today: date, max_expiries: int = 4,
                 min_days: int = 3) -> List[dict]:
    """قراردادهای OTM (کال بالای S، پوت زیر S) با قیمت قابل اتکا، گروه‌بندی‌شده بر اساس سررسید."""
    by_exp: Dict[date, dict] = {}
    for q in chain:
        if q["base"] != base_norm:
            continue
        days = (q["expiry"] - today).days
        if days < min_days:
            continue
        otm = (q["kind"] == "call" and q["strike"] >= S) or (q["kind"] == "put" and q["strike"] <= S)
        px = _mid(q)
        if not otm or px is None or px < 2:
            continue
        g = by_exp.setdefault(q["expiry"], {"T": days / 365.0, "strikes": [], "kinds": [], "prices": []})
        g["strikes"].append(float(q["strike"]))
        g["kinds"].append(q["kind"])
        g["prices"].append(px)
    groups = [g for _, g in sorted(by_exp.items()) if len(g["strikes"]) >= 3]
    return groups[:max_expiries]


def heston_for_put(chain: List[dict], base_name: str, S: float, K: float, T: float, put_ask: float,
                   r: float, today: date, cache: Optional[dict] = None, max_rmse: float = 0.35) -> Optional[dict]:
    """
    کالیبره‌ی هستون روی زنجیره‌ی همان پایه → قیمت تئوریک این پوت. اگه داده کم باشه یا برازش بد باشه None.
    S = قیمت مرجع پایه (وسط bid/ask یا آخرین).
    """
    from .protective_put import norm
    base = norm(base_name)
    cache = cache if cache is not None else {}
    key = (base, round(S, 2), round(r, 6))
    if key not in cache:
        groups = build_groups(chain, base, S, today)
        cache[key] = calibrate(S, r, groups) if groups else None
    cal = cache[key]
    if not cal or cal["rmse_rel"] > max_rmse:
        return None
    p = cal["params"]
    theo = float(heston_put(S, [K], T, r, p["v0"], p["kappa"], p["theta"], p["sigma"], p["rho"], n=400)[0])
    if theo <= 0:
        return None
    return {"price": theo, "rich_pct": (put_ask / theo - 1) * 100, "rmse_rel": cal["rmse_rel"],
            "n_points": cal["n_points"], "params": p}
