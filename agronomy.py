# -*- coding: utf-8 -*-
"""
علم الزراعة (FAO-56): تبخر-نتح مرجعي (Penman-Monteith)، احتياج الري، دورة زراعية، وحماية.
كل قرار بيتبني على 3 سيناريوهات كلها قابلة للتنفيذ (تعدّي القيود الصلبة) وبنختار الأفضل بنقاط.
"""
import json
import math
from datetime import datetime, timedelta

from common import CFG, ROOT

_KB = json.loads((ROOT / "server" / "data" / "crops.json").read_text(encoding="utf-8"))
CROPS = {c["id"]: c for c in _KB["crops"]}
SOILS = _KB["soils"]


def clip(x, lo, hi):
    return max(lo, min(hi, x))


# ------------------------------------------------------------------ ET0
def _es(t):
    return 0.6108 * math.exp(17.27 * t / (t + 237.3))


def ra_mj(lat, doy):
    phi = math.radians(lat)
    dr = 1 + 0.033 * math.cos(2 * math.pi / 365 * doy)
    d = 0.409 * math.sin(2 * math.pi / 365 * doy - 1.39)
    ws = math.acos(clip(-math.tan(phi) * math.tan(d), -1, 1))
    return 24 * 60 / math.pi * 0.0820 * dr * (ws * math.sin(phi) * math.sin(d) + math.cos(phi) * math.cos(d) * math.sin(ws))


