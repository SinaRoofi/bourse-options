# -*- coding: utf-8 -*-

import argparse, json, os, sys, time
from dataclasses import fields
from datetime import datetime, date, timedelta, timezone

import requests

from options.protective_put import ScanConfig, scan
from options.state import AlertState
from options.notify import format_opportunity, send
from options.market_stats import load_stats, IV_URL, HV_URL

DEFAULT_DATA_URL = "https://api.optionbaaz.ir/market-data/option-snapshot/full?type=all&market=equity&includeClosed=1"
TEHRAN = timezone(timedelta(hours=3, minutes=30))
MAX_STALE_MIN = float(os.getenv("OPT_MAX_STALE_MIN", "20"))   # قدیمی‌تر از این = دیتای بازار بسته/تعطیل
COOLDOWN_HOURS = float(os.getenv("OPT_COOLDOWN_HOURS", "3"))   # تکرار هشدار همون پوت
IMPROVE_PP = float(os.getenv("OPT_IMPROVE_PP", "0.25"))        # یا اگه حاشیه این‌قدر (درصدواحد) بهتر شد
MAX_ALERTS_PER_RUN = int(os.getenv("OPT_MAX_ALERTS", "8"))


def cfg_from_env() -> ScanConfig:
    cfg = ScanConfig()
    for f in fields(ScanConfig):
        v = os.getenv("OPT_" + f.name.upper())
        if v not in (None, ""):
            setattr(cfg, f.name, type(getattr(cfg, f.name))(float(v)))
    return cfg


def market_is_open(now: datetime) -> bool:
    # شنبه..چهارشنبه، ۹:۰۰ تا ۱۲:۳۰ تهران (تعطیلات رسمی رو API/ساعت بدون داده پوشش می‌ده)
    if now.weekday() in (3, 4):  # پنجشنبه/جمعه
        return False
    return (9, 0) <= (now.hour, now.minute) <= (12, 30)


class DataError(Exception):
    pass


def validate(payload) -> list:
    """ساختار پاسخ باید همان full.json باشد: {status, data:[{namad,type,queue,...}]}."""
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise DataError(f"پاسخ نامعتبر: status={payload.get('status') if isinstance(payload, dict) else type(payload)}")
    data = payload.get("data")
    if not isinstance(data, list) or len(data) < 50:
        raise DataError("فهرست نمادها خالی/ناقصه")
    if not any(x.get("type") == "OPTION" for x in data) or not any(x.get("type") != "OPTION" for x in data):
        raise DataError("نماد آپشن یا پایه در پاسخ نیست (ساختار API عوض شده؟)")
    return data


def snapshot_age_minutes(data: list, now: datetime):
    """سن تازه‌ترین معامله‌ی ثبت‌شده (دقیقه). None = هیچ زمان معتبری نبود."""
    newest = None
    for x in data:
        ts = x.get("lastTransaction")
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        newest = t if newest is None or t > newest else newest
    return None if newest is None else (now - newest).total_seconds() / 60


def load_data(path: str) -> list:
    if path:
        return validate(json.load(open(path, encoding="utf-8")))
    url = os.getenv("OPTIONS_API_URL") or DEFAULT_DATA_URL
    headers = {"User-Agent": "Mozilla/5.0 (compatible; option-scanner/1.0)", "Accept": "application/json"}
    headers.update(json.loads(os.getenv("OPTIONS_API_HEADERS") or "{}"))
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            return validate(r.json())
        except Exception as e:  # شبکه/JSON/ساختار
            last = e
            time.sleep(2 * (attempt + 1))
    raise DataError(f"دریافت دیتا ناموفق بود: {last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file"); ap.add_argument("--asof"); ap.add_argument("--ping", action="store_true", help="فقط پیام تست اتصال به کانال"); ap.add_argument("--iv-file", default=""); ap.add_argument("--hv-file", default=""); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="چک ساعت بازار رو رد کن")
    a = ap.parse_args()

    if a.ping:
        token, chat = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("OPTIONS_CHAT_ID", "")
        if not (token and chat):
            print("❌ TELEGRAM_BOT_TOKEN یا OPTIONS_CHAT_ID تنظیم نیست"); return 1
        ok = send(token, chat, "✅ اسکنر پروتکتیو پوت به کانال وصل شد.")
        print("✅ پیام تست ارسال شد" if ok else "❌ ارسال ناموفق (ربات ادمین کانال هست؟ chat id درسته؟)")
        return 0 if ok else 1

    now = datetime.now(TEHRAN)
    if not (a.force or a.file or market_is_open(now)):
        print("⏸️ بازار بسته است"); return 0

    today = date.fromisoformat(a.asof) if a.asof else now.date()
    cfg = cfg_from_env()
    hdr = json.loads(os.getenv("OPTIONS_API_HEADERS") or "{}")
    stats = load_stats(os.getenv("OPTIONS_IV_URL", IV_URL), os.getenv("OPTIONS_HV_URL", HV_URL), hdr, a.iv_file, a.hv_file)
    try:
        data = load_data(a.file)
    except DataError as e:
        print(f"❌ {e}")
        return 1
    if not (a.file or a.force):
        age = snapshot_age_minutes(data, datetime.now(timezone.utc))
        if age is None or age > MAX_STALE_MIN:
            print(f"⏸️ دیتا تازه نیست (آخرین معامله {'نامشخص' if age is None else f'{age:.0f} دقیقه'} پیش) — تعطیلی یا بازار بسته")
            return 0
    opps = scan(data, today, cfg, stats)
    print(f"🔎 {len(opps)} فرصت پیدا شد (آستانه: locked≥{cfg.min_locked_margin_pct}% / max_loss≤{cfg.max_loss_pct}%)")

    state = AlertState(os.getenv("GIST_ID", ""), os.getenv("GIST_TOKEN", ""))
    state.load()
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("OPTIONS_CHAT_ID", "")
    sent = 0
    for o in opps:
        if sent >= MAX_ALERTS_PER_RUN:
            break
        key = f"{o.put_symbol}:{o.tier}"
        if not state.should_alert(key, o.margin_pct, COOLDOWN_HOURS, IMPROVE_PP):
            continue
        text = format_opportunity(o)
        if a.dry_run or not (token and chat):
            print("─" * 40 + "\n" + text)
            if not a.dry_run:
                print("⚠️ TELEGRAM_BOT_TOKEN/OPTIONS_CHAT_ID تنظیم نیست")
        elif not send(token, chat, text):
            continue
        state.mark(key, o.margin_pct); sent += 1
    if not a.dry_run:
        state.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
