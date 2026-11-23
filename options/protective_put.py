# -*- coding: utf-8 -*-
"""
اسکنر استراتژی «پروتکتیو پوت» (خرید سهم پایه + خرید اختیار فروش به‌عنوان حدضرر).

ایده:
  سهم رو با قیمت ask می‌خریم و پوت رو با قیمت ask. اگه تا سررسید قیمت زیر strike بره، پوت
  رو اعمال می‌کنیم و سهم رو با قیمت strike می‌فروشیم. پس بدترین حالت (به ازای هر سهم):

      worst = K * (1 - کارمزد اعمال) - [ S_ask * (1 + کارمزد سهم) + P_ask * (1 + کارمزد آپشن) ]

  اگه worst > 0 باشه یعنی حتی در بدترین حالت سود داریم (موقعیت نابی که کم پیش میاد).
  اگه کمی منفی باشه یعنی «حدضرر ارزان» داریم و سود سهم بالا بازه.

داده: خروجی API با ساختار data[] که هر آیتم {namad, name, type, queue[], volume, turnover, ...} داره.
  - پوت: type=OPTION و name با «اختیارف»؛ نماد با «ط» شروع می‌شه
  - name فرمتی مثل «اختیارف خساپا-1000-1405/10/30» داره (پایه - strike - سررسید شمسی)
  - queue[0].buy = بهترین خرید (bid)، queue[0].sell = بهترین فروش (ask)
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, asdict, field
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional

from .pricing import put_iv, put_greeks, put_price

CONTRACT_SIZE = 1000  # هر قرارداد = ۱۰۰۰ سهم



# ───────────────────────── جدول کارمزد (کسر از ۱؛ ۰٫۲۳۲ درصد = 0.00232) ─────────────────────────
# سهام: با معامله‌ی واقعی تأیید شده (فملی بورس، فزر فرابورس: ۰.۳۶۳۲٪ خرید). صندوق‌های سهامی/اهرمی: خرید ۰.۲۲۴۵٪ و فروش ۰.۲۳٪
# با ۳ صندوق (اهرم، موج، اطلس) کالیبره شده؛ جدول قبلی ۰.۲۳۲٪/۰.۲۳۷۵٪ بود. کارمزد واقعی سامانه = بالا‌گرد(ارزش × نرخ).
# منبع قبلی: جدول کارمزد کاربر (خرید و فروش جدا). اعمال سهام مالیات داره (۰٫۵۵٪) ولی اعمال ETF نه (۰٫۰۵٪).
BUY_FEE = {
    "stock": 0.003712, "equity_etf": 0.002245, "fixed_etf": 0.000375, "leveraged_etf": 0.002245,
    "mixed_etf": 0.001215, "sectoral_etf": 0.002245, "commodity_etf": 0.0012, "gold_etf": 0.0012,
    "silver_etf": 0.0012, "certificate": 0.0024,
}
SELL_FEE = {
    "stock": 0.0088, "equity_etf": 0.0023, "fixed_etf": 0.000375, "leveraged_etf": 0.0023,
    "mixed_etf": 0.0013225, "sectoral_etf": 0.0023, "commodity_etf": 0.0012, "gold_etf": 0.0012,
    "silver_etf": 0.0012, "certificate": 0.0024,
}
BUY_FEE_FARABOURS_STOCK = 0.003632   # خرید سهم فرابورسی (فروش مثل بورس ۰.۸۸٪)؛ با معامله‌ی واقعی فزر و دزاگرس تأیید شده
OPTION_FEE = 0.00103         # خرید/فروش اختیار
EXERCISE_FEE_STOCK = 0.0055  # اعمال با پایه‌ی سهم (شامل مالیات نقل‌وانتقال)
EXERCISE_FEE_ETF = 0.0005    # اعمال با پایه‌ی ETF


def fee_class(asset_type: str) -> str:
    t = (asset_type or "").upper()
    if t == "STOCK" or not t:
        return "stock"
    if "CERT" in t:
        return "certificate"
    for needle, key in (("LEVER", "leveraged_etf"), ("SECTOR", "sectoral_etf"), ("EQUITY", "equity_etf"),
                        ("FIXED", "fixed_etf"), ("MIXED", "mixed_etf"), ("GOLD", "gold_etf"),
                        ("SILVER", "silver_etf"), ("COMMODITY", "commodity_etf")):
        if needle in t:
            return key
    return "leveraged_etf" if t.startswith("ETF") else "stock"   # ناشناخته: محافظه‌کارانه


def asset_fees(asset_type: str, cfg: "ScanConfig", namad: Optional[str] = None) -> dict:
    """buy/sell/exercise برای پایه. مقدار override در cfg (غیر از -1) جدول رو کنار می‌زنه."""
    key = fee_class(asset_type)
    sell, buy = SELL_FEE[key], BUY_FEE[key]
    if key == "stock" and namad and norm(namad) in {norm(x) for x in cfg.farabourse_symbols}:
        buy = BUY_FEE_FARABOURS_STOCK
    exercise = EXERCISE_FEE_ETF if key.endswith("_etf") else EXERCISE_FEE_STOCK
    fees = {
        "class": key,
        "buy": cfg.stock_buy_fee if cfg.stock_buy_fee >= 0 else buy,
        "sell": cfg.sell_fee if cfg.sell_fee >= 0 else sell,
        "exercise": cfg.exercise_fee if cfg.exercise_fee >= 0 else exercise,
        "option": cfg.option_buy_fee,
    }
    # override نماد به نماد (مثلاً سهم فرابورسی): از همه‌ی ردیف‌های بالا قوی‌تره
    ov = {norm(k): v for k, v in cfg.fee_overrides.items()}.get(norm(namad)) if namad else None
    if ov:
        for src, dst in (("stock_buy", "buy"), ("sell", "sell"), ("exercise", "exercise"), ("option", "option")):
            if src in ov:
                fees[dst] = ov[src]
    return fees

# ───────────────────────── تنظیمات (همه قابل override با env در option_main.py) ─────────────────────────
@dataclass
class ScanConfig:
    # کارمزدها: -1 = خودکار از جدول بالا به‌ازای نوع پایه (سهم/ETF). عدد غیرمنفی = override برای همه.
    stock_buy_fee: float = -1.0     # خرید سهم/صندوق پایه
    sell_fee: float = -1.0          # فروش بازاری پایه (وقتی قیمت بالای strike رفته)
    exercise_fee: float = -1.0      # اعمال پوت (فروش پایه به قیمت strike)
    option_buy_fee: float = OPTION_FEE
    farabourse_symbols: tuple = ()   # سهم‌های فرابورسی (کارمزد خرید ۰.۳۶۳۲٪)
    fee_overrides: dict = field(default_factory=dict)   # {نماد پایه: {stock_buy|sell|exercise|option: کسر از ۱}}

    # آستانه‌ی «سود تضمینی»: حاشیه‌ی خالص بعد از کارمزد (درصد از سرمایه)
    min_locked_margin_pct: float = 0.5
    # حداقل حاشیه‌ی سالانه‌شده برای «سود تضمینی». -1 = خودکار: باید از نرخ بهره‌ی rate بیشتر باشه
    # (یعنی خالص بعد از هزینه‌ی فرصت پول مثبت). ۰ = هر سود مثبتی (حتی کمتر از سود بانکی).
    min_locked_annual_pct: float = -1.0

    # آستانه‌ی «حدضرر ارزان»: بیشترین ضرر ممکن (درصد از سرمایه) - صفر = غیرفعال
    max_loss_pct: float = 1.0

    # ارزش معامله: حداقل ارزش پوزیشنِ قابل‌اجرا با حجم عرضه در بهترین قیمت (ریال) و حداقل حجم قرارداد
    min_position_value: float = 100_000_000   # ۱۰ میلیون تومان
    min_contracts: int = 1

    # نرخ بهره‌ی بدون ریسک سالانه (۰.۴۲ = ۴۲٪): هزینه‌ی فرصت پول در پیام، آستانه‌ی خودکار حاشیه سالانه‌ی 🟢،
    # و نرخ بلک-شولز برای IV/دلتا/قیمت تئوریک
    rate: float = 0.42

    # سررسید
    min_days: int = 1
    # حالت ضرر (worst<0): اگه کمتر از این‌قدر روز مونده باشه هشدار نمی‌ره (بیمه‌ی چندروزه معنی نداره؛ سالانه‌سازی هم اغراق‌آمیزه)
    min_days_loss: int = 5
    # حالت ضرر: سربه‌سر باید «قابل‌دسترس» باشه؛ فاصله‌ی سربه‌سر (ln(سربه‌سر/قیمت)) تقسیم بر نوسان تا سررسید
    # (نوسان تاریخی؛ اگه نبود نوسان ضمنی وزنی) از این عدد بیشتر نباشه. 0 = خاموش. اگه نوسانی نداریم فیلتر اعمال نمی‌شه
    max_breakeven_sigma: float = 1.5
    max_days: int = 400

    # حداقل قیمت فروش پوت برای ورود به اسکن (ریال)؛ «بدون معامله» با ارزش معامله = ۰ تشخیص داده می‌شه
    min_option_price: float = 1


@dataclass
class Opportunity:
    tier: str                 # "locked" | "cheap_stop"
    put_symbol: str
    underlying: str
    strike: float
    expiry: date
    days: int
    stock_ask: float
    put_ask: float
    cost_per_share: float     # با کارمزد
    worst_per_share: float    # بدترین حالت (مثبت = سود تضمینی)
    margin_pct: float         # worst / cost * 100
    annual_pct: float         # حاشیه‌ی سالانه‌شده (ساده)
    carry_pct: float          # هزینه‌ی فرصت پول تا سررسید با نرخ cfg.rate (درصد)
    net_after_carry_pct: float  # margin_pct - carry_pct
    contracts: int            # قرارداد قابل اجرا با حجم عرضه‌ی بهترین قیمت ask
    position_value: float     # ارزش پوزیشن (ریال)
    put_volume_today: int
    put_turnover_today: float
    stock_bid: float
    intrinsic: float          # max(K - S, 0)
    moneyness_pct: float      # (K/S - 1) * 100
    ctx: dict = field(default_factory=dict)  # اطلاعات کمکی برای متن هشدار (سهم، پوت، مدل، سناریو، ...)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["expiry"] = self.expiry.isoformat()
        return d


# ───────────────────────── کمکی‌ها ─────────────────────────
_AR2FA = str.maketrans({"ي": "ی", "ك": "ک", "\u200c": "", " ": "", "\u200f": "", "ۀ": "ه"})
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def norm(s: str) -> str:
    return (s or "").translate(_AR2FA)


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> date:
    jy += 1595
    days = -355668 + (365 * jy) + (jy // 33) * 8 + (((jy % 33) + 3) // 4) + jd
    days += (jm - 1) * 31 if jm < 7 else ((jm - 7) * 30 + 186)
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    months = [0, 31, 29 if (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0 else 28,
              31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 13 and gd > months[gm]:
        gd -= months[gm]
        gm += 1
    return date(gy, gm, gd)


def gregorian_to_jalali(d: date) -> str:
    gy, gm, gd = d.year, d.month, d.day
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) + gd + g_d_m[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    jm, jd = (1 + days // 31, 1 + days % 31) if days < 186 else (7 + (days - 186) // 30, 1 + (days - 186) % 30)
    return f"{jy}/{jm:02d}/{jd:02d}"


_NAME_RE = re.compile(
    r"اختیار\s*([فخ])\s*(.+?)\s*-\s*(\d+(?:\.\d+)?)\s*-\s*(\d{4}|\d{2})\s*/?\s*(\d{2})\s*/?\s*(\d{2})\s*$"
)


@dataclass
class ParsedOption:
    kind: str          # "put" | "call"
    underlying: str
    strike: float
    expiry: date


def parse_option_name(name: str) -> Optional[ParsedOption]:
    m = _NAME_RE.search((name or "").translate(_FA_DIGITS))
    if not m:
        return None
    kind = "put" if m.group(1) == "ف" else "call"
    try:
        jy = int(m.group(4))
        jy = jy + 1400 if jy < 100 else jy  # سال دو رقمی مثل 05/11/14
        expiry = jalali_to_gregorian(jy, int(m.group(5)), int(m.group(6)))
    except Exception:
        return None
    return ParsedOption(kind, m.group(2).strip(), float(m.group(3)), expiry)


def _level1(item: dict) -> Optional[dict]:
    q = item.get("queue") or []
    return q[0] if q and q[0] else None


def _num(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default



def _queue_info(item: dict) -> dict:
    q = _level1(item) or {}
    bid, ask = _num(q.get("buy")), _num(q.get("sell"))
    return {
        "bid": bid, "bid_vol": _num(q.get("buyVolume")), "bid_cnt": int(_num(q.get("buyCount"))),
        "ask": ask, "ask_vol": _num(q.get("sellVolume")), "ask_cnt": int(_num(q.get("sellCount"))),
        "spread_pct": ((ask - bid) / ask * 100) if 0 < bid <= ask else None,   # bid>ask = سفارش نامعتبر/کراس
        "last": _num(item.get("closePrice")), "final": _num(item.get("endPrice")),
        "yesterday": _num(item.get("lastDayPrice")),
        "low": _num(item.get("lowPrice")), "high": _num(item.get("highPrice")),
        "day_min": _num(item.get("dailyPriceLow")), "day_max": _num(item.get("dailyPriceHigh")),
        "volume": _num(item.get("volume")), "turnover": _num(item.get("turnover")),
        "trades": int(_num(item.get("tradeCount"))),
        "ask_vol_known": q.get("sellVolume") is not None, "bid_vol_known": q.get("buyVolume") is not None,
        "open_interest": _num(item.get("openInterest")),
    }


def _stock_flow(item: dict) -> dict:
    ts = item.get("traderStatistics") or {}
    def side(who, k, f):
        return _num(((ts.get(who) or {}).get(k) or {}).get(f))
    has_flow = bool(ts) or item.get("_real_net_value") is not None
    real_net = side("real", "buy", "value") - side("real", "sell", "value")
    if item.get("_real_net_value") is not None:
        real_net = _num(item["_real_net_value"])
    return {
        "has_flow": has_flow,
        "per_capita_buy": item.get("_per_capita_buy"), "per_capita_sell": item.get("_per_capita_sell"),
        "buy_power": item.get("_buy_power"),
        "real_net_value": real_net,
        "real_net_vol": side("real", "buy", "volume") - side("real", "sell", "volume"),
        "legal_net_value": side("legal", "buy", "value") - side("legal", "sell", "value"),
        "real_buy_cnt": int(side("real", "buy", "count")), "real_sell_cnt": int(side("real", "sell", "count")),
        "avg_month_vol": _num(item.get("monthlyAverageVolume")),
        "industry": item.get("industryGroup") or "",
        "kind": "صندوق" if str(item.get("type", "")).startswith("ETF") else "سهم",
        "nav": _num(item.get("nav")), "nav_date": str(item.get("navDate") or ""),
    }


def _build_ctx(base: dict, it: dict, S: float, K: float, T: float, cost: float, worst: float,
               put_ask: float, cfg: "ScanConfig", vstats: Optional[dict] = None, fees: Optional[dict] = None) -> dict:
    sc = _queue_info(base); pc = _queue_info(it)
    sc.update(_stock_flow(base))
    iv = put_iv(put_ask, S, K, T, cfg.rate)
    greeks = put_greeks(S, K, T, cfg.rate, iv) if iv else {}
    intrinsic = max(K - S, 0.0)
    fees = fees or asset_fees(base.get("type"), cfg, base.get("namad"))
    # اگه سهم بالای strike بره بازاری می‌فروشیم (کارمزد فروش)، وگرنه اعمال می‌کنیم
    def payoff(st_price):
        return max(st_price * (1 - fees["sell"]), K * (1 - fees["exercise"]))
    be = cost / (1 - fees["sell"]) if worst < 0 else None    # قیمت سهم برای سربه‌سر (فروش بازاری)
    scenarios = [(chg, (payoff(S * (1 + chg / 100)) - cost) / cost * 100) for chg in (-30, -15, 0, 15, 30)]
    vstats = vstats or {}
    vol = {}
    for key, label in (("wiv", "wiv"), ("hv", "hv")):
        sg = vstats.get(key)
        if sg and sg > 0:
            theo = put_price(S, K, T, cfg.rate, sg)
            vol[label] = sg
            vol[label + "_theo"] = theo
            vol[label + "_rich_pct"] = (put_ask / theo - 1) * 100 if theo > 0 else None
    vol.update(iv_rank=vstats.get("iv_rank"), iv_pct=vstats.get("iv_pct"), leveraged=bool(vstats.get("leveraged")))
    # فاصله‌ی سربه‌سر بر حسب انحراف‌معیار حرکت تا سررسید (نوسان تاریخی، وگرنه ضمنی وزنی)
    be_sigma = be_sigma_src = None
    if be:
        for key in ("hv", "wiv"):
            if vol.get(key):
                be_sigma, be_sigma_src = math.log(be / S) / (vol[key] * math.sqrt(T)), key
                break
    return {
        "stock": sc, "put": pc, "iv": iv, **greeks, **vol,
        "premium_vs_intrinsic": put_ask - intrinsic,      # منفی = پوت زیر ارزش ذاتی (ارزون)
        "breakeven": be, "breakeven_chg_pct": (be / S - 1) * 100 if be else None,
        "be_sigma": be_sigma, "be_sigma_src": be_sigma_src,
        "scenarios": scenarios, "rate": cfg.rate, "fees": fees,
        "limit_by": "put" if put_ask_vol_lt_stock(it, base) else "stock",
    }


def put_ask_vol_lt_stock(it: dict, base: dict) -> bool:
    raw = (_level1(it) or {}).get("sellVolume")
    if raw is None:      # حجم عرضه‌ی پوت نامعلوم → محدودکننده فقط سمت سهم حساب می‌شه
        return False
    sv = _num((_level1(base) or {}).get("sellVolume")) // CONTRACT_SIZE
    return _num(raw) <= sv

# ───────────────────────── هستهٔ اسکن ─────────────────────────
def scan(data: Iterable[dict], today: date, cfg: ScanConfig = ScanConfig(),
         stats: Optional[dict] = None) -> List[Opportunity]:
    items = list(data)
    # نقشه‌ی سهم/صندوق پایه (نام نرمال‌شده → آیتم)
    underlying: Dict[str, dict] = {norm(x["namad"]): x for x in items if x.get("type") != "OPTION"}

    by_name = (stats or {}).get("by_name", {})
    aliases = (stats or {}).get("aliases", {})
    out: List[Opportunity] = []
    for it in items:
        if it.get("type") != "OPTION":
            continue
        po = parse_option_name(it.get("name", ""))
        if not po or po.kind != "put":
            continue
        days = (po.expiry - today).days
        if not (cfg.min_days <= days <= cfg.max_days):
            continue
        ukey = norm(po.underlying)
        base = underlying.get(ukey) or underlying.get(aliases.get(ukey, ""))
        if not base:
            continue

        p1, s1 = _level1(it), _level1(base)
        if not p1 or not s1:
            continue
        put_ask = _num(p1.get("sell"))
        put_vol_known = p1.get("sellVolume") is not None   # بعضی منابع حجم صف bid/ask نمی‌دن
        put_ask_vol = _num(p1.get("sellVolume"))
        stock_ask, stock_ask_vol = _num(s1.get("sell")), _num(s1.get("sellVolume"))
        size = int(_num(it.get("contractSize")) or CONTRACT_SIZE)
        if put_ask < cfg.min_option_price or stock_ask <= 0 or stock_ask_vol <= 0 or (put_vol_known and put_ask_vol <= 0):
            continue

        stock_contracts = stock_ask_vol // size
        contracts = int(min(put_ask_vol, stock_contracts)) if put_vol_known else int(stock_contracts)
        if contracts < cfg.min_contracts:
            continue

        fees = asset_fees(base.get("type"), cfg, base.get("namad"))
        min_annual = cfg.rate * 100 if cfg.min_locked_annual_pct < 0 else cfg.min_locked_annual_pct
        cost = stock_ask * (1 + fees["buy"]) + put_ask * (1 + fees["option"])
        proceeds = po.strike * (1 - fees["exercise"])
        worst = proceeds - cost
        margin = worst / cost * 100
        position_value = contracts * size * cost
        if position_value < cfg.min_position_value:
            continue

        if worst > 0 and margin >= cfg.min_locked_margin_pct and margin * 365 / max(days, 1) >= min_annual:
            tier = "locked"
        elif cfg.max_loss_pct > 0 and -margin <= cfg.max_loss_pct:   # شامل worst>0 که زیر آستانه‌ی 🟢 مونده (بیمه‌ی بدون هزینه)
            if worst < 0 and days < cfg.min_days_loss:
                continue
            tier = "cheap_stop"
        else:
            continue

        opp = Opportunity(
            tier=tier, put_symbol=it["namad"], underlying=base["namad"], strike=po.strike,
            expiry=po.expiry, days=days, stock_ask=stock_ask, put_ask=put_ask,
            cost_per_share=cost, worst_per_share=worst, margin_pct=margin,
            annual_pct=margin * 365 / max(days, 1),
            carry_pct=cfg.rate * days / 365 * 100, net_after_carry_pct=margin - cfg.rate * days / 365 * 100,
            contracts=contracts,
            position_value=position_value, put_volume_today=int(_num(it.get("volume"))),
            put_turnover_today=_num(it.get("turnover")), stock_bid=_num(s1.get("buy")),
            intrinsic=max(po.strike - stock_ask, 0.0), moneyness_pct=(po.strike / stock_ask - 1) * 100,
            ctx=_build_ctx(base, it, stock_ask, po.strike, days / 365.0, cost, worst, put_ask, cfg,
                           by_name.get(norm(base["namad"])), fees),
        )
        z = opp.ctx.get("be_sigma")
        if worst < 0 and cfg.max_breakeven_sigma > 0 and z is not None and z > cfg.max_breakeven_sigma:
            continue    # سربه‌سر دور از دسترس: عملاً ضرر قطعی بدون شانس سود معنی‌دار
        out.append(opp)
    out.sort(key=lambda o: (o.tier != "locked", -o.margin_pct))
    return out
