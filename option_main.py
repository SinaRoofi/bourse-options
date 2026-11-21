# -*- coding: utf-8 -*-

import argparse, json, os, sys, time
from dataclasses import fields
from datetime import datetime, date, timedelta, timezone

import requests

from options.protective_put import scan
from options.config import load_settings, ConfigError
from options.errors import DataError, response_diag
from options.sources import fetch_tradersarena, fetch_market_state, chain_from_items
from options.probe import probe
from options.state import AlertState
from options.notify import format_opportunity, send
from options.market_stats import load_stats, IV_URL, HV_URL

DEFAULT_DATA_URL = "https://api.optionbaaz.ir/market-data/option-snapshot/full?type=all&market=equity&includeClosed=1"
TEHRAN = timezone(timedelta(hours=3, minutes=30))


def market_is_open(now: datetime) -> bool:
    # شنبه..چهارشنبه، ۹:۰۰ تا ۱۲:۳۰ تهران (تعطیلات رسمی رو API/ساعت بدون داده پوشش می‌ده)
    if now.weekday() in (3, 4):  # پنجشنبه/جمعه
        return False
    return (9, 0) <= (now.hour, now.minute) <= (12, 30)


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


AUTH_HINT = ("ℹ️ این endpoint نیاز به ورود دارد. وقتی تو مرورگر لاگین هستی، هدر Authorization (یا Cookie) درخواست "
             "option-snapshot رو از DevTools بردار و تو Secret بذار: OPTIONS_API_TOKEN یا OPTIONS_API_COOKIE (SETUP.md بخش ۶).")


def build_headers() -> dict:
    h = {"User-Agent": "Mozilla/5.0 (compatible; option-scanner/1.0)", "Accept": "application/json"}
    token = (os.getenv("OPTIONS_API_TOKEN") or "").strip()
    if token:
        h["Authorization"] = token if token.lower().startswith(("bearer ", "basic ")) else "Bearer " + token
    cookie = (os.getenv("OPTIONS_API_COOKIE") or "").strip()
    if cookie:
        h["Cookie"] = cookie
    try:
        h.update(json.loads(os.getenv("OPTIONS_API_HEADERS") or "{}"))
    except json.JSONDecodeError:
        raise DataError("OPTIONS_API_HEADERS باید JSON معتبر باشه")
    return h


def load_data(path: str) -> list:
    if path:
        with open(path, encoding="utf-8") as fh:
            return validate(json.load(fh))
    url = os.getenv("OPTIONS_API_URL") or DEFAULT_DATA_URL
    headers = build_headers()
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code in (401, 403):   # تکرار فایده ندارد؛ توکن/دسترسی مشکل دارد
                raise DataError(f"دسترسی به API رد شد: {response_diag(r)}", status=r.status_code)
            r.raise_for_status()
            return validate(r.json())
        except DataError as e:
            if e.status in (401, 403):
                raise
            last = e
        except Exception as e:  # شبکه/JSON/ساختار
            last = e
        time.sleep(2 * (attempt + 1))
    raise DataError(f"دریافت دیتا ناموفق بود: {last}")


def notify_ops(key: str, text: str, token: str, chat: str, dry_run: bool, cooldown_h: float = 6) -> None:
    """پیام مشکل عملیاتی (دسترسی/دیتا) به کانال؛ حداکثر هر cooldown_h ساعت یک بار برای هر key."""
    state = AlertState(os.getenv("GIST_ID", ""), os.getenv("GIST_TOKEN", ""))
    state.load()
    if not state.should_alert(key, 0.0, cooldown_h, 1e9):
        return
    if dry_run or not (token and chat):
        print(text)
    elif not send(token, chat, text):
        return
    state.mark(key, 0.0)
    if not dry_run:
        state.save()


def report_data_error(e: "DataError", token: str, chat: str, dry_run: bool) -> None:
    if e.status in (401, 403):
        print(AUTH_HINT)
        text = (f"⚠️ اسکنر آپشن: دسترسی به API دیتا رد شد (HTTP {e.status}). توکن/کوکی منقضی یا نادرسته؛ "
                "تا رفع این مشکل هشداری ارسال نمی‌شه. این پیام حداکثر هر ۶ ساعت تکرار می‌شه.")
        notify_ops("_auth_error", text, token, chat, dry_run)
    else:
        reason = " ".join(str(e).split())[:150]
        text = (f"⚠️ اسکنر آپشن: دریافت دیتا ناموفق بود ({reason}). تا برگشتن API هشداری ارسال نمی‌شه؛ "
                "این پیام حداکثر هر ۳ ساعت تکرار می‌شه.")
        notify_ops("_data_error", text, token, chat, dry_run, cooldown_h=3)


def fetch_dataset(a, settings, stats, today=None):
    """→ (آیتم‌ها به قالب full.json، زنجیره‌ی کامل کال/پوت برای هستون، برچسب منبع)."""
    if a.file:
        data = load_data(a.file)
        return data, chain_from_items(data), "file"
    src = settings.data_source
    first_err = None
    if src in ("tradersarena", "auto"):
        try:
            data, chain = fetch_tradersarena(os.getenv("TRADERSARENA_BASE_URL") or None, stats, settings.scan, today)
            return data, chain, "tradersarena"
        except DataError as e:
            if src == "tradersarena":
                raise
            first_err = e
            print(f"⚠️ tradersarena ناموفق، می‌رم سراغ optionbaaz: {e}")
    try:
        data = load_data("")
    except DataError as e:
        if first_err is not None:
            raise DataError(f"tradersarena: {first_err} | optionbaaz: {e}", status=e.status or first_err.status)
        raise
    return data, chain_from_items(data), "optionbaaz"


