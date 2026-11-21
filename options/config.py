# -*- coding: utf-8 -*-
"""
خواندن تنظیمات از config.json (کنار option_main.py).
اولویت: متغیر محیطی OPT_* (روی GitHub: Variables/Secrets یا env تو workflow)  >  config.json  >  مقدار پیش‌فرض کد.
"""
import json
import os
from dataclasses import dataclass, fields
from typing import Any, Mapping, Optional

from .protective_put import ScanConfig

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")


class ConfigError(Exception):
    pass


@dataclass
class Settings:
    scan: ScanConfig
    cooldown_hours: float = 3.0
    improve_pp: float = 0.25
    max_alerts: int = 8
    max_stale_min: float = 20.0
    data_source: str = "auto"      # auto | tradersarena | optionbaaz
    heston_enabled: bool = True


TOP_KEYS = {"rate_percent", "min_locked_margin_pct", "min_locked_annual_pct", "max_loss_pct",
            "min_position_value_toman", "min_contracts", "min_days", "max_days", "min_option_price",
            "fees_percent", "alerts", "max_stale_minutes", "data_source", "heston"}
FEE_KEYS = {"stock_buy", "sell", "exercise", "option"}
ALERT_KEYS = {"cooldown_hours", "improve_pp", "max_per_run"}
DATA_SOURCES = {"auto", "tradersarena", "optionbaaz"}


def _num(name: str, v: Any, auto: bool = False) -> float:
    if auto and v == "auto":
        return -1.0
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        extra = " یا \"auto\"" if auto else ""
        raise ConfigError(f"{name} باید عدد{extra} باشه (الان: {v!r})")
    return float(v)


def _check_keys(where: str, d: Mapping, allowed: set) -> None:
    unknown = [k for k in d if not k.startswith("_") and k not in allowed]
    if unknown:
        raise ConfigError(f"کلید ناشناخته تو {where}: {unknown}. کلیدهای مجاز: {sorted(allowed)}")


def apply_file(settings: Settings, raw: Mapping) -> None:
    _check_keys("config.json", raw, TOP_KEYS)
    s = settings.scan
    if "rate_percent" in raw:
        rate = _num("rate_percent", raw["rate_percent"])
        if not 0 <= rate <= 300:
            raise ConfigError(f"rate_percent باید بین ۰ و ۳۰۰ باشه (الان: {rate})")
        s.rate = rate / 100
    if "min_locked_margin_pct" in raw:
        s.min_locked_margin_pct = _num("min_locked_margin_pct", raw["min_locked_margin_pct"])
    if "min_locked_annual_pct" in raw:
        s.min_locked_annual_pct = _num("min_locked_annual_pct", raw["min_locked_annual_pct"], auto=True)
    if "max_loss_pct" in raw:
        s.max_loss_pct = _num("max_loss_pct", raw["max_loss_pct"])
    if "min_position_value_toman" in raw:
        s.min_position_value = _num("min_position_value_toman", raw["min_position_value_toman"]) * 10  # تومان → ریال
    for k in ("min_contracts", "min_days", "max_days"):
        if k in raw:
            setattr(s, k, int(_num(k, raw[k])))
    if "min_option_price" in raw:
        s.min_option_price = _num("min_option_price", raw["min_option_price"])
    if "fees_percent" in raw:
        fees = raw["fees_percent"]
        if not isinstance(fees, dict):
            raise ConfigError("fees_percent باید یک شیء {..} باشه")
        _check_keys("fees_percent", fees, FEE_KEYS)
        for key, attr in (("stock_buy", "stock_buy_fee"), ("sell", "sell_fee"), ("exercise", "exercise_fee")):
            if key in fees:
                v = _num("fees_percent." + key, fees[key], auto=True)
                setattr(s, attr, v if v < 0 else v / 100)
        if "option" in fees:
            s.option_buy_fee = _num("fees_percent.option", fees["option"]) / 100
    if "alerts" in raw:
        al = raw["alerts"]
        if not isinstance(al, dict):
            raise ConfigError("alerts باید یک شیء {..} باشه")
        _check_keys("alerts", al, ALERT_KEYS)
        if "cooldown_hours" in al:
            settings.cooldown_hours = _num("alerts.cooldown_hours", al["cooldown_hours"])
        if "improve_pp" in al:
            settings.improve_pp = _num("alerts.improve_pp", al["improve_pp"])
        if "max_per_run" in al:
            settings.max_alerts = int(_num("alerts.max_per_run", al["max_per_run"]))
    if "max_stale_minutes" in raw:
        settings.max_stale_min = _num("max_stale_minutes", raw["max_stale_minutes"])
    if "data_source" in raw:
        if raw["data_source"] not in DATA_SOURCES:
            raise ConfigError(f"data_source باید یکی از {sorted(DATA_SOURCES)} باشه (الان: {raw['data_source']!r})")
        settings.data_source = raw["data_source"]
    if "heston" in raw:
        he = raw["heston"]
        if not isinstance(he, dict):
            raise ConfigError("heston باید یک شیء {..} باشه")
        _check_keys("heston", he, {"enabled"})
        if "enabled" in he:
            if not isinstance(he["enabled"], bool):
                raise ConfigError(f"heston.enabled باید true یا false باشه (الان: {he['enabled']!r})")
            settings.heston_enabled = he["enabled"]


def apply_env(settings: Settings, env: Mapping[str, str]) -> None:
    """override اضطراری با متغیر محیطی؛ واحدها همان فیلدهای ScanConfig (مثلاً OPT_RATE=0.42، کارمزد به‌صورت کسر)."""
    for f in fields(ScanConfig):
        v = env.get("OPT_" + f.name.upper())
        if v not in (None, ""):
            try:
                setattr(settings.scan, f.name, type(getattr(settings.scan, f.name))(float(v)))
            except ValueError:
                raise ConfigError(f"OPT_{f.name.upper()} عدد نیست: {v!r}")
    ds = env.get("OPT_DATA_SOURCE")
    if ds:
        if ds not in DATA_SOURCES:
            raise ConfigError(f"OPT_DATA_SOURCE باید یکی از {sorted(DATA_SOURCES)} باشه (الان: {ds!r})")
        settings.data_source = ds
    if env.get("OPT_HESTON") not in (None, ""):
        settings.heston_enabled = env["OPT_HESTON"].strip().lower() in ("1", "true", "yes", "on")
    for var, attr, cast in (("OPT_COOLDOWN_HOURS", "cooldown_hours", float), ("OPT_IMPROVE_PP", "improve_pp", float),
                            ("OPT_MAX_ALERTS", "max_alerts", int), ("OPT_MAX_STALE_MIN", "max_stale_min", float)):
        v = env.get(var)
        if v not in (None, ""):
            try:
                setattr(settings, attr, cast(float(v)))
            except ValueError:
                raise ConfigError(f"{var} عدد نیست: {v!r}")


def load_settings(path: Optional[str] = None, env: Optional[Mapping[str, str]] = None) -> Settings:
    settings = Settings(scan=ScanConfig())
    path = path or os.getenv("OPT_CONFIG") or DEFAULT_PATH
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except json.JSONDecodeError as e:
            raise ConfigError(f"فایل {os.path.basename(path)} JSON معتبر نیست (خط {e.lineno}، ستون {e.colno}): {e.msg}")
        if not isinstance(raw, dict):
            raise ConfigError("config.json باید یک شیء {..} باشه")
        apply_file(settings, raw)
    apply_env(settings, os.environ if env is None else env)
    return settings
