# -*- coding: utf-8 -*-
"""
منبع داده‌ی tradersarena:
  • /data/option-watch-csv  → همه‌ی آپشن‌ها در یک درخواست (قیمت‌ها، OI، شناسه‌ی هر قرارداد)
  • /data/{insCode}/info    → صف ۵ سطحی با حجم، زمان به‌روزرسانی، جریان پول، حد مجاز قیمت (سهم/صندوق/آپشن)
راه‌های جایگزین (وقتی info نیست):
  • سهم/صندوق بدون id یا بدون info  → snapshot صنایع/صندوق‌ها (همان endpoint بورس‌ترکر)
  • پوتی که info‌اش نیومد              → قیمت‌های option-watch (حجم صف نامعلوم)
  • دیتای بدون زمان                     → وضعیت بازار optionbaaz برای تشخیص تعطیلی
بلک-شولز/هستون/یونانی‌های خود سایت استفاده نمی‌شن؛ همه‌چیز با نرخ بهره‌ی config.py خودمون حساب می‌شه.
"""
import dataclasses
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from .errors import DataError, response_diag
from .protective_put import CONTRACT_SIZE, ScanConfig, jalali_to_gregorian, norm, scan

DEFAULT_BASE_URL = "https://tradersarena.ir"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
MAX_WORKERS = 8
SNAPSHOT_TIMEFRAME = 12
MARKET_STATE_URL = "https://api.optionbaaz.ir/market/status/overview"

# ترتیب دقیق ستون‌های option-watch-csv (۴۶ ستون؛ تأییدشده با ردیف نمونه)
OW_FA = [
    "شناسه", "نماد", "نوع قرارداد", "سهم پایه", "وضعیت قرارداد", "حجم", "ارزش", "قیمت اعمال", "قیمت سهم پایه",
    "درصد تغییر قیمت سهم پایه", "درصد اختلاف آخرین و پایانی سهم پایه", "قیمت سر به سری", "درصد سر به سری",
    "تقاضا", "عرضه", "آخرین", "درصد آخرین", "بیشترین", "کمترین", "نوسان", "سررسید", "فاصله تا سررسید",
    "اندازه قرارداد", "موقعیت‌های باز", "حباب فروش بر پایه بهترین تقاضا", "حباب پریمیوم",
    "حباب خرید بر پایه بهترین عرضه", "بلک شولز", "فاصله تا بلک شولز", "دلتا", "گاما", "وگا", "تتا", "رو",
    "نوسان‌پذیری ضمنی", "نوسان‌پذیری سهم پایه", "اهرم", "ارزش ذاتی", "ارزش زمانی", "کاورد کال",
    "بتای سهم پایه", "ضریب همبستگی", "قدرت خرید", "قیمت دیروز", "ورود پول", "ورود پول مفهومی",
]
OW_KEYS = [
    "id", "symbol", "kind", "base", "status", "volume", "value", "strike", "base_price",
    "base_chg_pct", "base_last_final_pct", "breakeven", "breakeven_pct",
    "bid", "ask", "last", "last_pct", "high", "low", "range_pct", "expiry", "days",
    "size", "open_interest", "bubble_sell", "bubble_premium",
    "bubble_buy", "site_bs", "site_bs_diff_pct", "site_delta", "site_gamma", "site_vega", "site_theta", "site_rho",
    "site_iv", "site_base_hv", "leverage", "intrinsic", "time_value", "covered_call",
    "beta", "corr", "buy_power", "yesterday", "money_in", "money_in_concept",
]
assert len(OW_FA) == len(OW_KEYS) == 46
FA2KEY = {norm(fa): key for fa, key in zip(OW_FA, OW_KEYS)}
TEXT_KEYS = {"id", "symbol", "kind", "base", "status", "expiry"}
PUT_WORDS = {"فروش", "put", "PUT", "P"}
CALL_WORDS = {"خرید", "call", "CALL", "C"}


