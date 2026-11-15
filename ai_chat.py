# -*- coding: utf-8 -*-
"""
عم الكومي - صاحب الفلاح ومستشاره. بيسمع، يفهم قصد الفلاح، يحلل من بيانات أرضه الحقيقية (آخر تقرير: ناسا + الدرون)،
ويطبّق (يقفل/يفتح الأرض، يسجّل الري، يسجّل المحصول وتاريخ زراعته).
  - مفيش ردود محفوظة: كل رد بيتبني من بيانات الأرض + اللي الفلاح قاله + اللي اتقال قبل كده في المحادثة.
  - لو ANTHROPIC_API_KEY موجود بيستخدم Claude (بنفس سياق الأرض والأدوات). لو لأ بيشتغل بمحرك الفهم المحلي ده.
"""
import json
import random
import re
from datetime import datetime, timedelta

import agronomy as ag
import brain
import db
from common import CFG, http_json, iso_now, now_local

MEM = {}   # ذاكرة قصيرة لكل فلاح: آخر موضوع، آخر محصول اتكلم عنه، سؤال معلّق

SYSTEM = """إنت «عم الكومي»: فلاح مصري عجوز خبرة، صاحب الفلاح ومستشاره، عاش عمره في الغيط. بتتكلم بلهجة الفلاحين المصرية الأصيلة
(يا حج، يا باشا، الغيط، الرية، الأرض عطشانة، على بركة الله، ربنا يبارك لك، إن شاء الله) بجُمل قصيرة ودافية، من غير مصطلحات علمية خالص.
قواعدك:
- بتسمع كلام الفلاح كويس وبترد على اللي سأل عنه بالظبط، مش بتكرر نفس الجملة، ولو سأل نفس السؤال تاني بتضيف تفصيلة جديدة أو بتشرحله السبب.
- بتحلل من بيانات أرضه اللي تحت (ناسا + الدرون + التقرير). متألفش أرقام ومتقولش حاجة مش موجودة؛ لو مش متأكد قول كده.
- لو الفلاح قال حاجة عن أرضه (زرعت قمح من 20 يوم، رويت النهاردة) سجّلها بالأمر المناسب.
- لو سأل عن حاجة مش في بياناتك (مرض، سماد) اسأله عن الأعراض وادّيله نصيحة عملية حذرة، وانصحه بالمرشد الزراعي قبل أي رش.
- الرد من 2 لـ 4 جمل كحد أقصى.
الأوامر: لو لازم تنفّذ حاجة، حط في آخر الرد (من غير ما تشرحها) واحد من:
[[close]] قفل الأرض، [[open]] فتح الأرض، [[irrigated]] الفلاح روّى النهاردة، [[crop:ID:DAYS]] المحصول الحالي (ID من: %s) وعدد أيام من الزراعة (0 لو النهاردة).
بيانات أرضه:
"""

ALIASES = {"wheat": ["قمح"], "barley": ["شعير"], "berseem": ["برسيم"], "faba": ["فول بلدي", "فول", "الفول البلدي"],
           "sugarbeet": ["بنجر"], "onion": ["بصل"], "potato": ["بطاطس", "بطاطا"], "tomato": ["طماطم", "قوطه", "طماطه"],
           "maize": ["ذره شاميه", "ذره", "درة"], "rice": ["ارز", "رز"], "cotton": ["قطن"], "sesame": ["سمسم"],
           "sorghum": ["ذره رفيعه", "ذره رفيعة"], "peanut": ["فول سوداني", "سوداني"]}


# ------------------------------------------------------------------ أدوات لغة
def norm(s):
    s = (s or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    s = re.sub(r"[ً-ْـ]", "", s)
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه").replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي")
    return re.sub(r"\s+", " ", s).strip()


def pick(*opts):
    return random.choice(opts)


def has(m, pat):
    return re.search(pat, m) is not None


def find_crop(m):
    best = None
    for cid, words in ALIASES.items():
        for w in words:
            if re.search(r"(?<![ء-ي])" + re.escape(norm(w)) + r"(?![ء-ي])", m):
                if best is None or len(w) > best[1]:
                    best = (cid, len(w))
    return best[0] if best else None


