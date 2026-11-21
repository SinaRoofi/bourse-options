# -*- coding: utf-8 -*-
"""--probe: ساختار و دسترسی منابع داده رو تو لاگ نشون می‌ده (فقط داده‌ی عمومی بازار؛ هیچ‌وقت هدر/توکن چاپ نمی‌شه)."""
import time
from typing import Optional

import requests

from .sources import DEFAULT_BASE_URL, MARKET_STATE_URL, UA, describe

OPTIONBAAZ_FULL = "https://api.optionbaaz.ir/market-data/option-snapshot/full?type=all&market=equity&includeClosed=1"
CALLPUT_CHAIN = "https://ov.callputoption.ir/get_chain"


def _one(label: str, url: str, params: Optional[dict], headers: Optional[dict]) -> None:
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    h.update(headers or {})
    print(f"== {label}")
    t0 = time.time()
    try:
        r = requests.get(url, params=params, headers=h, timeout=25)
    except requests.RequestException as e:
        print(f"   ❌ خطای شبکه: {type(e).__name__}: {e}")
        return
    ct = r.headers.get("content-type", "")
    print(f"   HTTP {r.status_code} | {len(r.content)} بایت | {time.time() - t0:.1f}s | {ct} | server={r.headers.get('server', '')}")
    try:
        j = r.json()
    except ValueError:
        print("   بدنه (۲۰۰ کاراکتر اول):", " ".join((r.text or "")[:200].split()))
        return
    print("   ساختار:", describe(j, 500))
    rows = None
    if isinstance(j, list):
        rows = j
    elif isinstance(j, dict):
        for k in ("rows", "data", "items", "result"):
            if isinstance(j.get(k), list):
                rows = j[k]
                print(f"   کلید لیست: {k} ({len(rows)} ردیف)")
                break
    if rows:
        first = rows[0]
        if isinstance(first, list):
            print(f"   ردیف اول: {len(first)} ستون: {first[:50]}")
        else:
            print("   ردیف اول:", repr(first)[:600])


def probe(base_url: Optional[str], optionbaaz_headers: Optional[dict]) -> None:
    base = (base_url or DEFAULT_BASE_URL).rstrip("/")
    ms = int(time.time() * 1000)
    _one("tradersarena option-watch-csv", f"{base}/data/option-watch-csv", {"_": ms}, None)
    _one("tradersarena snapshot leveraged-funds", f"{base}/data/industries/leveraged-funds/snapshot", {"timeframe": 12, "_": ms}, None)
    _one("tradersarena snapshot 57 (بانک‌ها)", f"{base}/data/industries/57/snapshot", {"timeframe": 12, "_": ms}, None)
    _one("tradersarena info (نمونه: id یک نماد)", f"{base}/data/22826119556531041/info", {"_": ms}, None)
    _one("callputoption get_chain", CALLPUT_CHAIN, None, None)
    _one("optionbaaz option-snapshot/full", OPTIONBAAZ_FULL, None, optionbaaz_headers)
    _one("optionbaaz market status", MARKET_STATE_URL, None, None)