def is_put(kind) -> bool:
    return kind in PUT_WORDS


# ───────────────────────── HTTP ─────────────────────────
def extra_headers() -> dict:
    """هدر اضافه‌ی اختیاری برای tradersarena از env OPTIONS_API_HEADERS (JSON)، مثلاً اگه روزی کوکی/توکن لازم شد."""
    raw = os.getenv("OPTIONS_API_HEADERS") or ""
    try:
        d = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        raise DataError("OPTIONS_API_HEADERS باید JSON معتبر باشه")
    return d if isinstance(d, dict) else {}


def http_get_json(url: str, params: Optional[dict] = None, headers: Optional[dict] = None,
                  timeout: float = 25, retries: int = 2, label: str = "") -> Any:
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8"}
    h.update(extra_headers())
    h.update(headers or {})
    name = label or url
    last: Optional[DataError] = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=h, timeout=timeout)
        except requests.RequestException as e:
            last = DataError(f"{name}: {type(e).__name__}: {e}")
        else:
            if r.status_code in (401, 403):
                raise DataError(f"{name}: دسترسی رد شد: {response_diag(r)}", status=r.status_code)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    raise DataError(f"{name}: پاسخ JSON نیست: {response_diag(r)}")
            last = DataError(f"{name}: {response_diag(r)}", status=r.status_code)
            if r.status_code < 500 and r.status_code != 429:
                raise last
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise last if last else DataError(f"{name}: نامشخص")


def describe(payload: Any, limit: int = 400) -> str:
    if isinstance(payload, dict):
        parts = []
        for k, v in list(payload.items())[:12]:
            size = f"[{len(v)}]" if isinstance(v, (list, dict)) else ""
            parts.append(f"{k}{size}")
        return "dict{" + ", ".join(parts) + "}"
    if isinstance(payload, list):
        first = repr(payload[0])[:limit] if payload else ""
        return f"list[{len(payload)}] first={first}"
    return type(payload).__name__


# ───────────────────────── option-watch-csv ─────────────────────────
def _num(x) -> Optional[float]:
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _extract_rows(payload: Any) -> Tuple[Optional[list], list]:
    header = None
    rows = None
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for hk in ("columns", "headers", "header", "cols"):
            if isinstance(payload.get(hk), list):
                header = payload[hk]
                break
        for rk in ("rows", "data", "items", "result", "options"):
            v = payload.get(rk)
            if isinstance(v, list):
                rows = v
                break
            if isinstance(v, dict):
                h2, r2 = _extract_rows(v)
                return (header or h2), r2
    if rows is None:
        raise DataError("ساختار option-watch-csv ناشناخته: " + describe(payload))
    if rows and isinstance(rows[0], list) and header is None and "نماد" in [norm(str(x)) for x in rows[0]]:
        header, rows = rows[0], rows[1:]
    return header, rows


def _kind_index(row) -> Optional[int]:
    for i, v in enumerate(row[:8]):
        if v in PUT_WORDS or v in CALL_WORDS:
            return i
    return None


def _record(row, header_idx: Optional[Dict[str, int]]) -> Optional[dict]:
    if isinstance(row, dict):
        rec = {}
        for k, v in row.items():
            key = FA2KEY.get(norm(str(k))) or (k if k in OW_KEYS else None)
            if key:
                rec[key] = v
        return rec or None
    if not isinstance(row, (list, tuple)):
        return None
    j = _kind_index(row)
    if j is None:
        return None
    ref = (header_idx or {}).get("kind", OW_KEYS.index("kind"))
    off = j - ref                      # اگه ستونی اضافه/کم بود، با موقعیت «نوع قرارداد» تراز می‌شه
    idx = header_idx or {k: i for i, k in enumerate(OW_KEYS)}
    return {key: row[i + off] for key, i in idx.items() if 0 <= i + off < len(row)}


