# -*- coding: utf-8 -*-
import html
import re

import requests
from .protective_put import Opportunity, gregorian_to_jalali

_FA = str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹٫")
TG_LIMIT = 3900  # سقف تلگرام ۴۰۹۶ کاراکتره؛ با حاشیه


def fa(x) -> str:
    if x is None:
        return "—"
    s = str(x).translate(_FA)
    # اعداد علامت‌دار (+۱٫۲ / -۳): علامت تو متن راست‌به‌چپ بعد از عدد نیفته
    return "\u200e" + s if s[:1] in "+-" else s


def n(x: float) -> str:
    if round(x) == 0:      # «-۰» نشون داده نشه
        x = 0
    return fa(f"{x:,.0f}")


def pct(x: float, d: int = 2, sign: bool = False) -> str:
    if round(x, d) == 0:
        x = 0.0
    return fa(f"{x:+.{d}f}" if sign and x else f"{x:.{d}f}") + "٪"


def rate_txt(rate: float) -> str:
    """نرخ بهره بدون گرد شدن اضافه: ۴۳٫۵ نه ۴۴؛ ۴۲ نه ۴۲٫۰."""
    return fa(f"{rate * 100:.1f}".rstrip("0").rstrip("."))


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
    if not p["turnover"]:   # بدون معامله = ارزش معامله صفر
        out.append("پوت امروز معامله نشده (ارزش معامله ۰)؛ ممکنه قیمت‌های خرید و فروش قدیمی باشه.")
    if p["ask_vol_known"] and p["ask_vol"] < 5:
        out.append(f"حجم سرخط فروش پوت فقط {fa(int(p['ask_vol']))} قرارداده؛ ممکنه کامل پر نشه.")
    if p["spread_pct"] is not None and p["spread_pct"] > 3:
        out.append(f"فاصله‌ی قیمت خرید و فروش پوت {pct(p['spread_pct'], 1)} هست؛ خروج زودتر از سررسید گرون تموم می‌شه، برای نگه‌داشتن تا اعمال مناسبه.")
    if s["day_max"] and s["ask"] >= s["day_max"] * 0.99:
        out.append(f"قیمت {s['kind']} نزدیک سقف مجاز روزه؛ ممکنه صف خرید بشه و {s['kind']} گیرت نیاد.")
    if s["day_min"] and s["ask"] <= s["day_min"] * 1.01:
        out.append(f"قیمت {s['kind']} نزدیک کف مجاز روزه.")
    if c.get("leveraged"):
        out.append("پایه صندوق اهرمیه؛ نوسان قیمتش بالاست و قیمت‌ها زود عوض می‌شن.")
    if c.get("hv") and c.get("iv") and c["iv"] > 2.5 * c["hv"] and o.days > 3:
        out.append("نوسان ضمنی پوت چند برابر نوسان تاریخیه؛ برای پوت عمیقاً ITM معنی‌دار نیست ولی برای پوت‌های دیگه یعنی گرونه.")
    out.append("اروپایی: فقط در سررسید اعمال می‌شه و تا اون موقع پول بسته می‌مونه.")
    out.append("ترتیب خرید: اول پوت (کم‌عمق‌تره)، بعد سهم. کارمزدها تخمینی‌ان؛ با کارگزاری‌ات چک کن.")
    return out


