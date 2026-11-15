# -*- coding: utf-8 -*-
"""
عقل النظام:  يشوف (ناسا + الدرون + التوقعات)  ->  يفهم (ET0، رطوبة، NDVI، رؤية)  ->  يحلل (3 سيناريوهات لكل قرار)
->  يقرر (الأفضل بنقاط)  ->  ينفّذ (تقرير، تنبيه واتساب، أمر قفل الأرض للـ ESP32 في الخطر الشديد).
التقرير لازم يخلص في 40 ثانية من فتح التطبيق (report_deadline_seconds).
"""
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import agronomy as ag
import camera
import db
import nasa
import vision
import whatsapp
from common import CFG, iso_now, now_local

JOBS = {}
_alert_state = {}


# ------------------------------------------------------------------ helpers
def first_name(f):
    return (f.get("name") or "").split()[0] if f.get("name") else ""


def loc(f):
    lat = f.get("lat") if f.get("lat") is not None else CFG["defaults"]["lat"]
    lng = f.get("lng") if f.get("lng") is not None else CFG["defaults"]["lng"]
    return lat, lng


def queue_cover(f, cmd, reason):
    """أمر فتح/قفل للأرض. الـ ESP32 بيسحبه من /api/device/command وينفّذه."""
    dev = f.get("device_id") or CFG["default_device_id"]
    db.ex("INSERT INTO commands(farmer_id,device_id,cmd,status,reason,created_at) VALUES(?,?,?,?,?,?)",
          (f["id"], dev, cmd, "pending", reason, iso_now()))
    db.update_farmer(f["id"], cover_state="closing" if cmd == "close" else "opening")
    db.event("cover", f"أمر {'قفل' if cmd == 'close' else 'فتح'} الأرض لـ {f['name']} ({reason})", f["id"])


def capture_vision(f):
    hub = camera.hub()
    frame, ts = hub.latest()
    if frame is None or not hub.online():
        return {"ok": False, "error": "الدرون/الكاميرا مش متصلة دلوقتي"}
    res = vision.analyze_jpeg(frame)
    if res.get("ok"):
        hist = [json.loads(r["json"]).get("water_pct") for r in
                db.q("SELECT json FROM vision WHERE farmer_id=? ORDER BY id DESC LIMIT 10", (f["id"],))]
        st, txt = vision.water_state(res["water_pct"], hist)
        res["water_state"], res["water_text"] = st, txt
        res["captured_at"] = iso_now()
        db.ex("INSERT INTO vision(ts,farmer_id,json) VALUES(?,?,?)", (iso_now(), f["id"], json.dumps(res)))
    return res


# ------------------------------------------------------------------ scoring
def day_scores(rows, crop, farmer, now):
    out = []
    for r in rows[-3:]:
        th = ag.thermal_score(r, crop)
        gw = r.get("GWETROOT")
        sp = ag.soil_params(farmer, crop, ag.days_after_planting(farmer, now))
        ws = ag.water_score(None if gw is None else sp["taw"] * ag.clip((0.65 - gw) / 0.40, 0, 1), sp["taw"])
        parts, wts = [th], [0.45]
        if ws is not None:
            parts.append(ws)
            wts.append(0.55)
        sc = sum(p * w for p, w in zip(parts, wts)) / sum(wts)
        out.append({"date": r["date"], "score": round(sc), "tmax": r.get("T2M_MAX"), "gw": gw, "rain": r.get("PRECTOTCORR")})
    return out


def three_day_text(scores, today_overall):
    if len(scores) < 2:
        return "لسه محتاجين كام يوم نقارن بيهم."
    a = scores[-1]["score"]
    b = scores[-2]["score"]
    c = scores[-3]["score"] if len(scores) >= 3 else None
    seq = " ← ".join(str(s["score"]) for s in scores)
    d = a - b
    if d >= 4:
        trend = "الأرض بقت أحسن من اليوم اللي قبله"
    elif d <= -4:
        trend = "الأرض أسوأ شوية من اليوم اللي قبله، خد بالك"
    else:
        trend = "حالة الأرض ثابتة تقريباً"
    extra = ""
    if c is not None and a < c - 8:
        extra = " وفي نزول واضح عن 3 أيام فاتت."
    elif c is not None and a > c + 8:
        extra = " وفي تحسّن واضح عن 3 أيام فاتت."
    return f"درجة الأرض في آخر أيام ناسا المتاحة: {seq}. {trend}.{extra}"


