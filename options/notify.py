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
    return fa(f"{x:+.{d}f}" if sign else f"{x:.{d}f}") + "٪"


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
    if p["volume"] == 0:
        out.append("پوت امروز معامله نشده؛ ممکنه bid/ask قدیمی باشه.")
    if p["ask_vol_known"] and p["ask_vol"] < 5:
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
# (به‌جز مختصات نمودار payoff که با همان فرمول _build_ctx حساب می‌شه و تست شده).
_LRM = "\u200e"          # اجبار جهت چپ‌به‌راست؛ بدون اون علامت منفی در متن راست‌به‌چپ بعد از عدد می‌افته
RICH_LIMIT = 30000       # سقف تلگرام ۳۲۷۶۸ کاراکتره؛ با حاشیه


def _e(x) -> str:
    return html.escape(str(x), quote=False)


def _table(rows, header=None) -> str:
    """rows: لیست (برچسب، مقدار) یا لیست tuple چندستونه؛ مقدارها HTML آماده‌اند (قبلاً escape شده)."""
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


def payoff_chart(o: Opportunity, width: int = 26, height: int = 9) -> str:
    """نمودار متنی payoff (داخل <pre>، فقط حروف لاتین/عدد تا جهت متن بهم نریزه)."""
    k_chg = (o.strike / o.stock_ask - 1) * 100
    be_chg = o.ctx.get("breakeven_chg_pct")
    lo, hi = -30.0, 30.0
    for v in (k_chg, be_chg):
        if v is not None:
            hi, lo = max(hi, v + 10), min(lo, v - 10)
    lo, hi = max(lo, -90.0), min(hi, 100.0)

    def col(chg: float) -> int:
        return min(width - 1, max(0, round((chg - lo) / (hi - lo) * (width - 1))))

    xs = [lo + (hi - lo) * i / (width - 1) for i in range(width)]
    vals = [_pnl_at(o, x) for x in xs]
    ymax, ymin = max(max(vals), 0.0), min(min(vals), 0.0)
    if ymax - ymin < 1e-9:
        ymax, ymin = ymax + 1, ymin - 1
    pad = (ymax - ymin) * 0.06
    ymax, ymin = ymax + pad, ymin - pad

    def row(v: float) -> int:
        return round((ymax - v) / (ymax - ymin) * (height - 1))

    zr = row(0.0)
    grid = [[" "] * width for _ in range(height)]
    for i in range(width):
        grid[zr][i] = "─"
    for i, v in enumerate(vals):
        r = row(v)
        for rr in range(min(r, zr), max(r, zr) + 1):
            if rr != zr:            # خط صفر دیده بشه
                grid[rr][i] = "░"
        grid[r][i] = "█"

    # برچسب محور عمودی = مقدار واقعی بالاترین/پایین‌ترین نقطه (نه مقدار با padding)
    labels = {row(min(vals)): f"{min(vals):+.1f}%", row(max(vals)): f"{max(vals):+.1f}%"}
    labels[zr] = "0%"
    lines = [f"{labels.get(r, ''):>7} │" + "".join(grid[r]) for r in range(height)]

    step = 15 if hi - lo <= 80 else 30
    ticks = [t for t in range(-90, 101, step) if lo <= t <= hi]
    axis, tl = ["─"] * width, [" "] * (width + 4)
    for t in ticks:
        c = col(t)
        axis[c] = "┬"
        txt = f"{t:+d}" if t else "0"
        s = max(0, min(width + 4 - len(txt), c - len(txt) // 2))
        if all(ch == " " for ch in tl[max(0, s - 1):s + len(txt) + 1]):
            tl[s:s + len(txt)] = list(txt)
    mk, ml = [" "] * (width + 4), [" "] * (width + 4)
    for sym, chg in (("S", 0.0), ("K", k_chg), ("B", be_chg)):
        if chg is None or not (lo <= chg <= hi):
            continue
        c = col(chg)
        while c < width + 3 and mk[c] != " ":
            c += 1
        mk[c], ml[c] = "▲", sym
    pre = " " * 7 + " └" + "".join(axis)
    return "\n".join(lines + [pre, " " * 9 + "".join(tl).rstrip(), " " * 9 + "".join(mk).rstrip(),
                              " " * 9 + "".join(ml).rstrip()])


def format_opportunity_rich(o: Opportunity) -> str:
    """HTML برای sendRichMessage؛ جدول‌ها و نمودار همیشه دیده می‌شن، جزئیات ثانویه تو بلوک قابل‌بازشدن‌ان."""
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

    parts = [f"<h3>{head}</h3>",
             f"<p>{_tag(o.underlying)} | پوت {_tag(o.put_symbol)}</p>",
             f"<blockquote>{verdict}</blockquote>"]

    # ── قرارداد
    under = "" if c["premium_vs_intrinsic"] >= 0 else " (زیر ارزش ذاتی ✅)"
    parts += ["<h4>📌 قرارداد</h4>", _table([
        ("strike", n(o.strike)),
        ("سررسید", f"{_e(fa(gregorian_to_jalali(o.expiry)))} ({fa(o.days)} روز)"),
        (f"strike نسبت به {kind}", pct(o.moneyness_pct, 1, True)),
        ("ارزش ذاتی پوت", n(o.intrinsic)),
        ("قیمت پوت − ارزش ذاتی", n(c["premium_vs_intrinsic"]) + under),
    ])]

    # ── محاسبه
    calc = [
        (f"خرید {kind} (ask)", f"{n(o.stock_ask)} + کارمزد {pct(fe['buy'] * 100, 3)}"),
        ("خرید پوت (ask)", f"{n(o.put_ask)} + کارمزد {pct(fe['option'] * 100, 3)}"),
        ("سرمایه‌ی لازم", f"<b>{n(o.cost_per_share)}</b>"),
        (f"دریافتی اعمال (کارمزد {pct(fe['exercise'] * 100, 3)})", n(o.cost_per_share + o.worst_per_share)),
        ("بدترین حالت", f"<b>{n(o.worst_per_share)}</b> ({pct(o.margin_pct, 2, True)})"),
        ("سالانه‌شده", f"{ann}٪"),
        (f"هزینه‌ی فرصت پول (نرخ {rate_txt(c['rate'])}٪)", pct(o.carry_pct, 1)),
        ("خالص بعد از هزینه‌ی فرصت", f"<b>{pct(o.net_after_carry_pct, 2, True)}</b>"),
    ]
    if c["breakeven"]:
        calc.append((f"سربه‌سر ({'فروش بازاری، کارمزد ' + pct(fe['sell'] * 100, 3)})",
                     f"{n(c['breakeven'])} ({pct(c['breakeven_chg_pct'], 1, True)} از الان)"))
    parts += ["<h4>💰 محاسبه (هر سهم، ریال)</h4>", _table(calc)]

    # ── نمودار + سناریو
    flat = max(v for _, v in c["scenarios"]) - min(v for _, v in c["scenarios"]) < 0.05
    scen_rows = [(f"{fa(f'{chg:+d}') if chg else fa(0)}٪", n(o.stock_ask * (1 + chg / 100)), pct(v, 1, True)) for chg, v in c["scenarios"]]
    parts += [
        "<h4>📊 نمودار سود و زیان در سررسید</h4>",
        f"<p>محور افقی: تغییر قیمت {kind} تا سررسید (٪) | محور عمودی: سود/زیان خالص (٪ از سرمایه)</p>",
        f"<pre>{_e(payoff_chart(o))}</pre>",
        "<p>S = قیمت فعلی | K = strike" + (" | B = سربه‌سر" if c["breakeven"] else "") + "</p>",
        _table(scen_rows, header=(f"تغییر قیمت {kind}", "قیمت در سررسید", "سود/زیان خالص")),
    ]
    if flat:
        parts.append(f"<p>زیر strike ({n(o.strike)}) نتیجه همیشه {pct(c['scenarios'][0][1], 2, True)} ثابته (اعمال پوت)"
                     + (f"؛ فقط اگه {kind} بالاتر از {n(c['breakeven'])} بره سود می‌کنی." if c["breakeven"] else ".") + "</p>")

    # ── ظرفیت
    lim = "پوت" if c["limit_by"] == "put" else kind
    cap = [(f"{fa(o.contracts)} قرارداد", f"{n(o.contracts * 1000)} سهم ≈ {toman_m(o.position_value)} میلیون تومان"),
           ("محدودکننده", f"حجم عرضه‌ی {lim}" + ("" if p["ask_vol_known"] else " (حجم عرضه‌ی پوت نامعلومه)"))]
    parts += ["<h4>📦 ظرفیت اجرا (بهترین قیمت ask)</h4>", _table(cap)]

    # ── جزئیات پوت (بسته)
    put = [("bid / ask", f"{n(p['bid'])} / {n(p['ask'])}" + (f" (اسپرد {pct(p['spread_pct'], 1)})" if p["spread_pct"] is not None else "")),
           ("حجم عرضه / تقاضا", f"{n(p['ask_vol'])} / {n(p['bid_vol'])} قرارداد" if p["ask_vol_known"]
            else "تو این منبع نیست؛ قبل از خرید صف رو تو کارگزاری چک کن"),
           ("آخرین / پایانی / دیروز", f"{n(p['last'])} / {n(p['final'])} / {n(p['yesterday'])}"),
           (("موقعیت باز", f"{n(p['open_interest'])} قرارداد") if p.get("open_interest") else None),
           ("معامله‌ی امروز", f"{n(p['volume'])} قرارداد، {toman_m(p['turnover'])} میلیون تومان" + (f"، {n(p['trades'])} نوبت" if p["trades"] else ""))]
    if c.get("wiv") or c.get("hv"):
        vol = [f"ضمنی پوت {pct(c['iv'] * 100, 0)}" if c.get("iv") else None,
               f"wiv پایه {pct(c['wiv'] * 100, 0)}" if c.get("wiv") else None,
               f"HV {pct(c['hv'] * 100, 0)}" if c.get("hv") else None]
        put.append(("نوسان", " | ".join(x for x in vol if x)))
        if c.get("iv_rank") is not None or c.get("iv_pct") is not None:
            put.append(("IV rank / percentile", f"{fa(c.get('iv_rank'))} / {fa(c.get('iv_pct'))}"))
        for key, lab in (("wiv", "wiv"), ("hv", "HV")):
            if c.get(key + "_theo") and c.get(key + "_rich_pct") is not None:
                r_ = c[key + "_rich_pct"]
                put.append((f"تئوریک با {lab}", f"{n(c[key + '_theo'])} → پوت {pct(abs(r_), 1)} " + ("گرون‌تر ❌" if r_ > 0 else "ارزون‌تر ✅")))
    rich = [c[k] for k in ("wiv_rich_pct", "hv_rich_pct") if c.get(k) is not None]
    if len(rich) == 2:
        put.append(("جمع‌بندی", "پوت از هر دو مرجع ارزون‌تره (قیمت مناسب خرید)" if max(rich) < 0 else
                    "پوت از هر دو مرجع گرون‌تره" if min(rich) > 0 else "قیمت پوت بین wiv و HVه (منصفانه)"))
    h = c.get("heston")
    if h:
        put.append((f"هستون ({fa(h['n_points'])} قرارداد، خطای برازش {pct(h['rmse_rel'] * 100, 0)})",
                    f"تئوریک {n(h['price'])} → پوت {pct(abs(h['rich_pct']), 1)} " + ("گرون‌تر ❌" if h["rich_pct"] > 0 else "ارزون‌تر ✅")))
    if c.get("iv"):
        delta_txt = fa(f"{c['delta']:.2f}")
        gk = f"دلتا {delta_txt} | تتا روزانه {n(c['theta_day'])} ریال"
        put.append(("گریک‌ها" if (c.get("wiv") or c.get("hv")) else "نوسان ضمنی / گریک‌ها",
                    gk if (c.get("wiv") or c.get("hv")) else f"{pct(c['iv'] * 100, 0)} | {gk}"))
    parts.append(_details(f"🧮 پوت {_e(o.put_symbol)}", _table(put)))

    # ── جزئیات پایه (بسته)
    chg = (s["final"] / s["yesterday"] - 1) * 100 if s["yesterday"] else 0
    vr = s["volume"] / s["avg_month_vol"] if s["avg_month_vol"] else 0
    st = [("bid / ask", f"{n(s['bid'])} / {n(s['ask'])}"), ("حجم عرضه (ask)", n(s["ask_vol"])),
          ("پایانی / آخرین", f"{n(s['final'])} ({pct(chg, 1, True)}) / {n(s['last'])}"),
          ("محدوده امروز", f"{n(s['low'])} تا {n(s['high'])}"),
          ("حجم", n(s["volume"]) + (f" ({fa(f'{vr:.1f}')}× میانگین ماهانه)" if s["avg_month_vol"] else "")),
          ("ارزش معاملات", f"{toman_b(s['turnover'])} میلیارد تومان")]
    if s.get("nav"):
        st.append((f"NAV{(' (' + fa(s['nav_date'][:10]) + ')') if s['nav_date'] else ''}",
                   f"{n(s['nav'])} | قیمت خرید نسبت به NAV {pct((s['ask'] / s['nav'] - 1) * 100, 2, True)}"
                   + (" (زیر NAV ✅)" if s["ask"] < s["nav"] else " (بالای NAV)")))
    parts.append(_details(f"📈 {kind} {_e(o.underlying)}", _table(st)))

    # ── جریان پول (بسته)
    if s.get("has_flow"):
        fl = [("حقیقی خالص", f"{toman_b(s['real_net_value'])} میلیارد تومان ({'ورود' if s['real_net_value'] >= 0 else 'خروج'})")]
        if s["real_buy_cnt"] or s["real_sell_cnt"]:
            fl.append(("خریدار / فروشنده حقیقی", f"{n(s['real_buy_cnt'])} / {n(s['real_sell_cnt'])}"))
        if s.get("buy_power") is not None:
            fl.append(("قدرت خرید حقیقی", fa(f"{s['buy_power']:.2f}")))
        if s.get("per_capita_buy") and s.get("per_capita_sell"):
            fl.append(("سرانه خرید / فروش", f"{toman_m(s['per_capita_buy'])} / {toman_m(s['per_capita_sell'])} میلیون تومان"))
        parts.append(_details("💵 جریان پول", _table(fl)))

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
