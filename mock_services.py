# -*- coding: utf-8 -*-
"""
⚠️ للاختبار بس - مش جزء من النظام الحقيقي ⚠️
بيحاكي شكل ردود NASA POWER / MODIS-ORNL / Open-Meteo وكاميرا ESP32 عشان نختبر السيرفر من غير إنترنت.
النظام الحقيقي بيكلّم الخدمات الحقيقية (انظر config.json). الأرقام هنا مش حقيقية.
"""
import io
import json
import math
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
STATE = {"storm": False}


def jpeg_bytes():
    im = Image.open(ROOT / "web" / "assets" / "wheat.jpg").convert("RGB")
    b = io.BytesIO()
    im.save(b, "JPEG", quality=80)
    return b.getvalue()


JPG = jpeg_bytes()


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def out(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        if p == "/__storm":
            STATE["storm"] = q.get("on", ["0"])[0] == "1"
            return self.out({"storm": STATE["storm"]})
        if p == "/power/daily/point":
            params = q["parameters"][0].split(",")
            end = datetime.strptime(q["end"][0], "%Y%m%d")
            start = datetime.strptime(q["start"][0], "%Y%m%d")
            par = {k: {} for k in params}
            d = start
            while d <= end:
                key = d.strftime("%Y%m%d")
                lag = (datetime.now(timezone.utc).replace(tzinfo=None) - d).days < 2
                base = {"T2M": 21, "T2M_MAX": 27.5 + math.sin(d.day), "T2M_MIN": 15 + math.cos(d.day), "RH2M": 58, "WS2M": 3.1,
                        "PRECTOTCORR": 0.0, "ALLSKY_SFC_SW_DWN": 13.2, "PS": 101.2, "GWETROOT": 0.38 + 0.01 * (d.day % 5),
                        "GWETTOP": 0.3, "AOD_55": 0.21}
                for k in params:
                    par[k][key] = -999 if lag else base.get(k, 1)
                d += timedelta(days=1)
            return self.out({"geometry": {"coordinates": [31.2, 30.0, 23.4]}, "properties": {"parameter": par}})
        if p == "/power/hourly/point":
            now = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=30)
            key = now.strftime("%Y%m%d%H")
            return self.out({"properties": {"parameter": {k: {key: v} for k, v in
                             {"T2M": 22.1, "RH2M": 52, "WS2M": 3.0, "PRECTOTCORR": 0}.items()}}})
        if p == "/modis/MOD13Q1/dates":
            return self.out({"dates": [{"modis_date": f"A2026{d:03d}", "calendar_date": f"2026-{(d // 30) + 1:02d}-{(d % 30) + 1:02d}"} for d in (241, 257, 273, 289, 305)]})
        if p == "/modis/MOD13Q1/subset":
            return self.out({"subset": [{"calendar_date": f"2026-{m:02d}-10", "data": [v]} for m, v in ((9, 6200), (10, 6800), (11, 7100))]})
        if p == "/forecast":
            now = datetime.now().replace(minute=0, second=0, microsecond=0)
            times, temp, rh, rain, wind, gust = [], [], [], [], [], []
            for i in range(-6, 66):
                t = now + timedelta(hours=i)
                times.append(t.strftime("%Y-%m-%dT%H:%M"))
                temp.append(round(21 + 6 * math.sin((t.hour - 9) / 24 * 2 * math.pi), 1))
                rh.append(55)
                storm = STATE["storm"] and 1 <= i <= 4
                rain.append(5.0 if storm else 0.0)
                wind.append(40 if storm else 9)
                gust.append(72 if storm else 16)
            return self.out({"hourly": {"time": times, "temperature_2m": temp, "relative_humidity_2m": rh,
                                        "precipitation": rain, "wind_speed_10m": wind, "wind_gusts_10m": gust}})
        if p == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                while True:
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(JPG)).encode() + b"\r\n\r\n" + JPG + b"\r\n")
                    time.sleep(0.2)
            except Exception:
                return
        if p == "/capture":
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(JPG)))
            self.end_headers()
            self.wfile.write(JPG)
            return
        self.send_response(404)
        self.end_headers()


def serve(port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Mock)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    serve(18091)
    print("mock services on :18091")
    while True:
        time.sleep(3600)