# ------------------------------------------------------------------ report
def build_report(f, progress=None):
    def prog(p, stage):
        if progress:
            progress(p, stage)

    t0 = time.time()
    budget = CFG["report_deadline_seconds"]
    lat, lng = loc(f)
    now = now_local().replace(tzinfo=None)
    crop = ag.CROPS.get(f.get("crop"))
    missing, stale, sources = [], [], []

    prog(8, "بقرا بيانات ناسا")
    pool = ThreadPoolExecutor(max_workers=7)
    futs = {
        "daily": pool.submit(nasa.power_daily, lat, lng),
        "hourly": pool.submit(nasa.power_hourly_latest, lat, lng),
        "aod": pool.submit(nasa.power_aod, lat, lng),
        "ndvi": pool.submit(nasa.modis_ndvi, lat, lng),
        "fc": pool.submit(nasa.forecast, lat, lng),
        "vision": pool.submit(capture_vision, f),
    }
    R = {}
    labels = {"daily": "بيانات ناسا اليومية", "hourly": "قراءة ناسا الساعية", "aod": "جودة الهوا (AOD)",
              "ndvi": "NDVI من MODIS", "fc": "توقعات الساعات الجاية", "vision": "تحليل الدرون"}
    for i, (k, fut) in enumerate(futs.items()):
        remaining = max(1.0, t0 + budget - 9 - time.time())
        try:
            val = fut.result(timeout=remaining)
            if k in ("daily", "hourly", "aod", "ndvi", "fc"):
                val, st = val
                if st:
                    stale.append(labels[k])
            R[k] = val
        except Exception as e:
            missing.append(labels[k])
            R[k] = None
        prog(8 + int(40 * (i + 1) / len(futs)), "بقرا بيانات ناسا" if i < 4 else "الدرون بتصوّر وبحلل")
    pool.shutdown(wait=False)

    prog(55, "بحلل البيانات")
    daily = R["daily"]
    rows = daily["rows"] if daily else []
    elev = (daily or {}).get("elevation") or 50.0
    et0s = [ag.et0_pm(r, lat, elev) for r in rows]
    fc_rows = nasa.upcoming(R["fc"], 72) if R["fc"] else []
    vis = R["vision"] or {"ok": False, "error": "غير متاح"}

    # --- 3 سيناريوهات لكل قرار
    prog(65, "بحط 3 سيناريوهات وبختار الأفضل")
    irr = ag.irrigation_plan(f, crop, rows, et0s, fc_rows, now, elev) if rows else {"level": "unknown", "scenarios": [], "chosen": None}
    hz = ag.hazards(fc_rows, now)
    prot = ag.protection_plan(hz, f.get("cover_state") or "open")
    cropsc = ag.crop_scenarios(f, rows, now) if rows else {"scenarios": [], "chosen": None}

    # --- درجة الصحة
    latest = rows[-1] if rows else None
    ndvi_latest = (R["ndvi"] or {}).get("latest")
    comps = []
    if latest:
        comps.append(("thermal", ag.thermal_score(latest, crop), 0.25))
    if irr.get("depletion_mm") is not None:
        comps.append(("water", ag.water_score(irr["depletion_mm"], irr["soil"]["taw"]), 0.35))
    if ndvi_latest:
        comps.append(("ndvi", ag.ndvi_score(ndvi_latest["ndvi"]), 0.20))
    if vis.get("ok") and vis.get("usable") and vis.get("health") is not None:
        comps.append(("drone", vis["health"], 0.20))
    score = round(sum(c[1] * c[2] for c in comps) / sum(c[2] for c in comps)) if comps else None
    status = "unknown" if score is None else "good" if score >= 70 else "warn" if score >= 50 else "danger"
    if prot["severity"] == "severe":
        status = "danger"
    elif prot["severity"] == "warn" and status == "good":
        status = "warn"

    # --- تنفيذ
    prog(78, "بنفّذ القرار")
    actions_taken = []
    if prot["severity"] == "severe" and prot["close_now"] and CFG["alerts"]["auto_protect"] \
            and (f.get("cover_state") or "open") in ("open", "opening"):
        queue_cover(f, "close", "خطر شديد: " + "، ".join(hz["kinds"]))
        actions_taken.append("قفلت الأرض أوتوماتيك بسبب " + "، ".join(hz["kinds"]))

    # --- 3 أيام
    scores3 = day_scores(rows, crop, f, now) if rows else []
    three = {"scores": scores3, "text": three_day_text(scores3, score)}

    # --- النص بالعامية
    prog(88, "بكتب التقرير")
    text, bullets = compose(f, crop, score, status, irr, prot, cropsc, vis, rows, latest, R, now, actions_taken)

    series = [{"date": r["date"], "tmax": r.get("T2M_MAX"), "tmin": r.get("T2M_MIN"), "rh": r.get("RH2M"),
               "wind": r.get("WS2M"), "rain": r.get("PRECTOTCORR"), "sw": r.get("ALLSKY_SFC_SW_DWN"),
               "gw": r.get("GWETROOT"), "et0": e} for r, e in list(zip(rows, et0s))[-14:]]
    sources = []
    if rows:
        sources.append("NASA POWER (AG community)")
    if R["ndvi"]:
        sources.append("NASA MODIS MOD13Q1 NDVI via ORNL DAAC")
    if R["fc"]:
        sources.append("Open-Meteo (توقعات قصيرة - مش ناسا)")
    if vis.get("ok"):
        sources.append("كاميرا الدرون ESP32-CAM + تحليل صور")

    rep = {
        "generated_at": iso_now(), "elapsed_s": round(time.time() - t0, 1), "farmer_id": f["id"],
        "farmer": {"name": f["name"], "land_no": f["land_no"], "crop": crop["name"] if crop else None, "lat": lat, "lng": lng},
        "score": score, "status": status, "headline": bullets[0]["text"] if bullets else "", "text": text, "bullets": bullets,
        "actions_taken": actions_taken,
        "nasa": {"latest_date": latest["date"] if latest else None, "latest": latest, "hourly": R["hourly"], "aod": R["aod"],
                 "ndvi": R["ndvi"], "series": series, "elevation": elev,
                 "note": "بيانات ناسا بتتأخر من ساعات لأيام - التاريخ اللي فوق هو آخر قراءة متاحة فعلاً."},
        "forecast": {"hazards": {k: v for k, v in hz.items()}, "next24": [
            {"t": r["time"], "temp": r["temp"], "rain": r["rain"], "gust": r["gust"]} for r in fc_rows[:24]]},
        "drone": vis,
        "irrigation": irr, "protection": prot, "crops": cropsc,
        "three_days": three, "missing": missing, "stale": stale, "sources": sources,
        "components": [{"name": c[0], "score": round(c[1]), "weight": c[2]} for c in comps],
    }
    prog(96, "جاهز")
    return rep


