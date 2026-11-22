# -*- coding: utf-8 -*-

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone

from options.errors import DataError
from options.market_stats import IV_URL, HV_URL, load_stats
from options.notify import format_opportunity, send, send_opportunity
from options.probe import probe
from options.protective_put import norm, scan
from options.settings import ConfigError, load_settings
from options.sources import fetch_market_state, fetch_tradersarena
from options.state import AlertState

try:   # هستون به numpy نیاز داره؛ نبودنش نباید کل اسکنر رو بخوابونه
    from options.heston import heston_for_put
except ImportError:
    heston_for_put = None

TEHRAN = timezone(timedelta(hours=3, minutes=30))
AUTH_HINT = ("ℹ️ tradersarena دسترسی رو رد کرد: یا IP گیت‌هاب بلاک شده یا این آدرس ورود می‌خواد. "
             "اگه ورود می‌خواد، هدر/کوکی مرورگر رو (فقط تو Secret) به‌صورت JSON تو OPTIONS_API_HEADERS بذار.")


def market_is_open(now: datetime, start=(9, 0), end=(12, 30)) -> bool:
    if now.weekday() in (3, 4):  # پنجشنبه/جمعه
        return False
    return start <= (now.hour, now.minute) <= end


def snapshot_age_minutes(data: list, now: datetime):
    """سن تازه‌ترین زمان به‌روزرسانی (دقیقه) از lastTransaction آیتم‌ها. None = هیچ زمان معتبری نبود."""
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


def report_data_error(e: DataError, token: str, chat: str, dry_run: bool) -> None:
    if e.status in (401, 403):
        print(AUTH_HINT)
        text = (f"⚠️ اسکنر آپشن: دسترسی به tradersarena رد شد (HTTP {e.status}). تا رفع این مشکل هشداری ارسال نمی‌شه. "
                "این پیام حداکثر هر ۶ ساعت تکرار می‌شه.")
        notify_ops("_auth_error", text, token, chat, dry_run)
    else:
        reason = " ".join(str(e).split())[:150]
        text = (f"⚠️ اسکنر آپشن: دریافت دیتا ناموفق بود ({reason}). تا برگشتن دیتا هشداری ارسال نمی‌شه؛ "
                "این پیام حداکثر هر ۳ ساعت تکرار می‌شه.")
        notify_ops("_data_error", text, token, chat, dry_run, cooldown_h=3)


def attach_heston(o, chain, rate, today, cache, aliases_rev) -> None:
    """کالیبراسیون هستون روی زنجیره‌ی همان پایه (با نرخ rate خودمون) → قیمت تئوریک پوت. هیچ‌وقت هشدار رو نمی‌خوابونه."""
    if heston_for_put is None:
        print("⚠️ numpy نصب نیست؛ هستون محاسبه نشد (pip install numpy)")
        return
    try:
        s = o.ctx["stock"]
        S = (s["bid"] + s["ask"]) / 2 if s["bid"] > 0 else s["last"] or s["ask"]
        for name in [o.underlying] + aliases_rev.get(norm(o.underlying), []):
            res = heston_for_put(chain, name, S, o.strike, o.days / 365.0, o.put_ask, rate, today, cache)
            if res:
                res.pop("params", None)
                o.ctx["heston"] = res
                return
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ هستون برای {o.put_symbol} محاسبه نشد: {type(e).__name__}: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ping", action="store_true", help="فقط پیام تست اتصال به کانال")
    ap.add_argument("--probe", action="store_true", help="فقط ساختار/دسترسی منابع داده رو تو لاگ نشون بده")
    ap.add_argument("--dry-run", action="store_true", help="پیام‌ها رو فقط چاپ کن")
    ap.add_argument("--force", action="store_true", help="چک ساعت بازار و تازگی دیتا رو رد کن")
    a = ap.parse_args()
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("OPTIONS_CHAT_ID", "")

    if a.ping:
        if not (token and chat):
            print("❌ TELEGRAM_BOT_TOKEN یا OPTIONS_CHAT_ID تنظیم نیست")
            return 1
        ok = send(token, chat, "✅ اسکنر پروتکتیو پوت به کانال وصل شد.")
        print("✅ پیام تست ارسال شد" if ok else "❌ ارسال ناموفق (ربات ادمین کانال هست؟ chat id درسته؟)")
        return 0 if ok else 1

    if a.probe:
        probe(os.getenv("TRADERSARENA_BASE_URL") or None)
        return 0

    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"❌ config.py: {e}")
        return 1
    cfg = settings.scan

    now = datetime.now(TEHRAN)
    if not (a.force or market_is_open(now, settings.market_start, settings.market_end)):
        print("⏸️ بازار بسته است")
        return 0
    today = now.date()

    # iv-stats و underlying-symbols (عمومی): wiv/HV، نوع پایه و id بورسی هر پایه
    stats = load_stats(IV_URL, HV_URL)
    try:
        data, chain = fetch_tradersarena(os.getenv("TRADERSARENA_BASE_URL") or None, stats, cfg, today)
    except DataError as e:
        print(f"❌ {e}")
        report_data_error(e, token, chat, a.dry_run)
        # کد خروج ۰: وگرنه GitHub برای هر دقیقه یه ایمیل «workflow failed» می‌فرسته؛ خطا تو لاگ و تلگرام اطلاع داده شد
        return 0

    if not a.force:
        age = snapshot_age_minutes(data, datetime.now(timezone.utc))
        if age is None:
            # هیچ آیتمی زمان نداشت (فقط snapshot): وضعیت بازار optionbaaz تعطیلی وسط هفته رو می‌گیره (best-effort)
            if fetch_market_state() == "closed":
                print("⏸️ وضعیت بازار: بسته (تعطیلی)")
                return 0
        elif age > settings.max_stale_min:
            print(f"⏸️ دیتا تازه نیست (آخرین به‌روزرسانی {age:.0f} دقیقه پیش) — تعطیلی یا بازار بسته")
            return 0

    opps = scan(data, today, cfg, stats)
    print(f"🔎 {len(opps)} فرصت پیدا شد (آستانه: locked≥{cfg.min_locked_margin_pct}% / max_loss≤{cfg.max_loss_pct}%)")

    aliases_rev: dict = {}
    for alias, namad in (stats or {}).get("aliases", {}).items():
        aliases_rev.setdefault(namad, []).append(alias)
    hcache: dict = {}
    state = AlertState(os.getenv("GIST_ID", ""), os.getenv("GIST_TOKEN", ""))
    state.load()
    sent = 0
    for o in opps:
        if sent >= settings.max_alerts:
            break
        key = f"{o.put_symbol}:{o.tier}"
        if not state.should_alert(key, o.margin_pct, settings.cooldown_hours, settings.improve_pp):
            continue
        if settings.heston_enabled and chain:
            attach_heston(o, chain, cfg.rate, today, hcache, aliases_rev)
        text = format_opportunity(o)
        if a.dry_run or not (token and chat):
            print("─" * 40 + "\n" + text)
            if not a.dry_run:
                print("⚠️ TELEGRAM_BOT_TOKEN/OPTIONS_CHAT_ID تنظیم نیست")
        elif not send_opportunity(token, chat, o):
            continue
        state.mark(key, o.margin_pct)
        sent += 1
    if not a.dry_run and state.dirty:
        state.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
