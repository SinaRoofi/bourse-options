# -*- coding: utf-8 -*-
"""خواندن تنظیمات از config.py (کنار option_main.py)."""
import importlib.util
import os
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from .protective_put import ScanConfig

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.py")


class ConfigError(Exception):
    pass


@dataclass
class Settings:
    scan: ScanConfig
    cooldown_hours: float = 3.0
    improve_pp: float = 0.25
    max_alerts: int = 8
    max_stale_min: float = 20.0
    heston_enabled: bool = True
    market_start: Tuple[int, int] = (9, 0)
    market_end: Tuple[int, int] = (12, 30)


ALLOWED = {
    "RATE_PERCENT", "MIN_LOCKED_MARGIN_PCT", "MIN_LOCKED_ANNUAL_PCT", "MAX_LOSS_PCT", "MIN_POSITION_VALUE_TOMAN",
    "MIN_CONTRACTS", "MIN_DAYS", "MIN_DAYS_LOSS", "MAX_DAYS", "MAX_BREAKEVEN_SIGMA", "MIN_OPTION_PRICE", "FEES_PERCENT", "FEE_OVERRIDES", "FARABOURS_SYMBOLS", "ALERT_COOLDOWN_HOURS",
    "ALERT_IMPROVE_PP", "MAX_ALERTS_PER_RUN", "MARKET_START", "MARKET_END", "MAX_STALE_MINUTES", "HESTON_ENABLED",
}
FEE_KEYS = {"stock_buy", "sell", "exercise", "option"}


def _num(name: str, v: Any, auto: bool = False) -> float:
    if auto and v == "auto":
        return -1.0
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        extra = " یا \"auto\"" if auto else ""
        raise ConfigError(f"{name} باید عدد{extra} باشه (الان: {v!r})")
    return float(v)


def _hhmm(name: str, v: Any) -> Tuple[int, int]:
    try:
        h, m = str(v).split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except ValueError:
        pass
    raise ConfigError(f"{name} باید به شکل \"HH:MM\" باشه، مثلاً \"09:00\" (الان: {v!r})")