def compose(f, crop, score, status, irr, prot, cropsc, vis, rows, latest, R, now, taken):
    b = []
    hour = now.hour
    greet = "صباح الخير" if hour < 12 else "مساء الخير"
    nm = first_name(f)
    if score is None:
        b.append({"icon": "ℹ️", "kind": "info", "text": f"{greet} يا حج {nm}. مقدرتش أجمّع بيانات كفاية دلوقتي، جرّب كمان شوية."})
    else:
        phrase = {"good": "حالتها كويسة", "warn": "محتاجة انتباه شوية", "danger": "محتاجة تتصرف فيها بسرعة"}[status]
        b.append({"icon": {"good": "✅", "warn": "⚠️", "danger": "🚨"}[status], "kind": status,
                  "text": f"{greet} يا حج {nm}. أرضك النهاردة {phrase} ({score} من 100)."})

    # الري
    lvl = irr.get("level")
    chosen = next((c for c in irr.get("scenarios", []) if c["id"] == irr.get("chosen")), None)
    if lvl in ("now", "soon") and chosen:
        st = datetime.fromisoformat(chosen["start"])
        if st <= now.replace(minute=59):
            when = "دلوقتي"
        else:
            day = "النهاردة" if st.date() == now.date() else "بكرة"
            when = f"{day} الساعة {ag.hour_ar(st.hour)}"
        dur = ag.dur_ar(irr["minutes"])
        lead = "الأرض عطشانة" if lvl == "now" else "الأرض قربت تعطش"
        b.append({"icon": "💧", "kind": "irrigate",
                  "text": f"{lead}. روح اسقيها {when} لمدة {dur}. ({chosen['label'].replace('رى ', '')} أقل فقد في المياه)"})
    elif lvl == "ok" and chosen:
        b.append({"icon": "💧", "kind": "irrigate", "text": f"الأرض مرتاحة ومش محتاجة ميّه النهاردة. {chosen['label']}."})
    else:
        b.append({"icon": "💧", "kind": "irrigate", "text": "مقدرتش أحسب احتياج الري دلوقتي لأن بيانات ناسا مش متاحة."})
    if irr.get("assumed_crop"):
        b.append({"icon": "🌱", "kind": "hint", "text": "قولّي إنت زارع إيه وزرعته امتى (من إعدادات الأرض) عشان أظبط حسبة الميّه على محصولك بالظبط."})

    # الحماية
    hz = prot["hazard"]
    if prot["severity"] in ("warn", "severe"):
        kinds = " و".join(hz["kinds"])
        lead = hz.get("lead_h")
        when = "دلوقتي" if lead is None or lead < 0.5 else f"خلال {ag.dur_ar(lead * 60)}"
        if taken:
            b.append({"icon": "🛡️", "kind": "danger", "text": f"{kinds} {when}! أنا قفلت الأرض لوحدي علشان نحمي الزرع."})
        else:
            b.append({"icon": "🛡️", "kind": "danger", "text": f"{kinds} {when}! اضغط «قفل الأرض» عشان نحمي الزرع."})
    elif R.get("fc"):
        b.append({"icon": "🌤️", "kind": "ok", "text": "الجو هادي في الساعات الجاية، سيب الأرض مفتوحة."})

    # الجو
    if latest:
        w = latest
        b.append({"icon": "🌡️", "kind": "weather",
                  "text": f"الجو (آخر قراءة ناسا {w['date']}): حرارة لحد {round(w['T2M_MAX'])}° وأقل حاجة {round(w['T2M_MIN'])}°، رطوبة {round(w['RH2M'])}%، ريح {round((w['WS2M'] or 0) * 3.6)} كم/س."})

    # الدرون
    if vis.get("ok") and vis.get("usable"):
        wt = vis.get("water_text") or ""
        b.append({"icon": "🚁", "kind": "drone", "text": f"الدرون صوّرت أرضك: {vis['health_ar']} ({vis['veg_pct']}% زرع أخضر). {wt}."})
    else:
        b.append({"icon": "🚁", "kind": "drone", "text": f"الدرون: {vis.get('error') or 'اللقطة مش واضحة'}."})

    # المحاصيل
    ch = next((c for c in cropsc.get("scenarios", []) if c["id"] == cropsc.get("chosen")), None)
    if ch:
        why = "، ".join(ch["notes"][:2])
        ah = cropsc.get("after_harvest")
        lead = f"بعد حصاد {ah['crop']} (حوالي {ah['label']}) أنسب حاجة تزرعها {ch['name']}" if ah else f"لو هتزرع دلوقتي، أنسب حاجة ليك {ch['name']}"
        b.append({"icon": "🌾", "kind": "crop", "text": lead + (f" ({why})." if why else ".")})
    text = " ".join(x["text"] for x in b)
    return text, b