def parse_option_watch(payload: Any) -> List[dict]:
    header, rows = _extract_rows(payload)
    header_idx = None
    if header:
        header_idx = {FA2KEY[norm(str(h))]: i for i, h in enumerate(header) if norm(str(h)) in FA2KEY}
        if "kind" not in header_idx:
            header_idx = None
    out = []
    for row in rows:
        rec = _record(row, header_idx)
        if not rec or not isinstance(rec.get("symbol"), str) or not isinstance(rec.get("base"), str):
            continue
        for k, v in list(rec.items()):
            if k not in TEXT_KEYS:
                rec[k] = _num(v)
        out.append(rec)
    if rows and not out:
        raise DataError("هیچ ردیف قابل‌خواندنی تو option-watch-csv نبود؛ نمونه‌ی ردیف: " + repr(rows[0])[:300])
    return out


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else repr(float(x))


def option_items(recs: List[dict]) -> List[dict]:
    """رکوردهای پوت → آیتم با همان قالب full.json (name به شکل «اختیارف پایه-strike-سررسید»)."""
    items = []
    for r in recs:
        if not is_put(r.get("kind")):
            continue
        strike, expiry = r.get("strike"), r.get("expiry")
        if not strike or not expiry:
            continue
        items.append({
            "namad": r["symbol"], "name": f"اختیارف {r['base']}-{_fmt(strike)}-{expiry}", "type": "OPTION",
            # حجم صف bid/ask تو این منبع نیست → None (یعنی نامعلوم، نه صفر)
            "queue": [{"buy": r.get("bid") or 0, "sell": r.get("ask") or 0, "buyVolume": None, "sellVolume": None,
                       "buyCount": 0, "sellCount": 0}],
            "volume": r.get("volume") or 0, "turnover": r.get("value") or 0,
            "closePrice": r.get("last") or 0, "endPrice": r.get("last") or 0, "lastDayPrice": r.get("yesterday") or 0,
            "tradeCount": 0, "openInterest": r.get("open_interest") or 0,
            "contractSize": int(r.get("size") or CONTRACT_SIZE),
        })
    return items


def chain_from_records(recs: List[dict]) -> List[dict]:
    """زنجیره‌ی کامل (کال و پوت) برای کالیبراسیون هستون."""
    out = []
    for r in recs:
        try:
            y, m, d = (int(x) for x in str(r.get("expiry")).replace("-", "/").split("/"))
            exp = jalali_to_gregorian(y if y > 99 else y + 1400, m, d)
        except (ValueError, TypeError):
            continue
        if not r.get("strike"):
            continue
        out.append({"base": norm(r["base"]), "kind": "put" if is_put(r.get("kind")) else "call", "strike": r["strike"],
                    "expiry": exp, "bid": r.get("bid") or 0, "ask": r.get("ask") or 0, "last": r.get("last") or 0,
                    "volume": r.get("volume") or 0, "size": r.get("size") or CONTRACT_SIZE})
    return out


# ───────────────────────── snapshot سهم/صندوق (راه جایگزین) ─────────────────────────
INDUSTRY_CODES = ["01", "10", "11", "13", "14", "17", "19", "20", "21", "22", "23", "25", "27", "28", "29", "31", "32",
                  "34", "38", "39", "40", "42", "43", "44", "47", "49", "53", "54", "55", "56", "57", "58", "60", "64",
                  "66", "67", "70", "72", "73", "74"]
SLUG_TYPE = {"index-funds": "ETF_EQUITY", "classic-stock-funds": "ETF_EQUITY", "fund-in-funds": "ETF_EQUITY",
             "leveraged-funds": "ETF_LEVERAGE", "sector-funds": "ETF_SECTORAL", "energy-funds": "ETF_SECTORAL",
             "real-state-funds": "ETF_SECTORAL", "mixed-funds": "ETF_MIXED"}
ALL_FUND_SLUGS = list(SLUG_TYPE)
SLUGS_BY_SUBTYPE = {2: ["classic-stock-funds", "index-funds"], 3: ["leveraged-funds"], 8: ["sector-funds"]}


