# -*- coding: utf-8 -*-
import html
import requests
from .protective_put import Opportunity, gregorian_to_jalali

_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
TG_LIMIT = 3900  # سقف تلگرام ۴۰۹۶ کاراکتره؛ با حاشیه


def fa(x) -> str:
    return str(x).translate(_FA)


def n(x: float) -> str:
    return fa(f"{x:,.0f}")


def pct(x: float, d: int = 2, sign: bool = False) -> str:
    return fa(f"{x:+.{d}f}" if sign else f"{x:.{d}f}") + "٪"


def toman_m(rial: float) -> str:       # ریال → میلیون تومان
    return fa(f"{rial / 1e7:,.0f}")


def toman_b(rial: float) -> str:       # ریال → میلیارد تومان
    return fa(f"{rial / 1e10:,.1f}")


def _tag(s: str) -> str:
    return "#" + html.escape(s.replace(" ", "_").replace(".", "_"))


def _notes(o: Opportunity) -> list:
    c, s, p = o.ctx, o.ctx["stock"], o.ctx["put"]
    out = []
    if o.days <= 3:
        out.append("سررسید خیلی نزدیکه؛ مهلت و ساعت ثبت درخواست اعمال رو از کارگزاری چک کن.")
    if p["volume"] == 0:
        out.append("پوت امروز معامله نشده؛ ممکنه bid/ask قدیمی باشه.")
    if p["ask_vol"] < 5:
        out.append(f"حجم عرضه‌ی پوت در بهترین قیمت فقط {fa(int(p['ask_vol']))} قرارداده؛ ممکنه کامل پر نشه.")
    if p["spread_pct"] is not None and p["spread_pct"] > 3:
        out.append(f"اسپرد پوت {pct(p['spread_pct'], 1)} هست؛ خروج زودتر از سررسید گرون تموم می‌شه، برای نگه‌داشتن تا اعمال مناسبه.")
    if s["day_max"] and s["ask"] >= s["day_max"] * 0.99:
        out.append("قیمت سهم نزدیک سقف مجاز روزه؛ ممکنه صف خرید بشه و سهم گیرت نیاد.")
    if s["day_min"] and s["ask"] <= s["day_min"] * 1.01:
        out.append("قیمت سهم نزدیک کف مجاز روزه.")
    if c.get("leveraged"):
        out.append("پایه صندوق اهرمیه؛ نوسان قیمتش بالاست و قیمت‌ها زود عوض می‌شن.")
    if c.get("hv") and c.get("iv") and c["iv"] > 2.5 * c["hv"] and o.days > 3:
        out.append("نوسان ضمنی پوت چند برابر HVه؛ برای پوت عمیقاً ITM معنی‌دار نیست ولی برای پوت‌های دیگه یعنی گرونه.")
    out.append("اروپایی: فقط در سررسید اعمال می‌شه و تا اون موقع پول بسته می‌مونه.")
    out.append("ترتیب خرید: اول پوت (کم‌عمق‌تره)، بعد سهم. کارمزدها تخمینی‌ان؛ با کارگزاری‌ات چک کن.")
    return out