# ------------------------------------------------------------------ jobs
def latest_report(fid):
    r = db.q1("SELECT * FROM reports WHERE farmer_id=? ORDER BY id DESC LIMIT 1", (fid,))
    if not r:
        return None
    rep = json.loads(r["json"])
    rep["_id"] = r["id"]
    return rep


def start_job(fid, force=False):
    f = db.q1("SELECT * FROM farmers WHERE id=?", (fid,))
    last = latest_report(fid)
    if last and not force:
        age = (now_local() - datetime.fromisoformat(last["generated_at"])).total_seconds()
        if age < 15 * 60:
            jid = uuid.uuid4().hex[:10]
            JOBS[jid] = {"id": jid, "state": "done", "pct": 100, "stage": "جاهز", "t0": time.time(), "report": last, "cached": True}
            return JOBS[jid]
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "state": "running", "pct": 2, "stage": "بدأت", "t0": time.time(), "report": None, "cached": False}
    JOBS[jid] = job

    def prog(p, s):
        job["pct"], job["stage"] = p, s

    def run():
        try:
            rep = build_report(f, prog)
            rid = db.ex("INSERT INTO reports(farmer_id,day,created_at,score,json) VALUES(?,?,?,?,?)",
                        (fid, now_local().strftime("%Y-%m-%d"), iso_now(), rep["score"], json.dumps(rep, ensure_ascii=False)))
            rep["_id"] = rid
            job["report"], job["state"], job["pct"], job["stage"] = rep, "done", 100, "جاهز"
            db.event("report", f"تقرير جديد لـ {f['name']} (الدرجة {rep['score']}) في {rep['elapsed_s']} ثانية", fid)
        except Exception as e:  # لا نخفي الفشل
            job["state"], job["error"] = "error", f"{e.__class__.__name__}: {e}"
            db.event("error", f"فشل التقرير لـ {f['name']}: {e}", fid)

    threading.Thread(target=run, daemon=True).start()
    return job


