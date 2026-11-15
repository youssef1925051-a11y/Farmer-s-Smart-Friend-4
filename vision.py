# -*- coding: utf-8 -*-
"""
رؤية حاسوبية حقيقية على لقطة كاميرا الدرون (ESP32-CAM):
  - نسبة النبات الأخضر (ExG) وصحة الورق (VARI + نسبة الاصفرار)
  - نسبة التربة العارية ولونها (جفاف/رطوبة تقريبية)
  - وجود مياه الترعة (نسبة البكسلات المائية) وتغيّرها عن آخر لقطات
بتشتغل على numpy+Pillow. مقاييس تقديرية من كاميرا RGB عادية (مش NDVI حقيقي).
"""
import io


def analyze_jpeg(data):
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return {"ok": False, "error": "Pillow/numpy غير مثبتين"}
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as e:  # لقطة تالفة
        return {"ok": False, "error": f"صورة غير صالحة: {e}"}
    im.thumbnail((320, 320))
    a = np.asarray(im).astype(np.float32)
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    s = R + G + B + 1e-6
    r, g, b = R / s, G / s, B / s
    exg = 2 * g - r - b
    bright = s / 3.0

    sky = (bright > 150) & (np.abs(R - G) < 18) & (np.abs(G - B) < 25)
    valid = ~sky
    nvalid = max(int(valid.sum()), 1)

    veg = (exg > 0.06) & (G > R * 0.95) & (G > B * 1.05) & valid
    water = (B > R + 10) & (B >= G * 0.9) & (bright < 200) & valid & ~veg
    soil = (R > G) & (G >= B * 0.9) & (exg < 0.03) & (bright > 35) & valid

    veg_frac = float(veg.sum()) / nvalid
    water_frac = float(water.sum()) / nvalid
    soil_frac = float(soil.sum()) / nvalid

    if veg.sum() > 30:
        vari = ((G - R) / (G + R - B + 1e-6))[veg]
        vari_mean = float(np.clip(vari, -1, 1).mean())
        yellow = float(((R[veg] > 0.98 * G[veg])).mean())
    else:
        vari_mean, yellow = 0.0, 0.0

    gray = a.mean(axis=2)
    sharp = float(np.var(np.diff(gray, axis=1))) if gray.shape[1] > 1 else 0.0
    mean_bright = float(bright.mean())
    usable = mean_bright > 30 and sharp > 5

    soil_dark = None
    if soil.sum() > 30:
        soil_dark = float(bright[soil].mean())  # أغمق = أرطب غالبًا

    green_part = 0.55 * min(max(vari_mean / 0.20, 0), 1) + 0.45 * (1 - yellow)
    cover_part = 0.4 + 0.6 * min(veg_frac / 0.5, 1)
    health = round(100 * green_part * cover_part, 1) if veg_frac > 0.04 else None

    if health is None:
        health_ar = "مش باين زرع واضح في اللقطة"
    elif health >= 70:
        health_ar = "الورق أخضر وصحي"
    elif health >= 50:
        health_ar = "الزرع مقبول وفيه شوية اصفرار"
    else:
        health_ar = "الزرع ضعيف أو مصفر"

    return {
        "ok": True, "usable": bool(usable),
        "veg_pct": round(veg_frac * 100, 1), "water_pct": round(water_frac * 100, 1),
        "soil_pct": round(soil_frac * 100, 1), "yellow_pct": round(yellow * 100, 1),
        "vari": round(vari_mean, 3), "health": health, "health_ar": health_ar,
        "soil_brightness": None if soil_dark is None else round(soil_dark, 1),
        "brightness": round(mean_bright, 1), "sharpness": round(sharp, 1),
        "size": list(im.size),
    }


def water_state(water_pct, history):
    """مقارنة مياه الترعة باللقطات اللي فاتت (نسبي، مش لترات)."""
    hist = [h for h in history if h is not None]
    if water_pct is None:
        return None, "مش قادر أقيس الترعة من اللقطة دي"
    if water_pct < 1.5:
        return "none", "مفيش مياه باينة في الترعة من الدرون"
    if len(hist) >= 3:
        base = sum(hist) / len(hist)
        if base > 0:
            ratio = water_pct / base
            if ratio < 0.6:
                return "low", "المياه في الترعة أقل من المعتاد"
            if ratio > 1.4:
                return "high", "المياه في الترعة أعلى من المعتاد"
    return "ok", "المياه موجودة في الترعة"