def _path(d: Any, path: str, default=None):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def parse_snapshot(payload: Any, slug: str) -> List[dict]:
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise DataError(f"snapshot {slug}: ساختار ناشناخته: " + describe(payload))
    items = []
    for row in rows:
        sym = row.get("symbol")
        if not sym:
            continue
        levels = _path(row, "live.market.orders.levels", []) or []
        lv = levels[0] if levels else {}
        closing = _path(row, "live.market.prices.closing")
        closing_pct = _path(row, "live.market.changes.closingPercent")
        yesterday = closing / (1 + closing_pct / 100) if closing and closing_pct is not None and closing_pct > -100 else (closing or 0)
        item = {
            "namad": sym, "name": row.get("name") or sym,
            "type": "STOCK" if row.get("kind") == "STOCK" else SLUG_TYPE.get(slug, "ETF_LEVERAGE"),
            "queue": [{"buy": lv.get("bidPrice") or 0, "sell": lv.get("askPrice") or 0,
                       "buyVolume": lv.get("bidVolume") or 0, "sellVolume": lv.get("askVolume") or 0,
                       "buyCount": lv.get("bidCount") or 0, "sellCount": lv.get("askCount") or 0}] if lv else [],
            "closePrice": _path(row, "live.market.prices.close") or 0, "endPrice": closing or 0, "lastDayPrice": yesterday,
            "lowPrice": _path(row, "live.market.prices.low") or 0, "highPrice": _path(row, "live.market.prices.high") or 0,
            "volume": _path(row, "live.market.trading.volume") or 0, "turnover": _path(row, "live.market.trading.value") or 0,
        }
        flow = _path(row, "live.market.clientType.moneyFlowValue")
        if flow is not None:
            item["_real_net_value"] = flow
        items.append(item)
    return items


def pick_slugs(bases: List[str], stats: Optional[dict]) -> List[str]:
    meta = (stats or {}).get("by_name", {})
    aliases = (stats or {}).get("aliases", {})
    slugs: List[str] = []
    unknown = False
    for b in bases:
        key = norm(b)
        m = meta.get(aliases.get(key, key)) or {}
        if m.get("asset_type") == 1 and m.get("industry") is not None:
            slugs.append(f"{int(m['industry']):02d}")
        elif m.get("asset_type") == 2:
            slugs += SLUGS_BY_SUBTYPE.get(m.get("sub_type"), ALL_FUND_SLUGS)
        else:
            unknown = True
    if unknown:        # متادیتا نبود → همه‌ی صنایع و صندوق‌ها (مثل بورس‌ترکر)
        slugs = INDUSTRY_CODES + ALL_FUND_SLUGS
    return sorted(set(slugs))


def fetch_snapshots(base_url: str, slugs: List[str], timeframe: int = SNAPSHOT_TIMEFRAME) -> List[dict]:
    def one(slug: str):
        payload = http_get_json(f"{base_url}/data/industries/{slug}/snapshot",
                                params={"timeframe": timeframe, "_": int(time.time() * 1000)},
                                timeout=15, retries=1, label=f"snapshot {slug}")
        return parse_snapshot(payload, slug)

    items: List[dict] = []
    failed = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(one, s): s for s in slugs}
        for fut, slug in futs.items():
            try:
                items += fut.result()
            except DataError as e:
                if e.status in (401, 403):
                    raise
                failed.append(slug)
    if slugs and len(failed) > len(slugs) / 2:
        raise DataError(f"snapshot سهم/صندوق ناقص بود (ناموفق: {failed})")
    if failed:
        print(f"⚠️ snapshot ناموفق: {failed}")
    return items


# ───────────────────────── وضعیت بازار optionbaaz (برای دیتای بدون زمان) ─────────────────────────
STATE_CHILD_KEYS = ("TSE_STOCK", "IFB_STOCK", "TSE_ETF", "IFB_ETF")