def _load_module(path: str):
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("bourse_options_user_config", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SyntaxError as e:
        raise ConfigError(f"config.py خط {e.lineno}: {e.msg}")
    except Exception as e:  # noqa: BLE001  (مثلاً NameError تو خود فایل)
        raise ConfigError(f"config.py اجرا نشد: {type(e).__name__}: {e}")
    return mod


def load_settings(path: Optional[str] = None) -> Settings:
    st = Settings(scan=ScanConfig())
    mod = _load_module(path or DEFAULT_PATH)
    if mod is None:
        return st
    names = {n for n in vars(mod) if n.isupper() and not n.startswith("_")}
    unknown = sorted(names - ALLOWED)
    if unknown:
        raise ConfigError(f"اسم ناشناخته تو config.py: {unknown}. اسم‌های مجاز: {sorted(ALLOWED)}")
    g = lambda n: getattr(mod, n) if n in names else None   # noqa: E731
    s = st.scan
    if "RATE_PERCENT" in names:
        rate = _num("RATE_PERCENT", mod.RATE_PERCENT)
        if not 0 <= rate <= 300:
            raise ConfigError(f"RATE_PERCENT باید بین ۰ و ۳۰۰ باشه (الان: {rate})")
        s.rate = rate / 100
    if "MIN_LOCKED_MARGIN_PCT" in names:
        s.min_locked_margin_pct = _num("MIN_LOCKED_MARGIN_PCT", mod.MIN_LOCKED_MARGIN_PCT)
    if "MIN_LOCKED_ANNUAL_PCT" in names:
        s.min_locked_annual_pct = _num("MIN_LOCKED_ANNUAL_PCT", mod.MIN_LOCKED_ANNUAL_PCT, auto=True)
    if "MAX_LOSS_PCT" in names:
        s.max_loss_pct = _num("MAX_LOSS_PCT", mod.MAX_LOSS_PCT)
    if "MIN_POSITION_VALUE_TOMAN" in names:
        s.min_position_value = _num("MIN_POSITION_VALUE_TOMAN", mod.MIN_POSITION_VALUE_TOMAN) * 10   # تومان → ریال
    for n, attr in (("MIN_CONTRACTS", "min_contracts"), ("MIN_DAYS", "min_days"), ("MIN_DAYS_LOSS", "min_days_loss"), ("MAX_DAYS", "max_days")):
        if n in names:
            setattr(s, attr, int(_num(n, g(n))))
    if "MAX_BREAKEVEN_SIGMA" in names:
        s.max_breakeven_sigma = _num("MAX_BREAKEVEN_SIGMA", mod.MAX_BREAKEVEN_SIGMA)
    if "MIN_OPTION_PRICE" in names:
        s.min_option_price = _num("MIN_OPTION_PRICE", mod.MIN_OPTION_PRICE)
    if "FEES_PERCENT" in names:
        fees = mod.FEES_PERCENT
        if not isinstance(fees, dict):
            raise ConfigError("FEES_PERCENT باید یک دیکشنری {...} باشه")
        bad = sorted(set(fees) - FEE_KEYS)
        if bad:
            raise ConfigError(f"کلید ناشناخته تو FEES_PERCENT: {bad}. کلیدهای مجاز: {sorted(FEE_KEYS)}")
        for key, attr in (("stock_buy", "stock_buy_fee"), ("sell", "sell_fee"), ("exercise", "exercise_fee")):
            if key in fees:
                v = _num("FEES_PERCENT." + key, fees[key], auto=True)
                setattr(s, attr, v if v < 0 else v / 100)
        if "option" in fees:
            s.option_buy_fee = _num("FEES_PERCENT.option", fees["option"]) / 100
    if "FARABOURS_SYMBOLS" in names:
        syms = mod.FARABOURS_SYMBOLS
        if not isinstance(syms, (list, tuple, set)) or not all(isinstance(x, str) for x in syms):
            raise ConfigError("FARABOURS_SYMBOLS باید یک لیست از نمادها (رشته) باشه")
        s.farabourse_symbols = tuple(syms)
    if "FEE_OVERRIDES" in names:
        ov = mod.FEE_OVERRIDES
        if not isinstance(ov, dict):
            raise ConfigError("FEE_OVERRIDES باید یک دیکشنری {نماد: {...}} باشه")
        for sym, d in ov.items():
            if not isinstance(d, dict):
                raise ConfigError(f"FEE_OVERRIDES[{sym!r}] باید یک دیکشنری باشه")
            bad = sorted(set(d) - FEE_KEYS)
            if bad:
                raise ConfigError(f"کلید ناشناخته تو FEE_OVERRIDES[{sym!r}]: {bad}. کلیدهای مجاز: {sorted(FEE_KEYS)}")
            s.fee_overrides[str(sym)] = {k: _num(f"FEE_OVERRIDES.{sym}.{k}", v) / 100 for k, v in d.items()}
    if "ALERT_COOLDOWN_HOURS" in names:
        st.cooldown_hours = _num("ALERT_COOLDOWN_HOURS", mod.ALERT_COOLDOWN_HOURS)
    if "ALERT_IMPROVE_PP" in names:
        st.improve_pp = _num("ALERT_IMPROVE_PP", mod.ALERT_IMPROVE_PP)
    if "MAX_ALERTS_PER_RUN" in names:
        st.max_alerts = int(_num("MAX_ALERTS_PER_RUN", mod.MAX_ALERTS_PER_RUN))
    if "MAX_STALE_MINUTES" in names:
        st.max_stale_min = _num("MAX_STALE_MINUTES", mod.MAX_STALE_MINUTES)
    if "MARKET_START" in names:
        st.market_start = _hhmm("MARKET_START", mod.MARKET_START)
    if "MARKET_END" in names:
        st.market_end = _hhmm("MARKET_END", mod.MARKET_END)
    if "HESTON_ENABLED" in names:
        if not isinstance(mod.HESTON_ENABLED, bool):
            raise ConfigError(f"HESTON_ENABLED باید True یا False باشه (الان: {mod.HESTON_ENABLED!r})")
        st.heston_enabled = mod.HESTON_ENABLED
    return st