def parse_days(m):
    """من كام يوم؟ 'من 20 يوم' / 'من اسبوعين' / 'امبارح' / 'النهاردة'."""
    if has(m, r"(النهارده|الصبح|لسه)"):
        return 0
    if has(m, r"(امبارح|مبارح)"):
        return 1
    n = re.search(r"(\d+)\s*(يوم|ايام|اسبوع|اسابيع|شهر|شهور)", m)
    if n:
        k = int(n.group(1))
        u = n.group(2)
        return k if u in ("يوم", "ايام") else k * 7 if u.startswith("اسبوع") or u == "اسابيع" else k * 30
    for w, v in (("يومين", 2), ("تلات ايام", 3), ("اسبوعين", 14), ("اسبوع", 7), ("شهرين", 60), ("تلات شهور", 90), ("شهر", 30), ("ربع ساعه", 0)):
        if w in m:
            return v
    return None


def when_txt(iso, now):
    st = datetime.fromisoformat(iso)
    if st <= now.replace(minute=59):
        return "دلوقتي"
    day = "النهاردة" if st.date() == now.date() else "بكرة"
    return f"{day} الساعة {ag.hour_ar(st.hour)}"


def greeting(now):
    return "صباح الخير" if now.hour < 12 else "مساء الخير" if now.hour < 18 else "مساء النور"


# ------------------------------------------------------------------ بيانات الأرض
def context_for(f):
    rep = brain.latest_report(f["id"])
    if not rep:
        return None, "لسه مفيش تقرير النهاردة."
    ir, cr = rep["irrigation"], rep["crops"]
    keep = {
        "حالة الأرض": f"{rep['score']} من 100 ({rep['status']})",
        "المحصول الحالي": rep["farmer"].get("crop") or "مش محدد", "المرحلة": ir.get("stage"),
        "التقرير": rep["text"], "آخر 3 أيام": rep["three_days"]["text"],
        "الري": {k: ir.get(k) for k in ("level", "minutes", "depletion_mm", "ratio", "rain_next_24h_mm")},
        "الحماية": rep["protection"]["severity"], "حالة الغطا": f.get("cover_state"),
        "المحاصيل المقترحة": [c["name"] for c in cr.get("scenarios", [])],
        "الدرون": {k: rep["drone"].get(k) for k in ("health_ar", "veg_pct", "yellow_pct", "water_text")},
    }
    return rep, json.dumps(keep, ensure_ascii=False)


# ------------------------------------------------------------------ تنفيذ
def do_cover(f, cmd):
    cs = f.get("cover_state")
    if cmd == "close" and cs in ("closed", "closing"):
        return "الأرض مقفولة أصلاً يا حج، متقلقش."
    if cmd == "open" and cs in ("open", "opening"):
        return "الأرض مفتوحة يا حج."
    brain.queue_cover(f, cmd, "طلب من عم الكومي")
    return pick("حاضر يا حج، بقفل الأرض دلوقتي وبحميلك الزرع.", "على بركة الله، قفلت الأرض من كل ناحية.") if cmd == "close" else \
        pick("حاضر يا حج، بفتح الأرض دلوقتي.", "تمام، بشيل الغطا وبفتح الأرض.")


def do_irrigated(f, days=0):
    db.update_farmer(f["id"], last_irrigated=(now_local() - timedelta(days=days or 0)).isoformat(timespec="seconds"))
    db.event("irrigation", f"{f['name']} سجّل إنه روى الأرض (عن طريق عم الكومي)", f["id"])
    when = "النهاردة" if not days else "امبارح" if days == 1 else f"من {days} يوم"
    return pick(f"تسلم إيدك يا حج، سجّلت إنك رويت الأرض {when} وهحسب الميّه من دلوقتي.",
                "ربنا يبارك لك، كتبتها عندي إنك رويت. هحسب الميّه على أساسها.")