def format_opportunity_classic(o: Opportunity) -> str:
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
    lines.append(f"هزینه‌ی فرصت پول با نرخ {rate_txt(c['rate'])}٪: {pct(o.carry_pct, 1)} → خالص: <b>{pct(o.net_after_carry_pct, 2, True)}</b>")
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
           (f"حجم عرضه (ask): {n(p['ask_vol'])} قرارداد | حجم تقاضا (bid): {n(p['bid_vol'])}" if p["ask_vol_known"]
            else "حجم صف bid/ask پوت تو این منبع نیست؛ قبل از خرید صف رو تو کارگزاری چک کن."),
           f"آخرین/پایانی: {n(p['last'])} / {n(p['final'])} | دیروز: {n(p['yesterday'])}",
           (f"موقعیت باز: {n(p['open_interest'])} قرارداد" if p.get("open_interest") else None),
           f"معامله‌ی امروز: {n(p['volume'])} قرارداد، {toman_m(p['turnover'])} میلیون تومان" + (f"، {n(p['trades'])} نوبت" if p["trades"] else "")]
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
    h = c.get("heston")
    if h:
        put.append(f"هستون (کالیبره روی {fa(h['n_points'])} قرارداد، خطای برازش {pct(h['rmse_rel'] * 100, 0)}): تئوریک {n(h['price'])} → پوت "
                   + f"{pct(abs(h['rich_pct']), 1)} " + ("گرون‌تر ❌" if h["rich_pct"] > 0 else "ارزون‌تر ✅"))
    if c.get("iv") and (c.get("wiv") or c.get("hv")):
        delta_txt = fa(f"{c['delta']:.2f}")
        put.append(f"دلتا: {delta_txt} | تتا روزانه: {n(c['theta_day'])} ریال")
    elif c.get("iv"):
        delta_txt = fa(f"{c['delta']:.2f}")
        put.append(f"نوسان ضمنی: {pct(c['iv'] * 100, 0)} | دلتا: {delta_txt} | تتا روزانه: {n(c['theta_day'])} ریال")
    S.append((1, "\n".join(x for x in put if x)))

    chg = (s["final"] / s["yesterday"] - 1) * 100 if s["yesterday"] else 0
    vr = s["volume"] / s["avg_month_vol"] if s["avg_month_vol"] else 0
    vol_txt = f"حجم: {n(s['volume'])}" + (f" ({fa(f'{vr:.1f}')}× میانگین ماهانه)" if s["avg_month_vol"] else "")
    stock = [f"<b>📈 {kind} {html.escape(o.underlying)}</b>",
             f"bid/ask: {n(s['bid'])} / {n(s['ask'])} | حجم عرضه (ask): {n(s['ask_vol'])}",
             f"پایانی: {n(s['final'])} ({pct(chg, 1, True)}) | آخرین: {n(s['last'])} | محدوده امروز: {n(s['low'])} تا {n(s['high'])}",
             f"{vol_txt} | ارزش: {toman_b(s['turnover'])} میلیارد تومان"]
    if s.get("nav"):
        stock.append(f"NAV{(' (' + fa(s['nav_date'][:10]) + ')') if s['nav_date'] else ''}: {n(s['nav'])} | قیمت خرید نسبت به NAV: {pct((s['ask'] / s['nav'] - 1) * 100, 2, True)}"
                     + (" (زیر NAV ✅)" if s["ask"] < s["nav"] else " (بالای NAV)"))
    S.append((1, "\n".join(stock)))

    if s.get("has_flow"):
        flow = f"حقیقی خالص: {toman_b(s['real_net_value'])} میلیارد تومان ({'ورود' if s['real_net_value'] >= 0 else 'خروج'})"
        if s["real_buy_cnt"] or s["real_sell_cnt"]:
            flow += f" | خریدار/فروشنده حقیقی: {n(s['real_buy_cnt'])} / {n(s['real_sell_cnt'])}"
        extra = []
        if s.get("buy_power") is not None:
            bp_txt = fa(f"{s['buy_power']:.2f}")
            extra.append(f"قدرت خرید حقیقی: {bp_txt}")
        if s.get("per_capita_buy") and s.get("per_capita_sell"):
            extra.append(f"سرانه خرید/فروش: {toman_m(s['per_capita_buy'])} / {toman_m(s['per_capita_sell'])} میلیون تومان")
        S.append((2, "<b>💵 جریان پول</b>\n" + flow + ("\n" + " | ".join(extra) if extra else "")))

    cap = (f"<b>📦 ظرفیت اجرا (بهترین قیمت ask)</b>\n{fa(o.contracts)} قرارداد = {n(o.contracts * 1000)} سهم ≈ "
           f"{toman_m(o.position_value)} میلیون تومان | محدودکننده: حجم عرضه‌ی {'پوت' if c['limit_by'] == 'put' else kind}"
           + ("" if p["ask_vol_known"] else " (حجم عرضه‌ی پوت نامعلومه؛ ظرفیت فقط از سمت " + kind + ")"))
    S.append((0, cap))
    S.append((1, "<b>⚠️ نکات</b>\n" + "\n".join("• " + t for t in _notes(o))))

    # اگه طولانی شد، کم‌اهمیت‌ترین بخش‌ها (عدد بالاتر) حذف می‌شن
    while len("\n\n".join(t for _, t in S)) > TG_LIMIT and any(pr > 0 for pr, _ in S):
        worst_pr = max(pr for pr, _ in S)
        idx = max(i for i, (pr, _) in enumerate(S) if pr == worst_pr)
        S.pop(idx)
    return "\n\n".join(t for _, t in S)