def job_public(job):
    d = {k: job[k] for k in ("id", "state", "pct", "stage", "cached") if k in job}
    d["elapsed"] = round(time.time() - job["t0"], 1)
    d["limit"] = CFG["report_deadline_seconds"]
    if job.get("error"):
        d["error"] = job["error"]
    if job["state"] == "done":
        d["report"] = job["report"]
    return d


# ------------------------------------------------------------------ alerts / scheduler
def current_alert(f):
    """تنبيه سريع (توقعات + قواعد) للتطبيق: بيتنادى كل دقيقة."""
    lat, lng = loc(f)
    try:
        fc, _ = nasa.forecast(lat, lng)
    except Exception:
        return {"severity": "unknown", "kinds": []}
    now = now_local().replace(tzinfo=None)
    hz = ag.hazards(nasa.upcoming(fc, 6), now)
    return {"severity": hz["severity"], "kinds": hz["kinds"], "lead_h": hz["lead_h"], "gust": hz["gust"], "rain": hz["rain"]}


def check_all():
    for f in db.q("SELECT * FROM farmers WHERE status='confirmed' AND lat IS NOT NULL"):
        try:
            a = current_alert(f)
            fid = f["id"]
            st = _alert_state.setdefault(fid, {"last": 0, "clear": 0})
            kinds = "، ".join(a.get("kinds", []))
            if a["severity"] == "severe":
                if CFG["alerts"]["auto_protect"] and (f.get("cover_state") or "open") in ("open", "opening"):
                    queue_cover(f, "close", f"خطر شديد: {kinds}")
                    whatsapp.send_text(f, f"🚨 يا حج {first_name(f)}: {kinds} جاية. قفلت الأرض لوحدي لحماية الزرع.")
                st["clear"] = 0
            elif a["severity"] == "warn":
                if time.time() - st["last"] > 3 * 3600:
                    whatsapp.send_text(f, f"⚠️ يا حج {first_name(f)}: متوقع {kinds} خلال ساعات. افتح التطبيق واضغط «قفل الأرض» لو حابب.")
                    db.event("alert", f"تنبيه {kinds} لـ {f['name']}", fid)
                    st["last"] = time.time()
                st["clear"] = 0
            else:
                st["clear"] += 1
                if f.get("cover_state") in ("closed", "closing") and st["clear"] == 2:
                    whatsapp.send_text(f, f"🌤️ يا حج {first_name(f)}: الجو هدي. تقدر تفتح الأرض من التطبيق.")
                    db.event("alert", f"الجو هدي - ينفع نفتح أرض {f['name']}", fid)
        except Exception as e:
            db.event("error", f"فحص التنبيهات فشل: {e}", f["id"])


def scheduler():
    def loop():
        time.sleep(20)
        while True:
            try:
                check_all()
            except Exception:
                pass
            time.sleep(max(1, CFG["alerts"]["check_every_minutes"]) * 60)
    threading.Thread(target=loop, daemon=True).start()