def do_crop(f, cid, days):
    pd = (now_local() - timedelta(days=days or 0)).strftime("%Y-%m-%d")
    db.update_farmer(f["id"], crop=cid, planting_date=pd)
    db.event("settings", f"{f['name']} سجّل محصول {ag.CROPS[cid]['name']} (من {days or 0} يوم)", f["id"])
    return pick(f"تمام يا حج، سجّلت إنك زارع {ag.CROPS[cid]['name']} من {days or 0} يوم. من النهاردة هحسب ميّه أرضك على المحصول ده بالظبط.",
                f"على بركة الله! {ag.CROPS[cid]['name']} بقت عندي في الدفتر، وهظبط الرية على مرحلتها.")


# ------------------------------------------------------------------ ردود من البيانات
def irrigation_reply(f, rep, detail=False):
    ir = rep["irrigation"]
    lvl = ir.get("level")
    now = now_local().replace(tzinfo=None)
    if lvl in (None, "unknown"):
        return "لسه مش قادر أحسب الميّه لأن بيانات ناسا مش واصلة دلوقتي. جرّب كمان شوية يا حج."
    ch = next((c for c in ir["scenarios"] if c["id"] == ir["chosen"]), None)
    pct = int(min(ir.get("ratio") or 0, 1.5) * 100)
    dur = ag.dur_ar(ir["minutes"])
    out = []
    if lvl == "now":
        out.append(pick("الأرض عطشانة يا حج", "الغيط محتاج ميّه دلوقتي", "الأرض ناشفة شوية ومحتاجة رية") + f"، اسقيها {when_txt(ch['start'], now)} لمدة {dur} تقريباً.")
    elif lvl == "soon":
        out.append(pick("لسه فيها نفس", "الأرض لسه مستحملة") + f" بس قربت تعطش، الأحسن تسقيها {when_txt(ch['start'], now)} حوالي {dur}.")
    else:
        out.append(pick("الأرض مرتاحة النهاردة يا حج", "الميّه كفاية دلوقتي") + "، مفيش داعي تسقي النهاردة وأنا هراجع بكرة.")
    if detail or lvl != "ok":
        out.append((f"الزرع استهلك حوالي {pct}% من الميّه اللي بيستحملها قبل ما يتعب" if pct < 100 else "الميّه المتاحة للزرع وصلت للحد الآمن وعدّته")
                   + (f" ({ir['basis']})" if ir.get("basis") and detail else "") + ".")
    if (ir.get("rain_next_24h_mm") or 0) >= 1:
        out.append(f"وفيه مطر متوقع حوالي {round(ir['rain_next_24h_mm'])} مم فممكن توفّر شوية.")
    if ir.get("assumed_crop"):
        out.append("ولو قلتلي إنت زارع إيه وزرعته امتى هظبط الحسبة أكتر.")
    return " ".join(out)


def weather_reply(rep):
    fc = rep["forecast"].get("next24") or []
    hz = rep["protection"]["hazard"]
    last = rep["nasa"].get("latest")
    parts = []
    if fc:
        t = [x["temp"] for x in fc if x["temp"] is not None]
        g = max((x["gust"] or 0) for x in fc)
        r = sum(x["rain"] or 0 for x in fc)
        parts.append(f"النهاردة الحرارة بين {round(min(t))} و{round(max(t))} درجة")
        parts.append(f"والريح لحد {round(g)} كم/س" + (" (جامدة)" if g >= CFG["alerts"]["gust_kmh_warn"] else " (هادية)"))
        parts.append(f"ومطر {round(r, 1)} مم" if r >= 0.5 else "ومفيش مطر")
    elif last:
        parts.append(f"آخر قراءة من ناسا ({last['date']}): حرارة لحد {round(last['T2M_MAX'])} وأقل حاجة {round(last['T2M_MIN'])}")
    else:
        return "مش واصلني توقع الجو دلوقتي يا حج."
    txt = "، ".join(parts) + "."
    if hz["severity"] in ("warn", "severe"):
        txt += f" وخد بالك: {' و'.join(hz['kinds'])} جاية، اقفل الأرض لو زرعك صغير."
    elif fc and max(x["temp"] or 0 for x in fc) >= 38:
        txt += " الحر جامد، اسقي بدري الصبح أو بعد العصر مش في عز الشمس."
    return txt