# ═════════════════════════════ Rich Message (Bot API ≥ 10.1) ═════════════════════════════
# فقط ظاهر پیام عوض شده؛ همه‌ی عددها از همان Opportunity / ctx می‌آیند و هیچ محاسبه‌ی جدیدی تو قالب نیست
# (به‌جز مبلغ کارمزدها، ردیف‌های «قیمت اعمال» و «سربه‌سر» جدول سود/زیان که با همان فرمول‌های _build_ctx حساب می‌شن).
# قواعد نمایش: عنوان‌ها فارسی و عبارت‌های لاتین (HV, WIV, IV Rank, NAV) فقط داخل پرانتز (وگرنه تو متن راست‌به‌چپ
# جابه‌جا می‌شن)؛ نمودار نداریم (جدول)؛ حجم معاملات نداریم (فقط ارزش معاملات؛ «حجم سرخط فروش» حجم صفه نه معاملات)؛
# «بدون معامله» یعنی ارزش معامله = ۰ (نه قیمت ۱ ریال)؛ درصدها کنار عددشون داخل پرانتز.
RICH_LIMIT = 30000       # سقف تلگرام ۳۲۷۶۸ کاراکتره؛ با حاشیه


def _e(x) -> str:
    return html.escape(str(x), quote=False)


def _lat(s: str) -> str:
    """عبارت لاتین داخل پرانتز + RLM تا جهت متن راست‌به‌چپ بهم نخوره."""
    return f"({s})\u200f"


def n1(x: float) -> str:     # مبلغ‌های کوچیک (کارمزد) با یک رقم اعشار
    if round(x, 1) == 0:
        x = 0
    return fa(f"{x:,.1f}")


def _fee(base: float, rate: float) -> str:
    """مبلغ کارمزد (ریال، هر سهم) و نرخش داخل پرانتز."""
    return f"{n1(base * rate)} ریال ({pct(rate * 100, 3)})"