def format_opportunity(o: Opportunity) -> str:
    c, s, p = o.ctx, o.ctx["stock"], o.ctx["put"]
    kind = s["kind"]
    if o.tier == "locked":
        head = "🟢 <b>پروتکتیو پوت | سود تضمینی بعد از کارمزد</b>"
        verdict = (f"حتی اگه {kind} صفر بشه، با اعمال پوت <b>{pct(o.margin_pct)}</b> سود می‌مونه "
                   f"(سالانه ≈ {fa(f'{o.annual_pct:.0f}')}٪)")
    else:
        head = "🟡 <b>پروتکتیو پوت | حدضرر ارزان</b>"
        if o.worst_per_share > 0:
            verdict = (f"بدترین حالت هم <b>{pct(o.margin_pct)}</b> سود می‌مونه (سالانه ≈ {fa(f'{o.annual_pct:.0f}')}٪، "
                       f"کمتر از آستانه‌ی «ناب»)؛ یعنی بیمه‌ی بدون هزینه و سود {kind} بالا بازه")
        else:
            verdict = f"بیشترین ضرر ممکن تا سررسید: <b>{pct(-o.margin_pct)}</b> و سود {kind} بالا بازه"

    S = []  # (priority, text) ؛ عدد کمتر = مهم‌تر
    S.append((0, "\n".join([
        head, f"{_tag(o.underlying)} | پوت {_tag(o.put_symbol)}", verdict])))

    S.append((0, "\n".join([
        "<b>📌 قرارداد</b>",
        f"strike: {n(o.strike)} | سررسید: {fa(gregorian_to_jalali(o.expiry))} ({fa(o.days)} روز)",
        f"strike نسبت به {kind}: {pct(o.moneyness_pct, 1, True)} | ارزش ذاتی پوت: {n(o.intrinsic)}",
        f"قیمت پوت نسبت به ارزش ذاتی: {n(c['premium_vs_intrinsic'])}"
        + (" (زیر ارزش ذاتی ✅)" if c["premium_vs_intrinsic"] < 0 else ""),
    ])))

    lines = [
        "<b>💰 محاسبه (هر سهم، ریال)</b>",
        f"خرید {kind} (ask): {n(o.stock_ask)} + کارمزد | خرید پوت (ask): {n(o.put_ask)} + کارمزد",
    ]
    # دریافتی اعمال = cost + worst
    lines.append(f"سرمایه‌ی لازم: {n(o.cost_per_share)} | دریافتی اعمال بعد از کارمزد: "
                 f"{n(o.cost_per_share + o.worst_per_share)}")
    fe = c["fees"]
    lines.append(f"کارمزد: خرید {kind} {pct(fe['buy'] * 100, 3)} | آپشن {pct(fe['option'] * 100, 2)} | اعمال {pct(fe['exercise'] * 100, 2)} | فروش بازاری {pct(fe['sell'] * 100, 3)}")
    lines.append(f"بدترین حالت: {n(o.worst_per_share)} ({pct(o.margin_pct, 2, True)}) | سالانه‌شده: {fa(f'{o.annual_pct:.0f}')}٪")
    rate_txt = fa(f"{c['rate'] * 100:.0f}")
    lines.append(f"هزینه‌ی فرصت پول با نرخ {rate_txt}٪: {pct(o.carry_pct, 1)} → خالص: <b>{pct(o.net_after_carry_pct, 2, True)}</b>")
    if c["breakeven"]:
        lines.append(f"سربه‌سر وقتی {kind} در سررسید برسه به {n(c['breakeven'])} ({pct(c['breakeven_chg_pct'], 1, True)} از الان)")
    S.append((0, "\n".join(lines)))

    vals = [v for _, v in c["scenarios"]]
    if max(vals) - min(vals) < 0.05:
        scen = (f"زیر strike ({n(o.strike)}) نتیجه همیشه {pct(vals[0], 2, True)} ثابته (اعمال پوت)"
                + (f"؛ فقط اگه {kind} بالاتر از {n(c['breakeven'])} بره سود می‌کنی." if c["breakeven"] else "."))
    else:
        scen = " | ".join(f"{fa(f'{chg:+d}')}٪: {pct(v, 1, True)}" for chg, v in c["scenarios"]) + f"\n(تغییر قیمت {kind} نسبت به الان)"
    S.append((2, "<b>📊 سناریو در سررسید (سود/زیان خالص)</b>\n" + scen))

    put = [f"<b>🧮 پوت {html.escape(o.put_symbol)}</b>",
           f"bid/ask: {n(p['bid'])} / {n(p['ask'])}" + (f" (اسپرد {pct(p['spread_pct'], 1)})" if p["spread_pct"] is not None else ""),
           f"حجم عرضه (ask): {n(p['ask_vol'])} قرارداد | حجم تقاضا (bid): {n(p['bid_vol'])}",
           f"آخرین/پایانی: {n(p['last'])} / {n(p['final'])} | دیروز: {n(p['yesterday'])}",
           f"معامله‌ی امروز: {n(p['volume'])} قرارداد، {toman_m(p['turnover'])} میلیون تومان، {n(p['trades'])} نوبت"]
    if c.get("wiv") or c.get("hv"):
        parts = [f"ضمنی پوت: {pct(c['iv'] * 100, 0)}" if c.get("iv") else None,
                 f"wiv پایه: {pct(c['wiv'] * 100, 0)}" if c.get("wiv") else None,
                 f"HV: {pct(c['hv'] * 100, 0)}" if c.get("hv") else None]
        put.append("نوسان: " + " | ".join(x for x in parts if x))
        if c.get("iv_rank") is not None or c.get("iv_pct") is not None:
            put.append(f"IV rank: {fa(c.get('iv_rank'))} | IV percentile: {fa(c.get('iv_pct'))}")
        for key, lab in (("wiv", "wiv"), ("hv", "HV")):
            if c.get(key + "_theo") and c.get(key + "_rich_pct") is not None:
                r_ = c[key + "_rich_pct"]
                put.append(f"قیمت تئوریک با {lab}: {n(c[key + '_theo'])} → پوت {pct(abs(r_), 1)} " + ("گرون‌تر ❌" if r_ > 0 else "ارزون‌تر ✅"))
    rich = [c[k] for k in ("wiv_rich_pct", "hv_rich_pct") if c.get(k) is not None]
    if len(rich) == 2:
        put.append("➜ " + ("پوت از هر دو مرجع ارزون‌تره (قیمت مناسب خرید)" if max(rich) < 0 else
                            "پوت از هر دو مرجع گرون‌تره" if min(rich) > 0 else "قیمت پوت بین wiv و HVه (منصفانه)"))
    if c.get("iv") and (c.get("wiv") or c.get("hv")):
        delta_txt = fa(f"{c['delta']:.2f}")
        put.append(f"دلتا: {delta_txt} | تتا روزانه: {n(c['theta_day'])} ریال")
    elif c.get("iv"):
        delta_txt = fa(f"{c['delta']:.2f}")
        put.append(f"نوسان ضمنی: {pct(c['iv'] * 100, 0)} | دلتا: {delta_txt} | تتا روزانه: {n(c['theta_day'])} ریال")
    S.append((1, "\n".join(put)))

    chg = (s["final"] / s["yesterday"] - 1) * 100 if s["yesterday"] else 0
    vr = s["volume"] / s["avg_month_vol"] if s["avg_month_vol"] else 0
    stock = [f"<b>📈 {kind} {html.escape(o.underlying)}</b>",
             f"bid/ask: {n(s['bid'])} / {n(s['ask'])} | حجم عرضه (ask): {n(s['ask_vol'])}",
             f"پایانی: {n(s['final'])} ({pct(chg, 1, True)}) | آخرین: {n(s['last'])} | محدوده امروز: {n(s['low'])} تا {n(s['high'])}",
             f"حجم: {n(s['volume'])} ({fa(f'{vr:.1f}')}× میانگین ماهانه) | ارزش: {toman_b(s['turnover'])} میلیارد تومان"]
    if s.get("nav"):
        stock.append(f"NAV{(' (' + fa(s['nav_date'][:10]) + ')') if s['nav_date'] else ''}: {n(s['nav'])} | قیمت خرید نسبت به NAV: {pct((s['ask'] / s['nav'] - 1) * 100, 2, True)}"
                     + (" (زیر NAV ✅)" if s["ask"] < s["nav"] else " (بالای NAV)"))
    S.append((1, "\n".join(stock)))

    S.append((2, "<b>💵 جریان پول</b>\n" +
              f"حقیقی خالص: {toman_b(s['real_net_value'])} میلیارد تومان ({'ورود' if s['real_net_value'] >= 0 else 'خروج'}) | "
              f"خریدار/فروشنده حقیقی: {n(s['real_buy_cnt'])} / {n(s['real_sell_cnt'])}"))

    cap = (f"<b>📦 ظرفیت اجرا (بهترین قیمت ask)</b>\n{fa(o.contracts)} قرارداد = {n(o.contracts * 1000)} سهم ≈ "
           f"{toman_m(o.position_value)} میلیون تومان | محدودکننده: حجم عرضه‌ی {'پوت' if c['limit_by'] == 'put' else kind}")
    S.append((0, cap))
    S.append((1, "<b>⚠️ نکات</b>\n" + "\n".join("• " + t for t in _notes(o))))

    # اگه طولانی شد، کم‌اهمیت‌ترین بخش‌ها (عدد بالاتر) حذف می‌شن
    while len("\n\n".join(t for _, t in S)) > TG_LIMIT and any(pr > 0 for pr, _ in S):
        worst_pr = max(pr for pr, _ in S)
        idx = max(i for i, (pr, _) in enumerate(S) if pr == worst_pr)
        S.pop(idx)
    return "\n\n".join(t for _, t in S)


def send(token: str, chat_id: str, text: str) -> bool:
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=15)
    if not r.ok:
        print(f"⚠️ telegram {r.status_code}: {r.text[:200]}")
    return r.ok