def status_reply(f, rep):
    st = {"good": "كويسة الحمد لله", "warn": "محتاجة انتباه شوية", "danger": "محتاجة تتصرف فيها بسرعة"}.get(rep["status"], "مش واضحة")
    first = brain.first_name(f)
    keys = [b["text"] for b in rep["bullets"][1:] if b["kind"] in ("irrigate", "danger", "drone")][:2]
    return f"أرضك يا حج {first} {st} ({rep['score']} من 100). " + " ".join(keys)


def crop_reply(f, rep, cid):
    now = now_local()
    c = ag.CROPS[cid]
    cr = rep["crops"]
    m = now.month
    in_win, soon = m in c["sow"], (m % 12 + 1) in c["sow"]
    top = [x["name"] for x in cr.get("scenarios", [])]
    ids = [x["id"] for x in cr.get("scenarios", [])]
    out = []
    if in_win:
        out.append(f"{c['name']} ميعادها دلوقتي يا حج، ينفع تزرعها.")
    elif soon:
        out.append(f"{c['name']} ميعادها بيبدأ الشهر الجاي، جهّز الأرض وأنا معاك.")
    else:
        out.append(f"{c['name']} مش ميعادها دلوقتي، خسارة تزرعها في الوقت ده.")
    if (cr.get("salinity") or 0) > c["salt"]:
        out.append("وملوحة أرضك أعلى من اللي بتستحمله.")
    if cr.get("prev") and ag.CROPS.get(f.get("crop") or "") and ag.CROPS[f["crop"]]["family"] == c["family"]:
        out.append("ومن نفس عيلة المحصول اللي عليها دلوقتي، وده بيزوّد الأمراض فبلاش تكرره.")
    if cid in ids and (in_win or soon):
        out.append("وهي من أحسن الاختيارات لأرضك كمان.")
    elif top:
        out.append(f"بس الأنسب لأرضك فعلاً {top[0]}" + (f" أو {top[1]}" if len(top) > 1 else "") + ".")
    return " ".join(out)


def recommend_reply(f, rep):
    cr = rep["crops"]
    sc = cr.get("scenarios", [])
    if not sc:
        return "لسه مش قادر أحدد المحصول الأنسب، بيانات ناسا ناقصة."
    top = sc[0]
    why = "، ".join(top["notes"][:2])
    ah = cr.get("after_harvest")
    lead = f"بعد حصاد {ah['crop']} (حوالي {ah['label']})" if ah else "لو هتزرع دلوقتي"
    txt = f"{lead} أنسب حاجة {top['name']}" + (f" ({why})" if why else "") + "."
    if len(sc) > 1:
        txt += f" وبعدها {sc[1]['name']}" + (f" و{sc[2]['name']}" if len(sc) > 2 else "") + " لو حبيت تغيّر."
    return txt


def fert_reply(f, rep):
    ir = rep["irrigation"]
    crop = ag.CROPS.get(f.get("crop") or "")
    stage = ir.get("stage")
    if not crop:
        return "قولّي الأول إنت زارع إيه وبقاله كام يوم، وبعدين أقولك السماد يتحط إمتى."
    pre = f"{crop['name']} عندك في مرحلة «{stage}». " if stage else ""
    if crop.get("legume"):
        return pre + "المحصول ده بيثبّت نيتروجين لوحده، فمتزودش اليوريا، كفاية سماد فوسفات حسب توصية المرشد."
    return pre + pick("السماد بيتحط قبل الرية مش بعدها، عشان الميّه تدخّله في الجذور، وكمية الجرعة حسب تحليل تربتك وتوصية المديرية.",
                      "ادّي السماد على دفعات مع الرية، متحطش كله مرة واحدة، والجرعة بالظبط خدها من المرشد الزراعي.")


