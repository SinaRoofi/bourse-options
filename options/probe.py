# -*- coding: utf-8 -*-
"""--probe: ساختار و دسترسی منابع داده رو تو لاگ نشون می‌ده (فقط داده‌ی عمومی بازار؛ هیچ‌وقت هدر/توکن چاپ نمی‌شه)."""
import time
from typing import Optional

import requests

from .market_stats import HV_URL, IV_URL
from .sources import DEFAULT_BASE_URL, MARKET_STATE_URL, UA, describe, extra_headers


def _one(label: str, url: str, params: Optional[dict]) -> None:
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    h.update(extra_headers())
    print(f"== {label}")
    t0 = time.time()
    try:
        r = requests.get(url, params=params, headers=h, timeout=25)
    except requests.RequestException as e:
        print(f"   ❌ خطای شبکه: {type(e).__name__}: {e}")
        return
    print(f"   HTTP {r.status_code} | {len(r.content)} بایت | {time.time() - t0:.1f}s | "
          f"{r.headers.get('content-type', '')} | server={r.headers.get('server', '')}")
    try:
        j = r.json()
    except ValueError:
        print("   بدنه (۲۰۰ کاراکتر اول):", " ".join((r.text or "")[:200].split()))
        return
    print("   ساختار:", describe(j, 500))
    rows = j if isinstance(j, list) else next((j[k] for k in ("rows", "data", "items", "result")
                                              if isinstance(j, dict) and isinstance(j.get(k), list)), None)
    if rows:
        first = rows[0]
        print(f"   ردیف اول: {len(first)} ستون: {first[:50]}" if isinstance(first, list) else f"   ردیف اول: {repr(first)[:600]}")


def probe(base_url: Optional[str]) -> None:
    base = (base_url or DEFAULT_BASE_URL).rstrip("/")
    ms = int(time.time() * 1000)
    _one("tradersarena option-watch-csv", f"{base}/data/option-watch-csv", {"_": ms})
    _one("tradersarena info (نمونه: id یک نماد)", f"{base}/data/22826119556531041/info", {"_": ms})
    _one("tradersarena snapshot (جایگزین) leveraged-funds", f"{base}/data/industries/leveraged-funds/snapshot", {"timeframe": 12, "_": ms})
    _one("optionbaaz underlying-symbols (id و نوع پایه‌ها)", HV_URL, None)
    _one("optionbaaz iv-stats (wiv)", IV_URL, None)
    _one("optionbaaz وضعیت بازار", MARKET_STATE_URL, None)