def et0_pm(row, lat, elev=50.0):
    """FAO-56 Penman-Monteith (مم/يوم) من بيانات ناسا POWER اليومية."""
    try:
        tmax, tmin = row["T2M_MAX"], row["T2M_MIN"]
        rh, u2, rs = row["RH2M"], row["WS2M"], row["ALLSKY_SFC_SW_DWN"]
        if None in (tmax, tmin, rh, u2, rs):
            return None
        doy = datetime.strptime(row["date"], "%Y-%m-%d").timetuple().tm_yday
        tmean = (tmax + tmin) / 2
        p = row.get("PS") or 101.3 * ((293 - 0.0065 * elev) / 293) ** 5.26
        gamma = 0.000665 * p
        es = (_es(tmax) + _es(tmin)) / 2
        ea = es * clip(rh, 1, 100) / 100
        delta = 4098 * _es(tmean) / (tmean + 237.3) ** 2
        rns = 0.77 * rs
        ra = ra_mj(lat, doy)
        rso = (0.75 + 2e-5 * elev) * ra
        rel = clip(rs / rso if rso > 0 else 0.7, 0.3, 1.0)
        rnl = 4.903e-9 * (((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2) * (0.34 - 0.14 * math.sqrt(ea)) * (1.35 * rel - 0.35)
        rn = rns - rnl
        et0 = (0.408 * delta * rn + gamma * 900 / (tmean + 273) * u2 * (es - ea)) / (delta + gamma * (1 + 0.34 * u2))
        return round(max(0.0, et0), 2)
    except Exception:
        return None


# ------------------------------------------------------------------ crop stage
def days_after_planting(farmer, now):
    pd = farmer.get("planting_date")
    if not pd:
        return None
    try:
        return max(0, (now.date() - datetime.strptime(pd[:10], "%Y-%m-%d").date()).days)
    except Exception:
        return None


def kc_at(crop, dap):
    ini, mid, end = crop["kc"]
    s1, s2, s3, s4 = crop["stages"]
    if dap is None:
        return round(mid * 0.9, 2)
    if dap < s1:
        return ini
    if dap < s1 + s2:
        return round(ini + (mid - ini) * (dap - s1) / s2, 2)
    if dap < s1 + s2 + s3:
        return mid
    if dap < s1 + s2 + s3 + s4:
        return round(mid + (end - mid) * (dap - s1 - s2 - s3) / s4, 2)
    return end


def stage_ar(crop, dap):
    if dap is None:
        return None
    s1, s2, s3, s4 = crop["stages"]
    if dap < s1:
        return "إنبات وبداية نمو"
    if dap < s1 + s2:
        return "نمو خضري"
    if dap < s1 + s2 + s3:
        return "إزهار وتكوين محصول (أكتر مرحلة عطشانة)"
    if dap < s1 + s2 + s3 + s4:
        return "نضج"
    return "قرب الحصاد"


def soil_params(farmer, crop, dap):
    st = farmer.get("soil_type") or CFG["defaults"]["soil_type"]
    awc = SOILS.get(st, SOILS["clay_loam"])["awc"]
    rmax = crop["root"] if crop else 0.8
    if crop and dap is not None:
        dev = crop["stages"][0] + crop["stages"][1]
        root = rmax * clip(0.3 + 0.7 * dap / max(dev, 1), 0.3, 1.0)
    else:
        root = rmax * 0.8
    taw = awc * root
    p = crop["p"] if crop else 0.5
    return {"soil": st, "awc": awc, "root_m": round(root, 2), "taw": round(taw, 1), "raw": round(p * taw, 1)}


# ------------------------------------------------------------------ formatting helpers (عامية)
def dur_ar(minutes):
    m = int(round(minutes / 15.0) * 15)
    m = max(m, 15)
    if m < 60:
        return f"{m} دقيقة"
    h, r = divmod(m, 60)
    if h == 1 and r == 0:
        return "ساعة"
    if h == 2 and r == 0:
        return "ساعتين"
    if r == 0:
        return f"{h} ساعات" if h <= 10 else f"{h} ساعة"
    if r == 30:
        return "ساعة ونص" if h == 1 else f"{h} ساعات ونص"
    return f"{h} ساعة و{r} دقيقة"


def hour_ar(h, m=0):
    mm = f":{m:02d}" if m else ""
    hh = h % 12 or 12
    if h < 4:
        part = "بالليل"
    elif h < 12:
        part = "الصبح"
    elif h < 16:
        part = "الضهر"
    elif h < 19:
        part = "العصر"
    elif h < 21:
        part = "المغرب"
    else:
        part = "بالليل"
    return f"{hh}{mm} {part}"


# ------------------------------------------------------------------ irrigation
WINDOWS = [("dawn", 5, 8, "الفجر/الصبح بدري"), ("eve", 17, 20, "بعد العصر"), ("night", 21, 24, "بالليل")]


def _next_window(now, h0, h1):
    """أقرب بداية للنافذة (ولو احنا جواها دلوقتي بنبدأ حالاً)."""
    start = now.replace(hour=h0 % 24, minute=0, second=0, microsecond=0)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=h1)
    if now >= end:
        start += timedelta(days=1)
        end += timedelta(days=1)
    if start < now < end:
        start = now.replace(minute=0, second=0, microsecond=0)
    return start


def _window_weather(fc_rows, start, hours, fallback):
    sel = [r for r in fc_rows if start <= r["dt"] < start + timedelta(hours=hours)]
    if not sel:
        return {**fallback, "src": "nasa"}
    return {"temp": sum(r["temp"] for r in sel) / len(sel), "wind": max(r["gust"] or r["wind"] for r in sel),
            "rain": sum(r["rain"] for r in sel), "src": "forecast"}


def irrigation_plan(farmer, crop, rows, et0s, fc_rows, now, elev=50.0):
    dap = days_after_planting(farmer, now)
    sp = soil_params(farmer, crop, dap)
    kc = kc_at(crop, dap) if crop else 0.9
    etc = [None if e is None else round(e * kc, 2) for e in et0s]
    valid_etc = [e for e in etc[-3:] if e is not None]
    etc_recent = sum(valid_etc) / len(valid_etc) if valid_etc else None

    last = rows[-1] if rows else None
    gw = last.get("GWETROOT") if last else None
    dr_nasa = None if gw is None else sp["taw"] * clip((0.65 - gw) / 0.40, 0, 1)

    dr_et, basis = None, None
    li = farmer.get("last_irrigated")
    if li:
        try:
            ld = datetime.fromisoformat(li[:19]).date()
            s = 0.0
            for r, e in zip(rows, etc):
                if e is None:
                    continue
                if datetime.strptime(r["date"], "%Y-%m-%d").date() > ld:
                    s += e - 0.8 * (r.get("PRECTOTCORR") or 0)
            days_since = (now.date() - ld).days
            if days_since <= 14:
                dr_et = clip(s + (etc_recent or 0) * max(0, days_since - sum(1 for r in rows if datetime.strptime(r["date"], "%Y-%m-%d").date() > ld)), 0, sp["taw"])
                basis = "ميزان المياه منذ آخر رية سجّلتها"
        except Exception:
            pass
    if dr_et is not None:
        dr = dr_et
    elif dr_nasa is not None:
        dr, basis = dr_nasa, "رطوبة التربة من ناسا (GWETROOT)"
    else:
        dr = None

    out = {"dap": dap, "kc": kc, "stage": stage_ar(crop, dap) if crop else None, "soil": sp,
           "et0_last": next((e for e in reversed(et0s) if e is not None), None),
           "etc_recent": None if etc_recent is None else round(etc_recent, 2),
           "depletion_mm": None if dr is None else round(dr, 1), "basis": basis,
           "gwetroot": gw, "assumed_crop": crop is None or dap is None}
    if dr is None or etc_recent is None:
        out.update(level="unknown", ratio=None, scenarios=[], chosen=None)
        return out

    ratio = dr / max(sp["raw"], 1.0)
    out["ratio"] = round(ratio, 2)
    level = "now" if ratio >= 0.85 else "soon" if ratio >= 0.6 else "ok"
    out["level"] = level

    eff = CFG["defaults"]["irrigation_efficiency"]
    rate = CFG["defaults"]["application_rate_mm_h"]
    pump, area = CFG["defaults"].get("pump_flow_m3_h") or 0, farmer.get("area_m2") or 0
    if pump and area:
        rate = clip(pump / area * 1000, 5, 80)  # مم/ساعة من تصرف الطلمبة ومساحة الأرض
    out["rate_mm_h"] = round(rate, 1)
    fallback_wx = {"temp": (last["T2M_MAX"] + last["T2M_MIN"]) / 2 if last else 25,
                   "wind": (last["WS2M"] or 2) * 3.6 * 1.6 if last else 10, "rain": 0}
    rain24 = sum(r["rain"] for r in fc_rows if r["dt"] < now + timedelta(hours=24)) if fc_rows else 0.0
    out["rain_next_24h_mm"] = round(rain24, 1)

    net = dr if level != "ok" else 0.4 * sp["raw"]
    net = min(max(net - 0.8 * rain24, 0), CFG["defaults"].get("max_irrigation_mm", 50))
    gross = net / eff
    minutes = clip(gross / rate * 60, 20, 600)
    out["net_mm"], out["minutes"] = round(net, 1), int(minutes)

    cands = []
    if level in ("now", "soon"):
        max_delay = 20 if level == "now" else 36
        for wid, h0, h1, label in WINDOWS:
            start = _next_window(now, h0, h1)
            delay = (start - now).total_seconds() / 3600
            wx = _window_weather(fc_rows, start, 3, fallback_wx)
            loss = clip(0.10 + 0.012 * max(0, wx["temp"] - 20) + 0.0025 * wx["wind"], 0.10, 0.50)
            stress_risk = clip(delay / 24 * (1.0 if ratio >= 1 else 0.6), 0, 1)
            rain_pen = 15 if wx["rain"] > 1 else 0
            night_pen = 8 if wid == "night" else 0
            score = 100 - 120 * loss - 50 * stress_risk - night_pen - rain_pen
            cands.append({"id": wid, "label": f"رى {label}", "start": start.isoformat(timespec="minutes"),
                          "feasible": delay <= max_delay, "score": round(score, 1),
                          "why": f"فقد مياه متوقع ~{int(loss * 100)}% ({'من التوقعات' if wx['src'] == 'forecast' else 'من بيانات ناسا'})"
                                 + (" - فيه مطر متوقع فبيتجنّب" if rain_pen else "")})
    else:
        et = etc_recent
        for did, label, d, dose in (("skip", "مفيش رى النهاردة وراجع بكرة", 1, 0.0),
                                    ("light", "رية خفيفة وقائية الفجر", 0, 0.4),
                                    ("wait2", "استنى يومين وبعدين اسقي", 2, 0.0)):
            proj = ratio + et * d / max(sp["raw"], 1) - (dose * 0.8)
            feasible = proj < 0.95
            score = 100 - 45 * max(proj, 0) - (18 if dose else 0)
            cands.append({"id": did, "label": label, "feasible": feasible, "score": round(score, 1),
                          "why": f"بعد {d} يوم هتبقى نسبة الاستنزاف حوالي {int(max(proj,0) * 100)}% من الحد الآمن"})
    feas = [c for c in cands if c["feasible"]] or cands
    best = max(feas, key=lambda c: c["score"])
    out["scenarios"], out["chosen"] = cands, best["id"]
    return out


# ------------------------------------------------------------------ protection
def hazards(fc_rows, now, nasa_last=None):
    A = CFG["alerts"]
    res = {"gust": 0.0, "rain": 0.0, "tmax": None, "tmin": None, "lead_h": None, "kinds": [], "severity": "none"}
    up = [r for r in fc_rows if now.replace(tzinfo=None) <= r["dt"] < now.replace(tzinfo=None) + timedelta(hours=6)] if fc_rows else []
    if not up:
        return res
    res["gust"] = max(r["gust"] or r["wind"] or 0 for r in up)
    res["rain"] = round(sum(r["rain"] for r in up), 1)
    res["tmax"] = max(r["temp"] for r in up)
    res["tmin"] = min(r["temp"] for r in up)
    first = None
    for r in up:
        if (r["gust"] or 0) >= A["gust_kmh_warn"] or r["rain"] >= 0.5:
            first = r["dt"]
            break
    if first:
        res["lead_h"] = max(0, (first - now.replace(tzinfo=None)).total_seconds() / 3600)
    sev = "none"
    if res["gust"] >= A["gust_kmh_warn"]:
        res["kinds"].append("ريح جامدة")
        sev = "warn"
    if res["rain"] >= A["rain_mm_warn"]:
        res["kinds"].append("مطر")
        sev = "warn"
    if res["tmax"] >= A["heat_c_warn"]:
        res["kinds"].append("موجة حر")
        sev = "warn"
    if res["tmin"] <= A["frost_c_warn"]:
        res["kinds"].append("صقيع")
        sev = "warn"
    if res["gust"] >= A["gust_kmh_severe"] or res["rain"] >= A["rain_mm_severe"]:
        sev = "severe"
    res["severity"] = sev
    return res


def protection_plan(hz, cover_state):
    sev = hz["severity"]
    lead = hz["lead_h"] if hz["lead_h"] is not None else 6
    if sev == "severe":
        c = [{"id": "close_now", "label": "قفل الأرض فوراً", "feasible": True, "score": 90, "why": "أقل خطر على الزرع"},
             {"id": "close_before", "label": "قفل قبل الخطر بنص ساعة", "feasible": lead >= 1, "score": 80 - 25 * max(0, 1 - lead),
              "why": "بيسيب الأرض متهوّية لحد آخر لحظة"},
             {"id": "close_notify", "label": "قفل فوراً + تنبيه تفتح لما الخطر يعدّي", "feasible": True, "score": 93,
              "why": "حماية كاملة ومتابعة لفتح الأرض بعد ما الجو يهدى"}]
    elif sev == "warn":
        c = [{"id": "close_before", "label": "قفل قبل الخطر بساعة", "feasible": lead >= 1.2, "score": 85, "why": "تحمي الزرع وتهوّي أطول وقت"},
             {"id": "close_now", "label": "قفل دلوقتي", "feasible": True, "score": 82, "why": "أمان أعلى مع تهوية أقل"},
             {"id": "watch", "label": "سيبها مفتوحة ونراقب كل 15 دقيقة", "feasible": lead >= 2, "score": 78,
              "why": "الخطر لسه بعيد ساعتين+"}]
    else:
        c = [{"id": "keep_open", "label": "سيب الأرض مفتوحة", "feasible": True, "score": 95, "why": "مفيش خطر في الست ساعات الجايين"},
             {"id": "watch", "label": "مفتوحة مع مراقبة كل 15 دقيقة", "feasible": True, "score": 92, "why": "نفس الحال مع تنبيه فوري"},
             {"id": "precaution", "label": "قفل وقائي", "feasible": True, "score": 55, "why": "بيقلل التهوية من غير داعي"}]
    feas = [x for x in c if x["feasible"]] or c
    best = max(feas, key=lambda x: x["score"])
    return {"severity": sev, "hazard": hz, "scenarios": c, "chosen": best["id"],
            "close_now": best["id"] in ("close_now", "close_notify"), "cover_state": cover_state}


# ------------------------------------------------------------------ crops / rotation
MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def crop_scenarios(farmer, rows, now):
    tm = [r["T2M"] for r in rows[-14:] if r.get("T2M") is not None]
    tmean = sum(tm) / len(tm) if tm else None
    ec = farmer.get("salinity")
    ec = CFG["defaults"]["salinity_ds_m"] if ec is None else ec
    cur = CROPS.get(farmer.get("crop"))
    plan, after = now, None
    if cur and farmer.get("planting_date"):
        try:
            harvest = datetime.strptime(farmer["planting_date"][:10], "%Y-%m-%d") + timedelta(days=cur["days"])
            if harvest > now:
                plan, after = harvest, {"crop": cur["name"], "date": harvest.date().isoformat(),
                                        "label": f"{MONTHS_AR[harvest.month - 1]} {harvest.year}"}
        except Exception:
            pass
    prev = cur or CROPS.get(farmer.get("prev_crop"))
    try:
        pr = json.loads(farmer.get("priorities") or "[]") if isinstance(farmer.get("priorities"), str) else (farmer.get("priorities") or [])
    except Exception:
        pr = []
    order = [p for p in pr if p in ("water", "income", "soil")]
    for p in ("water", "income", "soil"):
        if p not in order:
            order.append(p)
    w = dict(zip(order, (0.5, 0.3, 0.2)))

    m, nm = plan.month, plan.month % 12 + 1
    near = (plan - now).days <= 30
    cands = []
    for c in CROPS.values():
        why_no = []
        in_win = m in c["sow"]
        soon = nm in c["sow"] and plan.day >= 16
        if not (in_win or soon):
            why_no.append("خارج ميعاد الزراعة")
        if in_win and near and tmean is not None and not (c["tmin"] <= tmean <= c["tmax"]):
            why_no.append("الحرارة الحالية مش مناسبة")
        if ec > c["salt"]:
            why_no.append("الملوحة أعلى مما يتحمله")
        if prev and prev["family"] == c["family"]:
            why_no.append("نفس عيلة المحصول السابق (بيزوّد الأمراض)")
        if why_no:
            continue
        if tmean is None or not in_win or not near:
            tfit = 0.7
        else:
            lo, hi = c["topt"]
            tfit = 1.0 if lo <= tmean <= hi else clip(1 - (lo - tmean) / max(lo - c["tmin"], 1), 0, 1) if tmean < lo else clip(1 - (tmean - hi) / max(c["tmax"] - hi, 1), 0, 1)
        timing = 1.0 if in_win else 0.75
        salt_m = clip((c["salt"] - ec) / c["salt"], 0, 1)
        water_s = clip(1 - c["water"] / 1200, 0, 1)
        income_s = c["value"] / 5
        soil_s = {1: 1.0, 0: 0.5, -1: 0.2}[c["n"]]
        notes = []
        if prev:
            if prev["legume"] and c["group"] == "cereal":
                soil_s = min(1.0, soil_s + 0.3)
                notes.append("بتستفيد من النيتروجين اللي سابه المحصول اللي فات")
            if prev["group"] == "cereal" and c["legume"]:
                soil_s = min(1.0, soil_s + 0.3)
                notes.append("بتحسّن التربة بعد الحبوب")
            if prev["n"] == -1 and c["n"] == -1:
                soil_s -= 0.1
        if c["legume"]:
            notes.append("بتثبّت نيتروجين في الأرض")
        if c["water"] <= 450:
            notes.append("استهلاكها للمياه قليل")
        if salt_m > 0.6 and ec >= 1:
            notes.append("بتستحمل ملوحة أرضك")
        if not in_win:
            notes.append("ميعادها بيبدأ الشهر الجاي - جهّز الأرض")
        score = 100 * (0.25 * tfit + 0.15 * timing + 0.10 * salt_m + 0.5 * (w["water"] * water_s + w["income"] * income_s + w["soil"] * clip(soil_s, 0, 1)))
        cands.append({"id": c["id"], "name": c["name"], "score": round(score, 1), "feasible": True, "water_mm": c["water"],
                      "days": c["days"], "notes": notes, "in_window": in_win})
    cands.sort(key=lambda x: -x["score"])
    top = cands[:3]
    return {"tmean14": None if tmean is None else round(tmean, 1), "salinity": ec, "prev": prev["name"] if prev else None,
            "priorities": order, "scenarios": top, "chosen": top[0]["id"] if top else None, "n_feasible": len(cands),
            "after_harvest": after, "plan_date": plan.date().isoformat()}


# ------------------------------------------------------------------ health
def thermal_score(row, crop):
    lo, hi = (crop["topt"] if crop else (12, 28))
    tmin_c, tmax_c = (crop["tmin"], crop["tmax"]) if crop else (4, 40)
    t, tx = row["T2M"], row["T2M_MAX"] if row.get("T2M_MAX") is not None else row["T2M"]
    s = 100.0
    if tx > hi + 6:
        s -= clip((tx - (hi + 6)) / max(tmax_c - (hi + 6), 2) * 100, 0, 100)
    if t < lo - 8:
        s -= clip(((lo - 8) - t) / max((lo - 8) - tmin_c, 2) * 60, 0, 60)
    return clip(s, 0, 100)


def water_score(dr, taw):
    if dr is None or not taw:
        return None
    return clip(100 * (1 - (dr / taw) ** 1.2), 0, 100)


def ndvi_score(v):
    return None if v is None else clip((v - 0.15) / 0.65 * 100, 0, 100)