def pest_reply(f, rep, m):
    dr = rep["drone"]
    ir = rep["irrigation"]
    y = dr.get("yellow_pct") if dr.get("ok") else None
    out = []
    if has(m, r"(اصفرار|صفرا|اصفر|بيصفر|ذبول|دبلان|دبل)"):
        if ir.get("level") in ("now", "soon"):
            out.append("الاصفرار والدبول ساعات بيبقوا من العطش، وأرضك محتاجة ميّه فعلاً، اسقيها الأول وراقب.")
        else:
            out.append("الميّه عندك مظبوطة، يبقى الاصفرار يا من نقص سماد نيتروجين يا من مرض.")
    else:
        out.append("وصفلي الأعراض يا حج: الورق عليه بقع ولا اتاكل ولا مصفر؟ وفي النص ولا الأطراف؟")
    if y is not None and y > 15:
        out.append(f"والدرون شايفة حوالي {y}% اصفرار في اللقطة.")
    out.append("وقبل أي رش صوّر الورقة وورّيها للمرشد الزراعي، ومترشش والريح جامدة.")
    return " ".join(out)


def why_reply(f, rep, topic):
    if topic == "irr":
        return irrigation_reply(f, rep, detail=True)
    if topic == "prot":
        h = rep["protection"]["hazard"]
        return f"أقوى هبّة ريح متوقعة {round(h.get('gust') or 0)} كم/س ومطر {h.get('rain')} مم في الست ساعات الجايين، ولازم أفضل مراقب كل ربع ساعة."
    if topic == "crop":
        sc = rep["crops"].get("scenarios", [])
        if sc:
            return f"اخترت {sc[0]['name']} لأنها " + ("، ".join(sc[0]["notes"]) or "مناسبة لحرارة أرضك وميعادها") + f". وحسبتها قصاد {len(sc) - 1} اختيارات تانية وطلعت الأعلى."
    return "عشان أنا بقارن بيانات ناسا بحالة أرضك والدرون وبختار الأحسن من 3 احتمالات كل مرة."