def attach_heston(o, chain, rate, today, cache, aliases_rev) -> None:
    """کالیبراسیون هستون روی زنجیره‌ی همان پایه (با نرخ rate خودمون) → قیمت تئوریک پوت. هیچ‌وقت هشدار رو نمی‌خوابونه."""
    try:
        from options.heston import heston_for_put
        s = o.ctx["stock"]
        S = (s["bid"] + s["ask"]) / 2 if s["bid"] > 0 else s["last"] or s["ask"]
        names = [o.underlying] + aliases_rev.get(o.underlying, [])
        for name in names:
            res = heston_for_put(chain, name, S, o.strike, o.days / 365.0, o.put_ask, rate, today, cache)
            if res:
                res.pop("params", None)
                o.ctx["heston"] = res
                return
    except ImportError:
        print("⚠️ numpy نصب نیست؛ هستون محاسبه نشد (pip install numpy)")
    except Exception as e:   # noqa: BLE001
        print(f"⚠️ هستون برای {o.put_symbol} محاسبه نشد: {type(e).__name__}: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file"); ap.add_argument("--asof")
    ap.add_argument("--ping", action="store_true", help="فقط پیام تست اتصال به کانال")
    ap.add_argument("--probe", action="store_true", help="فقط ساختار و دسترسی منابع داده رو تو لاگ نشون بده")
    ap.add_argument("--iv-file", default=""); ap.add_argument("--hv-file", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="چک ساعت بازار رو رد کن")
    a = ap.parse_args()

    if a.ping:
        token, chat = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("OPTIONS_CHAT_ID", "")
        if not (token and chat):
            print("❌ TELEGRAM_BOT_TOKEN یا OPTIONS_CHAT_ID تنظیم نیست"); return 1
        ok = send(token, chat, "✅ اسکنر پروتکتیو پوت به کانال وصل شد.")
        print("✅ پیام تست ارسال شد" if ok else "❌ ارسال ناموفق (ربات ادمین کانال هست؟ chat id درسته؟)")
        return 0 if ok else 1

    if a.probe:
        probe(os.getenv("TRADERSARENA_BASE_URL") or None, build_headers())
        return 0

    now = datetime.now(TEHRAN)
    if not (a.force or a.file or market_is_open(now)):
        print("⏸️ بازار بسته است"); return 0

    today = date.fromisoformat(a.asof) if a.asof else now.date()
    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"❌ تنظیمات: {e}")
        return 1
    cfg = settings.scan
    # iv-stats و underlying-symbols عمومی‌ان؛ توکن رو بهشون نمی‌فرستیم (توکن منقضی نباید اونا رو هم بخوابونه)
    stats = load_stats(os.getenv("OPTIONS_IV_URL", IV_URL), os.getenv("OPTIONS_HV_URL", HV_URL), None, a.iv_file, a.hv_file)
    try:
        data, chain, source = fetch_dataset(a, settings, stats, today)
    except DataError as e:
        print(f"❌ {e}")
        if not a.file:
            report_data_error(e, os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("OPTIONS_CHAT_ID", ""), a.dry_run)
        # کد خروج ۰: وگرنه GitHub برای هر دقیقه یه ایمیل «workflow failed» می‌فرسته؛ خطا تو لاگ و تلگرام اطلاع داده شد
        return 0
    print(f"📥 منبع داده: {source}")
    if not (a.file or a.force):
        age = snapshot_age_minutes(data, datetime.now(timezone.utc))
        if age is None and source == "tradersarena":
            # info زمان ندارد (فقط snapshot استفاده شد): وضعیت بازار (best-effort) تعطیلی وسط هفته رو می‌گیره
            if fetch_market_state() == "closed":
                print("⏸️ وضعیت بازار: بسته (تعطیلی)")
                return 0
        elif age is None or age > settings.max_stale_min:
            print(f"⏸️ دیتا تازه نیست (آخرین معامله {'نامشخص' if age is None else f'{age:.0f} دقیقه'} پیش) — تعطیلی یا بازار بسته")
            return 0
    opps = scan(data, today, cfg, stats)
    print(f"🔎 {len(opps)} فرصت پیدا شد (آستانه: locked≥{cfg.min_locked_margin_pct}% / max_loss≤{cfg.max_loss_pct}%)")

    aliases_rev = {}
    for alias, namad in (stats or {}).get("aliases", {}).items():
        aliases_rev.setdefault(namad, []).append(alias)
    hcache: dict = {}
    state = AlertState(os.getenv("GIST_ID", ""), os.getenv("GIST_TOKEN", ""))
    state.load()
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("OPTIONS_CHAT_ID", "")
    sent = 0
    for o in opps:
        if sent >= settings.max_alerts:
            break
        key = f"{o.put_symbol}:{o.tier}"
        if not state.should_alert(key, o.margin_pct, settings.cooldown_hours, settings.improve_pp):
            continue
        if settings.heston_enabled and chain:
            from options.protective_put import norm
            attach_heston(o, chain, cfg.rate, today, hcache, {norm(k): v for k, v in aliases_rev.items()})
        text = format_opportunity(o)
        if a.dry_run or not (token and chat):
            print("─" * 40 + "\n" + text)
            if not a.dry_run:
                print("⚠️ TELEGRAM_BOT_TOKEN/OPTIONS_CHAT_ID تنظیم نیست")
        elif not send(token, chat, text):
            continue
        state.mark(key, o.margin_pct); sent += 1
    if not a.dry_run and state.dirty:
        state.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
