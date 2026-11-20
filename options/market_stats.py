# -*- coding: utf-8 -*-
"""
آمار نوسان پایه‌ها از optionbaaz:
  iv-stats           → wiv (نوسان ضمنی وزنی بازار آپشن همان پایه)، ivRank، ivPercentile
  underlying-symbols → hv (نوسان تاریخی)، tseOptionAliases (نام‌های جایگزین در نام قرارداد)، نوع دارایی

شکست دریافت این داده‌ها هیچ‌وقت اسکن اصلی را متوقف نمی‌کند؛ فقط بخش «نوسان» از پیام حذف می‌شود.
"""
import json
from typing import Dict, Optional

import requests

from .protective_put import norm

IV_URL = "https://api.optionbaaz.ir/options/underlying/iv-stats"
HV_URL = "https://api.optionbaaz.ir/options/underlying-symbols?market=equity"


def _get(url: str, headers: Optional[dict] = None, timeout: float = 10) -> Optional[dict]:
    try:
        r = requests.get(url, headers=headers or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"⚠️ market stats fetch failed ({url}): {e}")
        return None


def build_stats(iv_payload: Optional[dict], hv_payload: Optional[dict]) -> Dict[str, dict]:
    """
    خروجی: {"by_name": {norm(namad): {wiv, iv_rank, iv_pct, hv, leveraged, asset}},
            "aliases": {norm(alias): norm(namad)}}
    """
    by_name: Dict[str, dict] = {}
    aliases: Dict[str, str] = {}
    for row in (iv_payload or {}).get("data", []) or []:
        by_name.setdefault(norm(row.get("namad", "")), {}).update(
            wiv=row.get("wiv"), iv_rank=row.get("ivRank"), iv_pct=row.get("ivPercentile"))
    for row in (hv_payload or {}).get("data", []) or []:
        key = norm(row.get("namad", ""))
        by_name.setdefault(key, {}).update(
            hv=row.get("hv"), leveraged=(row.get("assetType") == 2 and row.get("subType") == 3),
            asset=row.get("assetType"))
        for a in row.get("tseOptionAliases") or []:
            aliases[norm(a)] = key
    return {"by_name": by_name, "aliases": aliases}


def load_stats(iv_url: str = IV_URL, hv_url: str = HV_URL, headers: Optional[dict] = None,
               iv_file: str = "", hv_file: str = "") -> Dict[str, dict]:
    def read(path):
        return json.load(open(path, encoding="utf-8")) if path else None
    iv = read(iv_file) if iv_file else _get(iv_url, headers)
    hv = read(hv_file) if hv_file else _get(hv_url, headers)
    return build_stats(iv, hv)