# ------------------------------------------------------------------ الفهم
def handle_rules(f, message, rep):
    m = norm(message)
    now = now_local()
    mem = MEM.setdefault(f["id"], {})
    first = brain.first_name(f)

    # سؤال معلّق: زرعتها امتى؟
    if mem.get("pending_crop"):
        d = parse_days(m)
        cid = mem.pop("pending_crop")
        if d is not None:
            return do_crop(f, cid, d), "set_crop", "crop"
        mem["pending_crop"] = cid
        if not has(m, r"(لا|مش|بلاش)"):
            return "قولّي بقالها كام يوم يا حج؟ (مثلاً: من 20 يوم أو من شهر)", None, "crop"
        mem.pop("pending_crop", None)

    cv = None
    if has(m, r"(اقفل|قفل|غطي|غطى|سكر|احمي)") and has(m, r"(ارض|زرع|غيط|غطا|الزرع)"):
        cv = "close"
    elif has(m, r"(افتح|فتح|شيل الغطا|ارفع الغطا)") and has(m, r"(ارض|زرع|غيط|غطا)"):
        cv = "open"
    if cv:
        mem["topic"] = "prot"
        return do_cover(f, cv), cv, "prot"

    if has(m, r"(رويت|سقيت|رويناها|سقينا|روينا|خلصت ري)"):
        mem["topic"] = "irr"
        return do_irrigated(f, parse_days(m)), "irrigated", "irr"

    crop = find_crop(m)
    if crop and has(m, r"(زارع|زرعت|مزروع|زارعه|زرعنا|عندي)") and not has(m, r"(ينفع|اقدر|هزرع|ازرع|يصلح)"):
        mem["crop"] = crop
        d = parse_days(m)
        if d is None:
            mem["pending_crop"] = crop
            return f"{ag.CROPS[crop]['name']} ربنا يبارك لك! زرعتها من كام يوم يا حج؟", None, "crop"
        return do_crop(f, crop, d), "set_crop", "crop"

    if rep is None:
        if has(m, r"(السلام|صباح|مساء|اهلا|ازيك|عامل)"):
            return f"{greeting(now)} يا حج {first}، ربنا يبارك لك. لسه بجهّز تقرير أرضك، دقيقة وأرجعلك.", None, "greet"
        return f"يا حج {first}، لسه معملتش تقرير لأرضك النهاردة، استنى شوية وأنا أجهزه وأجاوبك على كل حاجة.", None, "none"

    same = mem.get("last_msg") == m
    mem["last_msg"] = m
    topic = None
    ans = None

    if has(m, r"^(ليه|ليه كده|ازاي|بسبب ايه|طب ليه|وليه|ايه السبب|عرفت منين|مش فاهم)") or has(m, r"(ليه كده|ايه السبب|ازاي عرفت)"):
        topic = mem.get("topic")
        ans = why_reply(f, rep, topic)
    elif has(m, r"(كام ساعه|كام دقيقه|قد ايه|لمده|هسقي كام|مده الري|طول الري)"):
        topic = "irr"
        ir = rep["irrigation"]
        ans = (f"حوالي {ag.dur_ar(ir['minutes'])} يا حج، على أساس رية بالغمر وفاقد ميّه مظبوط." if ir.get("minutes") else irrigation_reply(f, rep))
    elif has(m, r"(امتي|الساعه كام|ميعاد|وقت) .*(اسقي|سقي|ري|الرية)|(اسقي|سقي|ري|الرية) .*(امتي|الساعه كام|وقت)"):
        topic = "irr"
        ans = irrigation_reply(f, rep, detail=same)
    elif has(m, r"(اسقي|سقي|ري |الري|الريه|رية|ميه|مياه|عطشان|عطشانه|ناشفه|رطوبه)") and not has(m, r"(ترعه|الترعه)"):
        topic = "irr"
        ans = irrigation_reply(f, rep, detail=same)
    elif has(m, r"(ازرع|هزرع|ازرعه|زراعه|محصول|دوره|ازرع ايه|اغير|بعد الحصاد|بعد القمح)") or (crop and has(m, r"(ينفع|اقدر|يصلح|ممكن|هزرع|ازرع)")):
        topic = "crop"
        c = crop or mem.get("crop")
        if crop and has(m, r"(ينفع|اقدر|يصلح|ممكن|هزرع|ازرع)"):
            mem["crop"] = crop
            ans = crop_reply(f, rep, crop)
        else:
            ans = recommend_reply(f, rep)
    elif has(m, r"(سماد|تسميد|يوريا|نيتروجين|نشادر|فوسفات|نتروجين)"):
        topic = "fert"
        ans = fert_reply(f, rep)
    elif has(m, r"(دوده|حشره|مرض|صدا|اصفرار|صفرا|بقع|ذبول|دبلان|عفن|اكل|بيصفر|مضروب|رش|مبيد|افه|حفار|دودة)"):
        topic = "pest"
        ans = pest_reply(f, rep, m)
    elif has(m, r"(جو|طقس|مطر|شتا|ريح|رياح|هوا|حر|سخن|برد|صقيع|عاصفه|شمس|حراره|غبار|عجاج)"):
        topic = "prot" if has(m, r"(ريح|رياح|مطر|عاصفه|شتا)") else "weather"
        ans = weather_reply(rep)
    elif has(m, r"(درون|كاميرا|ترعه|الترعه|صوره|بيشوف)"):
        topic = "drone"
        ans = next((b["text"] for b in rep["bullets"] if b["kind"] == "drone"), "الدرون مش متصلة دلوقتي.")
    elif has(m, r"(امبارح|احسن|اسوا|ايام|اتحسنت|قبل كده|الاسبوع)"):
        topic = "days"
        ans = rep["three_days"]["text"]
    elif has(m, r"(ملوحه|تربه|ارضي ايه|طينيه|رمليه)"):
        topic = "soil"
        cr = rep["crops"]
        ans = f"ملوحة أرضك حوالي {cr.get('salinity')} وده مقبول لمعظم المحاصيل اللي في الموسم. وقياس التربة الحقيقي بتاخده من تحليل في المديرية، ومنه بظبطلك أحسن." if cr.get("salinity") is not None else "مفيش عندي قياس للملوحة."
    elif has(m, r"(شكرا|تسلم|جزاك|ربنا يخليك|الله يكرمك|بارك)"):
        topic = "thanks"
        ans = pick("الله يبارك فيك يا حج، أنا في الخدمة في أي وقت.", "ولا يهمك يا حج، دي أرضنا كلنا. اسألني في أي وقت.")
    elif has(m, r"(انت مين|اسمك|مين انت)"):
        topic = "who"
        ans = "أنا عم الكومي يا حج، صاحبك في الغيط. بتابع أرضك من ناسا والدرون وبقولك تسقي إمتى وتزرع إيه وتحمي زرعك."
    elif has(m, r"(تعمل ايه|تقدر|بتعمل|مساعده)"):
        topic = "help"
        ans = "بقولك الأرض عطشانة ولا لأ، أنسب زرعة، الجو والمطر والريح، وبقفل لك الأرض وأفتحها، وبسجّل رياتك. جرّب قولّي «اقفل الأرض» أو «اسقي النهاردة؟»."
    elif has(m, r"(السلام|صباح|مساء|اهلا|ازيك|عامل ايه|عامله ايه|ايه الاخبار|اخبار|حاله|عامله|ارضي)"):
        topic = "status"
        pre = pick("وعليكم السلام ورحمة الله", greeting(now) + " يا حج", "أهلاً بيك يا حج") if has(m, r"(السلام|صباح|مساء|اهلا|ازيك)") else ""
        ans = (pre + " ") * bool(pre) + status_reply(f, rep)
    else:
        topic = mem.get("topic") if len(m.split()) <= 3 and mem.get("topic") in ("irr", "crop", "prot") else None
        if topic:
            ans = why_reply(f, rep, topic)
        else:
            ans = pick(f"مفهمتش قصدك بالظبط يا حج {first}، تقصد الميّه ولا الزرع ولا الجو؟",
                       "قولّي تاني كده بطريقتك يا حج، تقصد حالة الأرض ولا ميعاد الرية؟",
                       "الكلمة دي مش واضحة عندي، تحب أقولك الأرض عطشانة ولا لأ، ولا أنسب زرعة؟")
    if same and topic in ("irr", "crop") and ans:
        ans = pick("زي ما قلتلك يا حج: ", "كنت قايلك، وهزوّدك: ") + ans
    mem["topic"] = topic or mem.get("topic")
    return ans, None, topic