def _table(rows, header=None) -> str:
    """rows: لیست tuple چندستونه؛ مقدارها HTML آماده‌اند (قبلاً escape شده). ردیف None رد می‌شه."""
    out = ["<table bordered striped>"]
    if header:
        out.append("<tr>" + "".join(f"<th>{h}</th>" for h in header) + "</tr>")
    for r in rows:
        if r is None:
            continue
        out.append("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    out.append("</table>")
    return "".join(out)


def _details(title: str, body: str) -> str:
    return f"<details><summary>{title}</summary>{body}</details>"


def _pnl_at(o: Opportunity, chg_pct: float) -> float:
    """سود/زیان خالص (٪ از سرمایه) در سررسید برای تغییر قیمت پایه؛ همان فرمول _build_ctx."""
    fe = o.ctx["fees"]
    st = o.stock_ask * (1 + chg_pct / 100)
    return (max(st * (1 - fe["sell"]), o.strike * (1 - fe["exercise"])) - o.cost_per_share) / o.cost_per_share * 100


def _price(x: float, traded: bool) -> str:
    """آخرین/پایانی: اگه ارزش معامله ۰ باشه معامله‌ای نبوده و قیمت منبع (مثلاً ۱ ریال) نشون داده نمی‌شه."""
    return n(x) if traded and x else "—"


def _turnover_txt(x: dict, unit_fn, unit: str) -> str:
    return f"{unit_fn(x['turnover'])} {unit}" if x["turnover"] > 0 else "بدون معامله (ارزش معامله: ۰)"


def _chg_txt(chg: float) -> str:
    return pct(chg, 0 if abs(chg - round(chg)) < 1e-9 else 1, True)


def _last_txt(x: dict, key: str = "last") -> str:
    """آخرین (یا پایانی) قیمت و درصد تغییرش نسبت به دیروز (داخل پرانتز)؛ بدون معامله = ارزش معامله ۰."""
    if x["turnover"] <= 0 or not x[key]:
        return "بدون معامله"
    if x["yesterday"] > 0:
        return f"{n(x[key])} ({pct((x[key] / x['yesterday'] - 1) * 100, 1, True)})"
    return n(x[key])


def _valuation(c: dict):
    rich = [c[k] for k in ("wiv_rich_pct", "hv_rich_pct") if c.get(k) is not None]
    if len(rich) != 2:
        return None
    return ("پوت از هر دو قیمت تئوریک ارزون‌تره ✅ (قیمت مناسب خرید)" if max(rich) < 0 else
            "پوت از هر دو قیمت تئوریک گرون‌تره ❌" if min(rich) > 0 else
            "قیمت پوت بین دو قیمت تئوریکه؛ منصفانه")


def _pnl_rows(o: Opportunity) -> list:
    """جدول سود/زیان در سررسید (جایگزین نمودار): سناریوها + نقطه‌های مهم (قیمت فعلی، قیمت اعمال، سربه‌سر)."""
    c = o.ctx
    pts = {round(float(chg), 6): ["سناریو", v] for chg, v in c["scenarios"]}

    def mark(chg: float, label: str) -> None:
        key = round(chg, 6)
        if key in pts:
            pts[key][0] = label if pts[key][0] == "سناریو" else f"{pts[key][0]} / {label}"
        else:
            pts[key] = [label, _pnl_at(o, chg)]

    mark(0.0, "قیمت خرید فعلی")
    mark((o.strike / o.stock_ask - 1) * 100, "قیمت اعمال")
    if c.get("breakeven_chg_pct") is not None:
        mark(c["breakeven_chg_pct"], "سربه‌سر")
    rows = []
    for chg in sorted(pts):
        label, v = pts[chg]
        price = o.stock_ask * (1 + chg / 100)
        rows.append((f"<b>{label}</b>" if label != "سناریو" else label,
                     f"{n(price)} ({_chg_txt(chg)})", pct(v, 1, True)))
    return rows


def format_opportunity_rich(o: Opportunity) -> str:
    """HTML برای sendRichMessage؛ اول کارت، بعد جدول خلاصه، بعد جدول‌ها؛ جزئیات ثانویه تو بلوک قابل‌بازشدن‌ان."""
    c, s, p, fe = o.ctx, o.ctx["stock"], o.ctx["put"], o.ctx["fees"]
    kind = s["kind"]
    ann = fa(f"{o.annual_pct:.0f}")
    if o.tier == "locked":
        head = "🟢 پروتکتیو پوت | سود تضمینی بعد از کارمزد"
        verdict = (f"حتی اگه {kind} صفر بشه، با اعمال پوت <b>{pct(o.margin_pct)}</b> سود می‌مونه (سالانه ≈ {ann}٪)")
    elif o.worst_per_share > 0:
        head = "🟡 پروتکتیو پوت | حدضرر ارزان"
        verdict = (f"بدترین حالت هم <b>{pct(o.margin_pct)}</b> سود می‌مونه (سالانه ≈ {ann}٪، کمتر از آستانه‌ی «ناب»)؛ "
                   f"یعنی بیمه‌ی بدون هزینه و سود {kind} بالا بازه")
    else:
        head = "🟡 پروتکتیو پوت | حدضرر ارزان"
        verdict = f"بیشترین ضرر ممکن تا سررسید: <b>{pct(-o.margin_pct)}</b> و سود {kind} بالا بازه"

    # ── کارت اول (رنگ سبز/زرد فقط با ایموجی دایره‌ی عنوان؛ تلگرام برای بلوک‌ها رنگ پس‌زمینه نداره)
    card = (f"<blockquote><b>{head}</b><br>"
            f"<b>{kind}:</b> {_tag(o.underlying)}<br>آخرین قیمت: {_last_txt(s)}<br>"
            f"<b>اختیار فروش:</b> {_tag(o.put_symbol)}<br>آخرین قیمت: {_last_txt(p)}</blockquote>")
    parts = [card, f"<p>{verdict}</p>"]

    # ── خلاصه و اطلاعات مهم
    summary = [
        (f"قیمت سرخط فروش {kind}", n(o.stock_ask)),
        ("قیمت سرخط فروش پوت", n(o.put_ask)),
        ("قیمت اعمال", f"{n(o.strike)} ({pct(o.moneyness_pct, 1, True)} نسبت به {kind})"),
        ("سررسید", f"{_e(fa(gregorian_to_jalali(o.expiry)))} ({fa(o.days)} روز)"),
        ("سرمایه‌ی لازم (هر سهم، با کارمزد)", f"<b>{n(o.cost_per_share)}</b> ریال"),
        ("بدترین حالت", f"<b>{n(o.worst_per_share)}</b> ریال ({pct(o.margin_pct, 2, True)})"),
        ("سالانه‌شده", f"{ann}٪"),
        (f"خالص بعد از هزینه‌ی فرصت (نرخ {rate_txt(c['rate'])}٪)", f"<b>{pct(o.net_after_carry_pct, 2, True)}</b>"),
        ("سربه‌سر", f"{n(c['breakeven'])} ({pct(c['breakeven_chg_pct'], 1, True)})") if c["breakeven"] else None,
        ("ظرفیت اجرا", f"{fa(o.contracts)} قرارداد ≈ {toman_m(o.position_value)} میلیون تومان"),
        ("ارزش معامله‌ی امروز پوت", _turnover_txt(p, toman_m, "میلیون تومان")),
        ("قیمت پوت نسبت به قیمت تئوریک", _valuation(c)) if _valuation(c) else None,
    ]
    parts += ["<h4>📋 خلاصه و اطلاعات مهم</h4>", _table(summary), "<p>همه‌ی محاسبات با احتساب کارمزدهاست.</p>"]

    # ── قرارداد
    under = "" if c["premium_vs_intrinsic"] >= 0 else " (زیر ارزش ذاتی ✅)"
    parts += ["<h4>📌 قرارداد</h4>", _table([
        ("قیمت اعمال", f"{n(o.strike)} ({pct(o.moneyness_pct, 1, True)} نسبت به {kind})"),
        ("ارزش ذاتی پوت", n(o.intrinsic)),
        ("ارزش زمانی پوت (قیمت منهای ارزش ذاتی)", n(c["premium_vs_intrinsic"]) + under),
    ])]

    # ── محاسبه (مبلغ کارمزدها جدا، بعد جمع‌بندی)
    calc = [
        (f"قیمت سرخط فروش {kind}", n(o.stock_ask)),
        (f"کارمزد خرید {kind}", _fee(o.stock_ask, fe["buy"])),
        ("قیمت سرخط فروش پوت", n(o.put_ask)),
        ("کارمزد خرید پوت", _fee(o.put_ask, fe["option"])),
        ("سرمایه‌ی لازم", f"<b>{n(o.cost_per_share)}</b>"),
        ("قیمت اعمال", n(o.strike)),
        ("کارمزد اعمال", _fee(o.strike, fe["exercise"])),
        ("دریافتی اعمال", n(o.cost_per_share + o.worst_per_share)),
        ("بدترین حالت", f"<b>{n(o.worst_per_share)}</b> ({pct(o.margin_pct, 2, True)})"),
        (f"هزینه‌ی فرصت پول (نرخ {rate_txt(c['rate'])}٪)", pct(o.carry_pct, 1)),
        ("خالص بعد از هزینه‌ی فرصت", f"<b>{pct(o.net_after_carry_pct, 2, True)}</b>"),
    ]
    if c["breakeven"]:
        calc.append(("سربه‌سر (فروش بازاری)", f"{n(c['breakeven'])} ({pct(c['breakeven_chg_pct'], 1, True)})"))
        calc.append(("کارمزد فروش بازاری در سربه‌سر", _fee(c["breakeven"], fe["sell"])))
    parts += ["<h4>💰 محاسبه (هر سهم، ریال)</h4>", _table(calc), "<p>همه‌ی محاسبات با احتساب کارمزدهاست.</p>"]

    # ── سود و زیان در سررسید (به‌جای نمودار)
    flat = max(v for _, v in c["scenarios"]) - min(v for _, v in c["scenarios"]) < 0.05
    parts += [
        "<h4>📊 سود و زیان در سررسید</h4>",
        _table(_pnl_rows(o), header=("موقعیت", f"قیمت {kind} در سررسید (تغییر نسبت به قیمت خرید)", "سود/زیان خالص")),
    ]
    if flat:
        parts.append(f"<p>زیر قیمت اعمال ({n(o.strike)}) نتیجه همیشه {pct(c['scenarios'][0][1], 2, True)} ثابته (اعمال پوت)"
                     + (f"؛ فقط اگه {kind} بالاتر از {n(c['breakeven'])} بره سود می‌کنی." if c["breakeven"] else ".") + "</p>")

    # ── ظرفیت
    lim = "پوت" if c["limit_by"] == "put" else kind
    cap = [("ظرفیت اجرا", f"{fa(o.contracts)} قرارداد ({n(o.contracts * 1000)} سهم) ≈ {toman_m(o.position_value)} میلیون تومان"),
           ("حجم سرخط فروش پوت", f"{n(p['ask_vol'])} قرارداد" if p["ask_vol_known"] else "تو این منبع نیست؛ قبل از خرید تو کارگزاری چک کن"),
           (f"حجم سرخط فروش {kind}", f"{n(s['ask_vol'])} سهم ({n(s['ask_vol'] // 1000)} قرارداد)"),
           ("محدودکننده", f"حجم سرخط فروش {lim}")]
    parts += ["<h4>📦 ظرفیت اجرا</h4>", _table(cap)]

    # ── جریان پول: فقط ورود پول حقیقی، سرانه‌ها و قدرت خرید
    if s.get("has_flow"):
        fl = [("ورود پول حقیقی (خالص)", f"{toman_b(s['real_net_value'])} میلیارد تومان ({'ورود' if s['real_net_value'] >= 0 else 'خروج'})"),
              ("سرانه‌ی خرید حقیقی", f"{toman_m(s['per_capita_buy'])} میلیون تومان") if s.get("per_capita_buy") is not None else None,
              ("سرانه‌ی فروش حقیقی", f"{toman_m(s['per_capita_sell'])} میلیون تومان") if s.get("per_capita_sell") is not None else None,
              ("قدرت خرید حقیقی", fa(f"{s['buy_power']:.2f}")) if s.get("buy_power") is not None else None]
        parts += ["<h4>💵 جریان پول حقیقی</h4>", _table(fl)]

    # ── جزئیات پوت (بسته)
    ptraded = p["turnover"] > 0
    put = [("قیمت سرخط خرید", n(p["bid"])),
           ("قیمت سرخط فروش", n(p["ask"])),
           ("فاصله‌ی سرخط خرید و فروش", pct(p["spread_pct"], 1)) if p["spread_pct"] is not None else None,
           ("آخرین قیمت", _last_txt(p)),
           ("قیمت پایانی", _last_txt(p, "final")),
           ("قیمت دیروز", n(p["yesterday"])),
           ("موقعیت باز", f"{n(p['open_interest'])} قرارداد") if p.get("open_interest") else None,
           ("ارزش معامله‌ی امروز", _turnover_txt(p, toman_m, "میلیون تومان") + (f"، {n(p['trades'])} نوبت" if p["trades"] else ""))]
    if c.get("iv"):
        put.append(("نوسان ضمنی پوت", pct(c["iv"] * 100, 0)))
    if c.get("wiv") or c.get("hv"):
        put.append((f"نوسان ضمنی وزنی پایه {_lat('WIV')}", pct(c["wiv"] * 100, 0)) if c.get("wiv") else None)
        put.append((f"نوسان تاریخی {_lat('HV')}", pct(c["hv"] * 100, 0)) if c.get("hv") else None)
        if c.get("iv_rank") is not None:
            put.append((f"رتبه‌ی نوسان ضمنی {_lat('IV Rank')}", fa(c["iv_rank"])))
        if c.get("iv_pct") is not None:
            put.append((f"صدک نوسان ضمنی {_lat('IV Percentile')}", fa(c["iv_pct"])))
        for key, lab in (("wiv", f"نوسان ضمنی وزنی {_lat('WIV')}"), ("hv", f"نوسان تاریخی {_lat('HV')}")):
            if c.get(key + "_theo") and c.get(key + "_rich_pct") is not None:
                r_ = c[key + "_rich_pct"]
                put.append((f"قیمت تئوریک پوت با {lab}",
                            f"{n(c[key + '_theo'])} (پوت {pct(abs(r_), 1)} " + ("گرون‌تر ❌" if r_ > 0 else "ارزون‌تر ✅") + ")"))
    if _valuation(c):
        put.append(("جمع‌بندی", _valuation(c)))
    h = c.get("heston")
    if h:
        put.append((f"قیمت تئوریک پوت با مدل هستون ({fa(h['n_points'])} قرارداد، خطای برازش {pct(h['rmse_rel'] * 100, 0)})",
                    f"{n(h['price'])} (پوت {pct(abs(h['rich_pct']), 1)} " + ("گرون‌تر ❌" if h["rich_pct"] > 0 else "ارزون‌تر ✅") + ")"))
    if c.get("iv"):
        put.append(("دلتا", fa(f"{c['delta']:.2f}")))
        put.append(("تتا روزانه", f"{n(c['theta_day'])} ریال"))
    parts.append(_details(f"🧮 جزئیات پوت {_e(o.put_symbol)}", _table(put)))

    # ── جزئیات پایه (بسته)
    straded = s["turnover"] > 0
    st = [("قیمت سرخط خرید", n(s["bid"])),
          ("قیمت سرخط فروش", n(s["ask"])),
          ("آخرین قیمت", _last_txt(s)),
          ("قیمت پایانی", _last_txt(s, "final")),
          ("کمترین قیمت امروز", n(s["low"]) if straded else "—"),
          ("بیشترین قیمت امروز", n(s["high"]) if straded else "—"),
          ("ارزش معاملات امروز", _turnover_txt(s, toman_b, "میلیارد تومان"))]
    if s.get("nav"):
        st.append((f"ارزش خالص دارایی {_lat('NAV')}{(' ' + fa(s['nav_date'][:10])) if s['nav_date'] else ''}", n(s["nav"])))
        st.append(("قیمت سرخط فروش نسبت به ارزش خالص",
                   f"{n(s['ask'])} ({pct((s['ask'] / s['nav'] - 1) * 100, 2, True)}) "
                   + ("✅ زیر ارزش خالص" if s["ask"] < s["nav"] else "بالای ارزش خالص")))
    parts.append(_details(f"📈 جزئیات {kind} {_e(o.underlying)}", _table(st)))

    # ── نکات
    parts += ["<h4>⚠️ نکات</h4>", "<ul>" + "".join(f"<li>{_e(t)}</li>" for t in _notes(o)) + "</ul>"]

    text = "".join(parts)
    if len(text) > RICH_LIMIT:   # عملاً پیش نمیاد؛ ولی اگه شد، فقط بخش‌های بسته رو برمی‌داریم
        text = re.sub(r"<details>.*?</details>", "", text, flags=re.S)
    return text


def send_rich(token: str, chat_id: str, html_text: str) -> bool:
    r = requests.post(f"https://api.telegram.org/bot{token}/sendRichMessage",
                      json={"chat_id": chat_id, "rich_message": {"html": html_text, "is_rtl": True}}, timeout=15)
    if not r.ok:
        print(f"⚠️ telegram sendRichMessage {r.status_code}: {r.text[:300]}")
    return r.ok


def send_opportunity(token: str, chat_id: str, o: Opportunity) -> bool:
    """اول Rich؛ اگه تلگرام ردش کرد (مثلاً این چت/سرور هنوز Rich نمی‌پذیره) قالب قدیمی، تا هشدار گم نشه."""
    if send_rich(token, chat_id, format_opportunity_rich(o)):
        return True
    return send(token, chat_id, format_opportunity_classic(o))


def send(token: str, chat_id: str, text: str) -> bool:
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=15)
    if not r.ok:
        print(f"⚠️ telegram {r.status_code}: {r.text[:200]}")
    return r.ok


def format_opportunity(o: Opportunity) -> str:
    """متنی که --dry-run چاپ می‌کنه = همان HTML که به sendRichMessage می‌ره."""
    return format_opportunity_rich(o)
