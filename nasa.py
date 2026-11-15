# -*- coding: utf-8 -*-
"""
مصادر البيانات الحقيقية:
  - NASA POWER  (حرارة، رطوبة، رياح، أمطار، إشعاع شمسي، ضغط، رطوبة التربة GWETROOT/GWETTOP، AOD)
  - NASA MODIS MOD13Q1 عن طريق ORNL DAAC (NDVI حقيقي للموقع)
  - Open-Meteo (مش ناسا) للتنبيهات القصيرة: ريح/مطر خلال الساعات الجاية - بيتعلّم في التقرير بمصدره
مفيش أي بيانات متولّدة: لو المصدر وقع بنرجّع آخر نسخة ناجحة معلّمة "قديمة" أو بنقول "غير متاح".
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from common import CFG, cached, http_json, now_local

POWER_PARAMS = "T2M,T2M_MAX,T2M_MIN,RH2M,WS2M,PRECTOTCORR,ALLSKY_SFC_SW_DWN,PS,GWETROOT,GWETTOP"


def _clean(v):
    return None if v is None or v <= -990 else v


def _rk(x):
    return f"{round(x, 2)}"


def power_daily(lat, lng, days=30):
    def fetch():
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=days)
        qs = urlencode({
            "parameters": POWER_PARAMS, "community": "AG", "longitude": lng, "latitude": lat,
            "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"), "format": "JSON"})
        js = http_json(f"{CFG['nasa']['power_base']}/daily/point?{qs}", timeout=22)
        par = js["properties"]["parameter"]
        elev = None
        try:
            elev = js["geometry"]["coordinates"][2]
        except Exception:
            pass
        rows = []
        for d in sorted(par["T2M"].keys()):
            row = {"date": f"{d[:4]}-{d[4:6]}-{d[6:]}"}
            for k in POWER_PARAMS.split(","):
                row[k] = _clean(par.get(k, {}).get(d))
            if row["T2M"] is None:
                continue
            rows.append(row)
        return {"rows": rows, "elevation": elev}

    return cached(f"power_daily_{_rk(lat)}_{_rk(lng)}", 1800, fetch)


def power_hourly_latest(lat, lng):
    """آخر قراءة ساعية متاحة من ناسا (بتتأخر ساعات/يوم)."""
    def fetch():
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=1)
        qs = urlencode({
            "parameters": "T2M,RH2M,WS2M,PRECTOTCORR", "community": "AG", "longitude": lng, "latitude": lat,
            "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"), "format": "JSON",
            "time-standard": "UTC"})
        js = http_json(f"{CFG['nasa']['power_base']}/hourly/point?{qs}", timeout=22)
        par = js["properties"]["parameter"]
        last = None
        for k in sorted(par["T2M"].keys()):
            if _clean(par["T2M"][k]) is not None:
                last = k
        if not last:
            raise ValueError("no hourly data")
        return {"time_utc": f"{last[:4]}-{last[4:6]}-{last[6:8]} {last[8:10]}:00",
                **{p: _clean(par[p].get(last)) for p in ("T2M", "RH2M", "WS2M", "PRECTOTCORR")}}

    return cached(f"power_hourly_{_rk(lat)}_{_rk(lng)}", 900, fetch)


def power_aod(lat, lng):
    """عمق الهباء الجوي (AOD) - أقرب مقياس لجودة/عكارة الهوا متاح من ناسا. مش pH ولا حموضة."""
    def fetch():
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=10)
        qs = urlencode({
            "parameters": "AOD_55", "community": "AG", "longitude": lng, "latitude": lat,
            "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"), "format": "JSON"})
        js = http_json(f"{CFG['nasa']['power_base']}/daily/point?{qs}", timeout=20)
        par = js["properties"]["parameter"]["AOD_55"]
        last = None
        for k in sorted(par):
            if _clean(par[k]) is not None:
                last = k
        if not last:
            raise ValueError("no AOD")
        return {"date": f"{last[:4]}-{last[4:6]}-{last[6:]}", "aod": _clean(par[last])}

    return cached(f"power_aod_{_rk(lat)}_{_rk(lng)}", 3600 * 6, fetch)


def modis_ndvi(lat, lng):
    """NDVI حقيقي من MODIS MOD13Q1 (250 متر، تركيب كل 16 يوم) عن طريق ORNL DAAC التابع لناسا."""
    base = CFG["nasa"]["modis_base"]

    def fetch():
        loc = urlencode({"latitude": lat, "longitude": lng})
        dates = http_json(f"{base}/MOD13Q1/dates?{loc}", timeout=20)["dates"]
        if not dates:
            raise ValueError("no modis dates")
        pick = dates[-5:]
        qs = urlencode({
            "latitude": lat, "longitude": lng, "band": "250m_16_days_NDVI",
            "startDate": pick[0]["modis_date"], "endDate": pick[-1]["modis_date"],
            "kmAboveBelow": 0, "kmLeftRight": 0})
        js = http_json(f"{base}/MOD13Q1/subset?{qs}", timeout=22)
        series = []
        for item in js.get("subset", []):
            vals = [v for v in item.get("data", []) if v is not None and v > -2000]
            if vals:
                series.append({"date": item["calendar_date"], "ndvi": round(sum(vals) / len(vals) * 0.0001, 3)})
        if not series:
            raise ValueError("empty ndvi")
        return {"series": series, "latest": series[-1]}

    return cached(f"modis_ndvi_{_rk(lat)}_{_rk(lng)}", 3600 * 12, fetch)


def forecast(lat, lng):
    """توقعات الساعات الجاية (Open-Meteo) - مصدر مش ناسا، للتنبيه القصير بس."""
    def fetch():
        qs = urlencode({
            "latitude": lat, "longitude": lng, "timezone": "Africa/Cairo", "forecast_days": 3,
            "hourly": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,wind_gusts_10m"})
        js = http_json(f"{CFG['forecast']['base']}?{qs}", timeout=15)
        h = js["hourly"]
        out = []
        for i, t in enumerate(h["time"]):
            out.append({"time": t, "temp": h["temperature_2m"][i], "rh": h["relative_humidity_2m"][i],
                        "rain": h["precipitation"][i] or 0.0, "wind": h["wind_speed_10m"][i],
                        "gust": h["wind_gusts_10m"][i]})
        return out

    return cached(f"forecast_{_rk(lat)}_{_rk(lng)}", 600, fetch)


def upcoming(fc, hours):
    """الساعات من دلوقتي لحد +hours (بتوقيت القاهرة)."""
    now = now_local().replace(tzinfo=None, minute=0, second=0, microsecond=0)
    res = []
    for r in fc or []:
        try:
            t = datetime.fromisoformat(r["time"])
        except Exception:
            continue
        if now <= t < now + timedelta(hours=hours):
            res.append({**r, "dt": t})
    return res