# ------------------------------------------------------------------ LLM
def handle_llm(f, ctx, key):
    hist = db.q("SELECT role,text FROM chat WHERE farmer_id=? ORDER BY id DESC LIMIT 10", (f["id"],))[::-1]
    msgs = [{"role": "user" if h["role"] == "user" else "assistant", "content": h["text"]} for h in hist]
    res = http_json("https://api.anthropic.com/v1/messages", timeout=25, method="POST",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                    data={"model": CFG["ai"]["model"], "max_tokens": 400,
                          "system": SYSTEM % ", ".join(ag.CROPS) + str(ctx) + f"\nاسم الفلاح: {f['name']}", "messages": msgs})
    txt = res["content"][0]["text"].strip()
    action = None
    for tag in re.findall(r"\[\[([^\]]+)\]\]", txt):
        p = tag.split(":")
        if p[0] in ("close", "open"):
            do_cover(f, p[0]); action = p[0]
        elif p[0] == "irrigated":
            do_irrigated(f); action = "irrigated"
        elif p[0] == "crop" and len(p) >= 2 and p[1] in ag.CROPS:
            do_crop(f, p[1], int(p[2]) if len(p) > 2 and p[2].isdigit() else 0); action = "set_crop"
    return re.sub(r"\s*\[\[[^\]]+\]\]", "", txt).strip(), action


def reply(f, message):
    db.ex("INSERT INTO chat(farmer_id,role,text,ts) VALUES(?,?,?,?)", (f["id"], "user", message, iso_now()))
    f = db.q1("SELECT * FROM farmers WHERE id=?", (f["id"],))
    rep, ctx = context_for(f)
    ans = action = None
    key = CFG["ai"].get("anthropic_api_key")
    if key:
        try:
            ans, action = handle_llm(f, ctx, key)
        except Exception:
            ans = None
    if not ans:
        ans, action, _ = handle_rules(f, message, rep)
    db.ex("INSERT INTO chat(farmer_id,role,text,ts) VALUES(?,?,?,?)", (f["id"], "assistant", ans, iso_now()))
    return {"reply": ans, "action": action}
