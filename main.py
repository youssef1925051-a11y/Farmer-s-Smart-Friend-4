# -*- coding: utf-8 -*-
"""
Farmer's Smart Friend (صديق الفلاحين) - السيرفر الرئيسي
شغّله:  python server/main.py     (من غير أي مكتبات إجبارية - Pillow/numpy للرؤية بس)
بيفتح: HTTP :8080 (الداشبورد/الأجهزة)  +  HTTPS :8443 (الموبايل - لازم https عشان الـ GPS)
"""
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agronomy as ag  # noqa: E402
import ai_chat  # noqa: E402
import brain  # noqa: E402
import camera  # noqa: E402
import db  # noqa: E402
import discovery  # noqa: E402
import whatsapp  # noqa: E402
from common import CFG, ROOT, iso_now, local_ip  # noqa: E402

WEB = ROOT / "web"
PHONE_RE = re.compile(r"^01[0125]\d{8}$")


def polygon_area_m2(poly):
    lat0 = sum(p[0] for p in poly) / len(poly)
    kx = 111320 * math.cos(math.radians(lat0))
    ky = 110540
    pts = [(p[1] * kx, p[0] * ky) for p in poly]
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FarmersSmartFriend/1.0"

    def log_message(self, *a):
        pass

    def setup(self):
        if isinstance(self.request, ssl.SSLSocket):
            self.request.settimeout(10)
            self.request.do_handshake()
            self.request.settimeout(None)
        super().setup()

    # ---------------------------------------------------------- helpers
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def err(self, msg, code=400):
        self.send_json({"error": msg}, code)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def farmer(self):
        tok = self.headers.get("X-Token")
        if not tok:
            return None
        return db.q1("SELECT * FROM farmers WHERE token=?", (tok,))

    def need_dash(self):
        pin = CFG.get("dashboard_pin")
        if pin and self.headers.get("X-Pin") != pin:
            self.err("pin", 401)
            return False
        return True

    # ---------------------------------------------------------- routing
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        try:
            self.route(method)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            pass
        except Exception as e:
            try:
                self.err(f"خطأ في السيرفر: {e}", 500)
            except Exception:
                pass

    def route(self, method):
        u = urlparse(self.path)
        p, qs = u.path, parse_qs(u.query)
        if p.startswith("/api/") or p.startswith("/webhook/"):
            return self.api(method, p, qs)
        return self.static(p)

    def static(self, p):
        if p in ("/app", "/dashboard"):
            self.send_response(301)
            self.send_header("Location", p + "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if p.endswith("/"):
            p += "index.html"
        f = (WEB / p.lstrip("/")).resolve()
        if not str(f).startswith(str(WEB.resolve())) or not f.is_file():
            return self.err("not found", 404)
        data = f.read_bytes()
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if f.suffix in (".html", ".js", ".css", ".json", ".webmanifest"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # ---------------------------------------------------------- API
    def api(self, method, p, qs):
        # ----- عام
        if p == "/api/status":
            ip = local_ip()
            return self.send_json({
                "ok": True, "time": iso_now(), "ip": ip,
                "app_url": f"https://{ip}:{CFG['server']['https_port']}/app/",
                "dashboard_url": f"http://localhost:{CFG['server']['http_port']}/dashboard/",
                "camera": camera.hub().status(),
                "whatsapp": {"configured": whatsapp.configured(), "require_confirm": bool(CFG["whatsapp"]["require_confirm"] or whatsapp.configured())},
                "ai": {"llm": bool(CFG["ai"].get("anthropic_api_key"))},
                "farmers": db.q("SELECT COUNT(*) c FROM farmers")[0]["c"],
                "pin_required": bool(CFG.get("dashboard_pin")),
                "qr": _qr_available()})
        if p == "/api/qr.svg":
            return self.qr(qs.get("u", [""])[0])
        if p == "/api/crops":
            return self.send_json({"crops": [{"id": c["id"], "name": c["name"]} for c in ag.CROPS.values()],
                                   "soils": {k: v["name"] for k, v in ag.SOILS.items()}})

        # ----- واتساب webhook
        if p == "/webhook/whatsapp":
            if method == "GET":
                if qs.get("hub.verify_token", [""])[0] == CFG["whatsapp"]["verify_token"]:
                    ch = qs.get("hub.challenge", [""])[0].encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(ch)))
                    self.end_headers()
                    self.wfile.write(ch)
                    return
                return self.err("forbidden", 403)
            n = whatsapp.handle_webhook(self.body())
            return self.send_json({"handled": n})

        # ----- تسجيل
        if p == "/api/register" and method == "POST":
            return self.register()

        # ----- الكاميرا
        if p == "/api/camera/status":
            return self.send_json(camera.hub().status())
        if p == "/api/camera/frame.jpg":
            fr, ts = camera.hub().latest()
            if not fr:
                return self.err("no frame", 503)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(fr)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(fr)
            return
        if p == "/api/camera/stream":
            return self.mjpeg()

        # ----- أجهزة ESP32
        if p == "/api/device/hello" and method == "POST":
            b = self.body()
            ip = b.get("ip") or self.client_address[0]
            camera.hub().set_device(ip, b.get("port"))
            return self.send_json({"ok": True, "server_time": iso_now()})
        if p == "/api/device/command":
            dev = qs.get("device", [CFG["default_device_id"]])[0]
            c = db.q1("SELECT * FROM commands WHERE device_id=? AND status='pending' ORDER BY id LIMIT 1", (dev,))
            if not c:
                return self.send_json({"cmd": "none"})
            db.ex("UPDATE commands SET status='sent' WHERE id=?", (c["id"],))
            return self.send_json({"cmd": c["cmd"], "id": c["id"]})
        if p == "/api/device/ack" and method == "POST":
            b = self.body()
            c = db.q1("SELECT * FROM commands WHERE id=?", (b.get("id"),))
            if c:
                db.ex("UPDATE commands SET status='done', acked_at=? WHERE id=?", (iso_now(), c["id"]))
                db.update_farmer(c["farmer_id"], cover_state="closed" if c["cmd"] == "close" else "open")
                db.event("cover", f"الجهاز نفّذ: {'اتقفلت' if c['cmd'] == 'close' else 'اتفتحت'} الأرض", c["farmer_id"])
            return self.send_json({"ok": True})

        # ----- الداشبورد
        if p.startswith("/api/dashboard/"):
            if not self.need_dash():
                return
            return self.dashboard(method, p, qs)

        # ----- المزارع (يحتاج token)
        f = self.farmer()
        if not f:
            return self.err("غير مسجّل", 401)
        return self.farmer_api(method, p, qs, f)

    # ---------------------------------------------------------- register
    def register(self):
        b = self.body()
        name = re.sub(r"\s+", " ", (b.get("name") or "").strip())
        phone = re.sub(r"\D", "", b.get("phone") or "")
        land = str(b.get("land_no") or "").strip()
        if len(name) < 3:
            return self.err("اكتب الاسم بالكامل")
        if not PHONE_RE.match(phone):
            return self.err("رقم الموبايل لازم يبقى 11 رقم ويبدأ بـ 01")
        if not land:
            return self.err("اكتب رقم الأرض في الوحدة الزراعية")
        tok = secrets.token_urlsafe(18)
        old = db.q1("SELECT * FROM farmers WHERE phone=?", (phone,))
        if old and old["status"] == "confirmed":
            return self.err("الرقم ده مسجّل قبل كده. افتح التطبيق من نفس الموبايل اللي سجّلت منه.", 409)
        if old:
            db.update_farmer(old["id"], name=name, land_no=land, token=tok, in_app_confirmed=0, wa_status="none", wa_confirmed=0)
            fid = old["id"]
        else:
            fid = db.ex("INSERT INTO farmers(name,phone,land_no,token,created_at,device_id) VALUES(?,?,?,?,?,?)",
                        (name, phone, land, tok, iso_now(), CFG["default_device_id"]))
        f = db.q1("SELECT * FROM farmers WHERE id=?", (fid,))
        db.event("register", f"مزارع جديد: {name} - أرض {land}", fid)

        def send():
            st = whatsapp.send_confirmation(f)
            db.update_farmer(fid, wa_status=st)
        threading.Thread(target=send, daemon=True).start()
        self.send_json({"token": tok, "farmer": db.farmer_public(f)})

    # ---------------------------------------------------------- farmer API
    def farmer_api(self, method, p, qs, f):
        fid = f["id"]
        req_wa = bool(CFG["whatsapp"]["require_confirm"] or whatsapp.configured())  # لو واتساب متفعّل التأكيد منه إجباري

        def step(fr):
            if not fr["in_app_confirmed"]:
                return "confirm"
            if req_wa and not fr["wa_confirmed"]:
                return "wait_wa"
            if not fr["polygon"]:
                return "field"
            if not fr["canal"]:
                return "canal"
            return "home"

        if p == "/api/farmer/state":
            return self.send_json({"step": step(f), "farmer": db.farmer_public(f), "require_wa": req_wa,
                                   "wa_configured": whatsapp.configured()})
        if p == "/api/farmer/confirm" and method == "POST":
            if self.body().get("decision"):
                db.update_farmer(fid, in_app_confirmed=1, status="confirmed")
                db.event("confirm", f"{f['name']} أكّد التسجيل من التطبيق", fid)
                f = db.q1("SELECT * FROM farmers WHERE id=?", (fid,))
                return self.send_json({"step": step(f)})
            db.ex("DELETE FROM farmers WHERE id=?", (fid,))
            db.event("confirm", f"{f['name']} لغى التسجيل", None)
            return self.send_json({"deleted": True})
        if p == "/api/farmer/field" and method == "POST":
            poly = self.body().get("polygon") or []
            if len(poly) < 3 or any(len(x) != 2 for x in poly):
                return self.err("لازم تحدد 3 نقط على الأقل")
            area = polygon_area_m2(poly)
            if area < 100:
                return self.err("المساحة صغيرة قوي، حدد حدود الأرض كلها")
            lat = sum(x[0] for x in poly) / len(poly)
            lng = sum(x[1] for x in poly) / len(poly)
            db.update_farmer(fid, polygon=poly, lat=lat, lng=lng, area_m2=area)
            db.event("field", f"{f['name']} حدد أرضه ({area / 4200:.2f} فدان)", fid)
            return self.send_json({"ok": True, "area_m2": area, "feddan": round(area / 4200, 2), "lat": lat, "lng": lng})
        if p == "/api/farmer/canal" and method == "POST":
            line = self.body().get("line") or []
            if len(line) < 2:
                return self.err("حدد نقطتين على الأقل للترعة")
            db.update_farmer(fid, canal=line)
            db.event("canal", f"{f['name']} حدد الترعة", fid)
            return self.send_json({"ok": True})
        if p == "/api/farmer/settings" and method == "POST":
            b = self.body()
            upd = {}
            if "crop" in b:
                upd["crop"] = b["crop"] if b["crop"] in ag.CROPS else None
            if "prev_crop" in b:
                upd["prev_crop"] = b["prev_crop"] if b["prev_crop"] in ag.CROPS else None
            if b.get("planting_date"):
                if not re.match(r"^\d{4}-\d{2}-\d{2}$", b["planting_date"]):
                    return self.err("تاريخ الزراعة غلط")
                upd["planting_date"] = b["planting_date"]
            if isinstance(b.get("priorities"), list):
                upd["priorities"] = [x for x in b["priorities"] if x in ("water", "income", "soil")]
            db.update_farmer(fid, **upd)
            return self.send_json({"ok": True, "farmer": db.farmer_public(db.q1("SELECT * FROM farmers WHERE id=?", (fid,)))})
        if p == "/api/farmer/irrigated" and method == "POST":
            db.update_farmer(fid, last_irrigated=iso_now())
            db.event("irrigation", f"{f['name']} سجّل إنه روى الأرض", fid)
            return self.send_json({"ok": True})
        if p == "/api/farmer/alert":
            return self.send_json(brain.current_alert(f))
        if p == "/api/report/start" and method == "POST":
            if step(f) != "home":
                return self.err("كمّل تحديد الأرض الأول")
            job = brain.start_job(fid, force=bool(self.body().get("force")))
            return self.send_json(brain.job_public(job))
        if p == "/api/report/job":
            job = brain.JOBS.get(qs.get("id", [""])[0])
            if not job:
                return self.err("job not found", 404)
            return self.send_json(brain.job_public(job))
        if p == "/api/report/latest":
            return self.send_json({"report": brain.latest_report(fid)})
        if p == "/api/chat" and method == "POST":
            msg = (self.body().get("message") or "").strip()
            if not msg:
                return self.err("اكتب رسالة")
            return self.send_json(ai_chat.reply(f, msg[:600]))
        if p == "/api/chat/history":
            return self.send_json({"messages": db.q("SELECT role,text,ts FROM chat WHERE farmer_id=? ORDER BY id DESC LIMIT 30", (fid,))[::-1]})
        if p == "/api/cover" and method == "POST":
            st = self.body().get("state")
            if st not in ("close", "open"):
                return self.err("state غلط")
            brain.queue_cover(f, st, "ضغط الفلاح على الزرار")
            return self.send_json({"ok": True, "cover_state": "closing" if st == "close" else "opening"})
        return self.err("not found", 404)

    # ---------------------------------------------------------- dashboard
    def dashboard(self, method, p, qs):
        if p == "/api/dashboard/farmers":
            out = []
            for f in db.q("SELECT * FROM farmers ORDER BY id DESC"):
                d = db.farmer_public(f)
                last = db.q1("SELECT score,created_at FROM reports WHERE farmer_id=? ORDER BY id DESC LIMIT 1", (f["id"],))
                d["last_score"], d["last_report_at"] = (last["score"], last["created_at"]) if last else (None, None)
                out.append(d)
            return self.send_json({"farmers": out})
        m = re.match(r"^/api/dashboard/farmer/(\d+)(?:/(\w+))?$", p)
        if m:
            fid, action = int(m.group(1)), m.group(2)
            f = db.q1("SELECT * FROM farmers WHERE id=?", (fid,))
            if not f:
                return self.err("not found", 404)
            if action is None:
                v = db.q1("SELECT json FROM vision WHERE farmer_id=? ORDER BY id DESC LIMIT 1", (fid,))
                return self.send_json({"farmer": db.farmer_public(f), "report": brain.latest_report(fid),
                                       "vision": json.loads(v["json"]) if v else None,
                                       "commands": db.q("SELECT cmd,status,reason,created_at FROM commands WHERE farmer_id=? ORDER BY id DESC LIMIT 5", (fid,)),
                                       "wa": db.q("SELECT kind,status,body,created_at FROM wa_outbox WHERE farmer_id=? ORDER BY id DESC LIMIT 5", (fid,))})
            if action == "soil" and method == "POST":
                b = self.body()
                st = b.get("soil_type") if b.get("soil_type") in ag.SOILS else f.get("soil_type")
                ec = b.get("salinity")
                db.update_farmer(fid, soil_type=st, salinity=float(ec) if ec not in (None, "") else None)
                return self.send_json({"ok": True})
            if action == "report" and method == "POST":
                return self.send_json(brain.job_public(brain.start_job(fid, force=True)))
            if action == "cover" and method == "POST":
                st = self.body().get("state")
                if st in ("close", "open"):
                    brain.queue_cover(f, st, "من الداشبورد")
                return self.send_json({"ok": True})
            if action == "wa_confirm" and method == "POST":
                whatsapp.apply_reply(f"yes:{f['token']}")
                return self.send_json({"ok": True})
        if p == "/api/dashboard/whatsapp" and method == "POST":
            b = self.body()
            pid, tok = re.sub(r"\D", "", str(b.get("phone_number_id") or "")), str(b.get("access_token") or "").strip()
            if not pid or len(tok) < 20:
                return self.err("اكتب Phone number ID (أرقام) و Access token كاملين")
            CFG["whatsapp"].update({"phone_number_id": pid, "access_token": tok, "template_name": str(b.get("template_name") or "").strip()})
            path = Path(os.environ.get("FSF_CONFIG") or (ROOT / "config.json"))
            try:
                disk = json.loads(path.read_text(encoding="utf-8"))
                disk.setdefault("whatsapp", {}).update(CFG["whatsapp"])
                path.write_text(json.dumps(disk, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                return self.err(f"اتفعّل لحد ما السيرفر يقفل بس، ومقدرتش أحفظ config.json: {e}", 500)
            db.event("system", "تم تفعيل واتساب من الداشبورد")
            return self.send_json({"ok": True, "configured": whatsapp.configured()})
        if p == "/api/dashboard/clear_log" and method == "POST":
            n = db.q("SELECT COUNT(*) c FROM events")[0]["c"]
            db.ex("DELETE FROM events")
            db.ex("DELETE FROM wa_outbox")
            db.ex("DELETE FROM commands WHERE status='done'")
            db.event("system", "تم مسح السجل من الداشبورد")
            return self.send_json({"ok": True, "deleted_events": n})
        if p == "/api/dashboard/job":
            job = brain.JOBS.get(qs.get("id", [""])[0])
            if not job:
                return self.err("job not found", 404)
            return self.send_json(brain.job_public(job))
        if p == "/api/dashboard/events":
            since = int(qs.get("since", ["0"])[0])
            ev = db.q("SELECT * FROM events WHERE id>? ORDER BY id DESC LIMIT 40", (since,))
            return self.send_json({"events": ev[::-1]})
        if p == "/api/dashboard/outbox":
            return self.send_json({"outbox": db.q("SELECT o.*, f.name FROM wa_outbox o LEFT JOIN farmers f ON f.id=o.farmer_id ORDER BY o.id DESC LIMIT 15")})
        return self.err("not found", 404)

    # ---------------------------------------------------------- media
    def mjpeg(self):
        hub = camera.hub()
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        last = 0.0
        t_end = time.time() + 3600
        while time.time() < t_end:
            fr, ts = hub.wait_new(last, 5)
            if not fr or ts == last:
                if not hub.online():
                    break
                continue
            last = ts
            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(fr)).encode() + b"\r\n\r\n" + fr + b"\r\n")
            time.sleep(0.06)
        self.close_connection = True

    def qr(self, text):
        try:
            import io
            import segno
            buf = io.BytesIO()
            segno.make(text, error="m").save(buf, kind="svg", scale=6, border=2, dark="#0b3d2e")
            data = buf.getvalue()
        except Exception:
            return self.err("qr unavailable", 404)
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _qr_available():
    try:
        import segno  # noqa: F401
        return True
    except ImportError:
        return False


class HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def handle_error(self, request, client_address):
        pass


class HTTPSServer(HTTPServer):
    ctx = None

    def get_request(self):
        sock, addr = self.socket.accept()
        return self.ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr


def ensure_cert(ip):
    cdir = ROOT / "certs"
    cdir.mkdir(exist_ok=True)
    cert, key, marker = cdir / "cert.pem", cdir / "key.pem", cdir / "ip.txt"
    if cert.exists() and key.exists() and marker.exists() and marker.read_text().strip() == ip:
        return cert, key
    if shutil.which("openssl"):
        try:
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
                            "-days", "3650", "-subj", "/CN=farmers-smart-friend",
                            "-addext", f"subjectAltName=DNS:localhost,IP:127.0.0.1,IP:{ip}"],
                           check=True, capture_output=True, timeout=60)
            marker.write_text(ip)
        except Exception:
            pass
    return cert, key


def main():
    db.init()
    camera.hub()
    discovery.start()
    brain.scheduler()
    host = CFG["server"]["host"]
    ip = local_ip()

    http = HTTPServer((host, CFG["server"]["http_port"]), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()

    https_ok = False
    try:
        cert, key = ensure_cert(ip)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        HTTPSServer.ctx = ctx
        https = HTTPSServer((host, CFG["server"]["https_port"]), Handler)
        threading.Thread(target=https.serve_forever, daemon=True).start()
        https_ok = True
    except Exception as e:
        print("⚠️  HTTPS مش شغال:", e)

    print("=" * 64)
    print("  🌾  صديق الفلاحين — Farmer's Smart Friend  (السيرفر شغال)")
    print("=" * 64)
    print(f"  صفحة السيرفر  : http://localhost:{CFG['server']['http_port']}/")
    print(f"  الداشبورد     : http://localhost:{CFG['server']['http_port']}/dashboard/")
    if https_ok:
        print(f"  تطبيق الموبايل: https://{ip}:{CFG['server']['https_port']}/app/   (من الموبايل على نفس الواي فاي)")
    print(f"  واتساب        : {'متفعّل' if whatsapp.configured() else 'مش متفعّل (حط المفاتيح في config.json)'}")
    print(f"  عم الكومي     : {'Claude API' if CFG['ai'].get('anthropic_api_key') else 'محرك القواعد المحلي'}")
    print("  (اقفل الشاشة دي عشان توقف السيرفر)")
    print("=" * 64)
    if CFG["server"].get("open_browser") and not os.environ.get("FSF_NO_BROWSER"):
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{CFG['server']['http_port']}/")).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