def parse_market_state(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return None
    data = payload.get("data")
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return None
    found: Dict[str, bool] = {}
    for g in groups:
        for c in (g.get("children") or []) if isinstance(g, dict) else []:
            if isinstance(c, dict) and c.get("key") in STATE_CHILD_KEYS and isinstance(c.get("isOpen"), bool):
                found[c["key"]] = c["isOpen"]
    if any(found.values()):
        return "open"
    if len(found) == len(STATE_CHILD_KEYS):
        return "closed"
    return None


def fetch_market_state() -> Optional[str]:
    try:
        return parse_market_state(http_get_json(MARKET_STATE_URL, timeout=4, retries=0, label="market-state"))
    except DataError:
        return None


# ───────────────────────── /data/{insCode}/info : صف ۵ سطحی کامل + زمان + جریان پول ─────────────────────────
TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
MAX_PUT_INFO = 40
PREFILTER_SLACK_PCT = 3.0     # حاشیه‌ی پیش‌فیلتر قیمتی (٪) تا فرصتی به‌خاطر اختلاف last/ask جا نیفته


def info_timestamp(info: dict) -> Optional[str]:
    """datei (شمسی) + timei (HHMMSS تهران) → ISO UTC؛ برای تشخیص تازگی/تعطیلی."""
    try:
        y, m, d = (int(x) for x in str(info.get("datei")).translate(_DIGITS).split("/"))
        t = int(info.get("timei"))
        if not 1300 < y < 1600:
            return None
        gd = jalali_to_gregorian(y, m, d)
        dt = datetime(gd.year, gd.month, gd.day, t // 10000, (t // 100) % 100, t % 100, tzinfo=TEHRAN_TZ)
    except (TypeError, ValueError):
        return None
    return dt.astimezone(timezone.utc).isoformat()


def tradable(info: dict) -> bool:
    """state = وضعیت معامله‌ی نماد تو بورس (A = مجاز). محافظه‌کارانه: فقط A یا نبودن فیلد؛ بقیه‌ی کدها رد می‌شن و تو لاگ میان."""
    st = str(info.get("state") or "")
    return not st or st == "A"


def info_item(info: dict, namad: str, name: str, typ: str) -> Optional[dict]:
    if not tradable(info):
        return None
    queue = []
    for i in range(1, 6):
        bp, ap = info.get(f"bp{i}"), info.get(f"ap{i}")
        if bp is None and ap is None:
            break
        queue.append({"qn": i, "buy": bp or 0, "sell": ap or 0,
                      "buyVolume": info.get(f"bv{i}") or 0, "sellVolume": info.get(f"av{i}") or 0,
                      "buyCount": info.get(f"bc{i}") or 0, "sellCount": info.get(f"ac{i}") or 0})
    item = {
        "namad": namad, "name": name, "type": typ, "queue": queue,
        "closePrice": info.get("cl") or 0, "endPrice": info.get("c") or 0, "lastDayPrice": info.get("y") or 0,
        "openPrice": info.get("op") or 0, "highPrice": info.get("hi") or 0, "lowPrice": info.get("lo") or 0,
        "volume": info.get("vol") or 0, "turnover": info.get("val") or 0, "tradeCount": info.get("tc") or 0,
        "nav": info.get("nav") or 0, "lastTransaction": info_timestamp(info),
    }
    if typ != "OPTION":     # حد مجاز قیمت برای آپشن (۱ تا ۵۰۰٬۰۰۰) بی‌معنی است
        item["dailyPriceHigh"], item["dailyPriceLow"] = info.get("u") or 0, info.get("l") or 0
    if info.get("rbc") is not None or info.get("tr") is not None:
        item["traderStatistics"] = {
            "real": {"buy": {"count": info.get("rbc") or 0, "volume": info.get("rbv") or 0},
                     "sell": {"count": info.get("rsc") or 0, "volume": info.get("rsv") or 0}},
            "legal": {"buy": {"count": info.get("lbc") or 0, "volume": info.get("lbv") or 0},
                      "sell": {"count": info.get("lsc") or 0, "volume": info.get("lsv") or 0}}}
        if info.get("tr") is not None:
            item["_real_net_value"] = info["tr"]
        item["_per_capita_buy"], item["_per_capita_sell"] = info.get("buy"), info.get("sel")
        item["_buy_power"] = info.get("rt")
    return item


def fetch_info(base_url: str, ins_code: str) -> dict:
    payload = http_get_json(f"{base_url}/data/{ins_code}/info", params={"_": int(time.time() * 1000)},
                            timeout=12, retries=1, label=f"info {ins_code}")
    if not isinstance(payload, dict) or ("bp1" not in payload and "cl" not in payload):
        raise DataError(f"info {ins_code}: ساختار ناشناخته: " + describe(payload))
    return payload


def fetch_infos(base_url: str, ins_codes: List[str]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    if not ins_codes:
        return out

    def one(code: str):
        try:
            return code, fetch_info(base_url, code)
        except DataError as e:
            if e.status in (401, 403):
                raise
            print(f"⚠️ info {code} ناموفق: {str(e)[:120]}")
            return code, None

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for code, info in ex.map(one, ins_codes):
            if info is not None:
                out[code] = info
    return out


def meta_to_type(meta: dict) -> str:
    if meta.get("asset_type") == 2:
        return {3: "ETF_LEVERAGE", 8: "ETF_SECTORAL", 2: "ETF_EQUITY"}.get(meta.get("sub_type"), "ETF_LEVERAGE")
    return "STOCK"


def _proxy_candidate(r: dict, cfg: ScanConfig) -> bool:
    """پیش‌فیلتر ارزان فقط با قیمت‌های option-watch (قیمت پایه ≈ آخرین): کاندید برای گرفتن صف دقیق."""
    S, K, ask = r.get("base_price") or 0, r.get("strike") or 0, r.get("ask") or 0
    if not (S and K and ask > 0):
        return ask > 0
    cost = S * 1.0023 + ask * (1 + cfg.option_buy_fee)       # خوش‌بینانه: کارمزد ETF
    margin = (K * (1 - 0.0005) - cost) / cost * 100
    floor = -(cfg.max_loss_pct + PREFILTER_SLACK_PCT) if cfg.max_loss_pct > 0 else cfg.min_locked_margin_pct - PREFILTER_SLACK_PCT
    return margin >= floor


def fetch_tradersarena(base_url: Optional[str], stats: Optional[dict], cfg: ScanConfig,
                       today: Optional[date] = None) -> Tuple[List[dict], List[dict]]:
    """
    → (آیتم‌های قالب full.json برای scan، زنجیره‌ی کامل برای هستون).
    ۱) option-watch-csv  ۲) info پایه‌های کاندید (صف ۵ سطحی)  ۳) info پوت‌های فرصت (حجم صف پوت + زمان)
    جایگزین‌ها: پایه بدون id/info ← snapshot صنایع/صندوق‌ها؛ پوت بدون info ← قیمت option-watch با حجم نامعلوم.
    نمادی که state غیر A داشته باشه (ممنوع/متوقف) جایگزین نداره و رد می‌شه.
    """
    base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
    today = today or date.today()
    payload = http_get_json(f"{base_url}/data/option-watch-csv", params={"_": int(time.time() * 1000)},
                            label="option-watch-csv")
    recs = parse_option_watch(payload)
    puts = [r for r in recs if is_put(r.get("kind")) and (r.get("ask") or 0) > 0]
    if not puts:
        raise DataError("هیچ پوتی با قیمت عرضه تو option-watch-csv نبود")
    bases = sorted({r["base"] for r in puts if _proxy_candidate(r, cfg)})

    meta_all = (stats or {}).get("by_name", {})
    aliases = (stats or {}).get("aliases", {})
    codes: Dict[str, Tuple[str, dict]] = {}
    via_snapshot: List[str] = []                      # پایه‌هایی که با info نمی‌شه گرفت → snapshot
    for b in bases:
        key = norm(b)
        meta = meta_all.get(aliases.get(key, key)) or {}
        if meta.get("ins_code"):
            codes[b] = (str(meta["ins_code"]), meta)
        else:
            via_snapshot.append(b)
    if via_snapshot:
        print(f"ℹ️ id بورسی این پایه‌ها تو underlying-symbols نیست؛ با snapshot گرفته می‌شن: {via_snapshot}")

    infos = fetch_infos(base_url, sorted({c for c, _ in codes.values()}))
    stock_items: List[dict] = []
    skipped: Dict[str, str] = {}
    for b, (code, meta) in codes.items():
        info = infos.get(code)
        if info is None:
            print(f"ℹ️ info {b} نیومد؛ با snapshot گرفته می‌شه")
            via_snapshot.append(b)
            continue
        name = meta.get("namad") or b
        it = info_item(info, name, name, meta_to_type(meta))
        if it is None:
            skipped[name] = str(info.get("state"))        # ممنوع/متوقف: جایگزین نداره، رد می‌شه
        else:
            stock_items.append(it)
    if skipped:
        print(f"⚠️ پایه‌های رد‌شده به‌خاطر state غیر A (نماد: state): {skipped}")
    if via_snapshot:
        stock_items += fetch_snapshots(base_url, pick_slugs(via_snapshot, stats))

    have = {norm(i["namad"]) for i in stock_items}
    missing = [b for b in bases if norm(b) not in have and aliases.get(norm(b)) not in have and b not in skipped
               and (meta_all.get(aliases.get(norm(b), norm(b))) or {}).get("namad") not in skipped]
    if missing:
        print(f"⚠️ صف این پایه‌ها از هیچ راهی نیومد (پوت‌هاشون رد می‌شن): {missing}")
    if bases and len(missing) > len(bases) / 2:
        raise DataError(f"صف بیشتر پایه‌ها دریافت نشد ({len(missing)} از {len(bases)}): نام نمادها یا id فرق دارن؟")
    if not stock_items:
        raise DataError("صف هیچ پایه‌ای دریافت نشد")

    # مرحله‌ی ۳: فقط پوت‌های فرصت (اسکن شل) ← info با حجم صف و زمان
    all_items = stock_items + option_items(recs)
    loose = dataclasses.replace(
        cfg, min_position_value=0, min_contracts=0,
        max_loss_pct=cfg.max_loss_pct + 2.0 if cfg.max_loss_pct > 0 else 0.0,
        min_locked_margin_pct=cfg.min_locked_margin_pct - 0.5)
    syms = [o.put_symbol for o in scan(all_items, today, loose, stats)][:MAX_PUT_INFO]
    ids = {r["symbol"]: str(r["id"]) for r in recs if r.get("id")}
    p_infos = fetch_infos(base_url, [ids[s] for s in syms if s in ids])
    csv_puts = {it["namad"]: it for it in all_items if it["type"] == "OPTION"}
    put_items: List[dict] = []
    p_skipped: Dict[str, str] = {}
    for sym in syms:
        info, old = p_infos.get(ids.get(sym)), csv_puts.get(sym)
        if old is None:
            continue
        if info is None:
            print(f"ℹ️ info پوت {sym} نیومد؛ از قیمت‌های option-watch (حجم صف نامعلوم)")
            put_items.append(old)
            continue
        new = info_item(info, sym, old["name"], "OPTION")
        if new is None:
            p_skipped[sym] = str(info.get("state"))
            continue
        new["openInterest"], new["contractSize"] = old["openInterest"], old["contractSize"]
        put_items.append(new)
    if p_skipped:
        print(f"⚠️ پوت‌های رد‌شده به‌خاطر state غیر A: {p_skipped}")
    return stock_items + put_items, chain_from_records(recs)
