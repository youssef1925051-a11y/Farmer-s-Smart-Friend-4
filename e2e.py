# -*- coding: utf-8 -*-
"""اختبار شامل: تسجيل -> تأكيد -> أرض -> ترعة -> تقرير (<40ث) -> شات -> قفل الأرض -> جهاز ESP32 -> عاصفة."""
import json, ssl, sys, time, urllib.request

B = "http://127.0.0.1:18080"
ctx = ssl._create_unverified_context()


def call(path, data=None, token=None, base=B, method=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["X-Token"] = token
    req = urllib.request.Request(base + path, data=None if data is None else json.dumps(data).encode(), headers=h, method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_http": e.code, **json.loads(e.read())}


ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name, extra)

r = call("/api/register", {"name": "محمد", "phone": "0123", "land_no": "5"})
check("validation rejects bad input", r.get("_http") == 400, r.get("error"))
r = call("/api/register", {"name": "محمد عبد الله", "phone": "01012345678", "land_no": "A-17"}, base="https://127.0.0.1:18443")
tok = r["token"]; check("register over HTTPS", bool(tok))
st = call("/api/farmer/state", token=tok); check("step=confirm", st["step"] == "confirm")
time.sleep(0.5)
ob = call("/api/dashboard/outbox")["outbox"]; check("whatsapp outbox logged, not faked", ob and ob[0]["status"] == "not_configured", ob[0]["status"] if ob else "")
st = call("/api/farmer/confirm", {"decision": True}, tok); check("confirm -> field", st["step"] == "field")
poly = [[30.0450, 31.2350], [30.0450, 31.2362], [30.0441, 31.2362], [30.0441, 31.2350]]
r = call("/api/farmer/field", {"polygon": poly}, tok); check("field saved", r.get("ok"), f"{r.get('feddan')} فدان")
r = call("/api/farmer/canal", {"line": [[30.0440, 31.2349], [30.0452, 31.2349]]}, tok); check("canal saved", r.get("ok"))
st = call("/api/farmer/state", token=tok); check("step=home", st["step"] == "home")
call("/api/farmer/settings", {"crop": "wheat", "planting_date": "2026-10-25", "priorities": ["water", "soil", "income"]}, tok)

t0 = time.time()
job = call("/api/report/start", {}, tok)
while job["state"] == "running" and time.time() - t0 < 45:
    time.sleep(1); job = call(f"/api/report/job?id={job['id']}", token=tok)
el = time.time() - t0
check("report done < 40s", job["state"] == "done" and el < 40, f"{el:.1f}s state={job['state']} {job.get('error','')}")
rep = job["report"]
print("---- التقرير ----"); print(rep["text"]); print("score", rep["score"], rep["status"], "| missing:", rep["missing"], "| stale:", rep["stale"])
print("3 أيام:", rep["three_days"]["text"])
print("ري:", rep["irrigation"].get("level"), rep["irrigation"].get("minutes"), "دقيقة | chosen", rep["irrigation"].get("chosen"))
for s in rep["irrigation"]["scenarios"]: print("   ", s["id"], s["feasible"], s["score"], s["label"])
print("محاصيل:", [(c["name"], c["score"]) for c in rep["crops"]["scenarios"]], "chosen", rep["crops"]["chosen"])
print("حماية:", rep["protection"]["severity"], rep["protection"]["chosen"])
print("درون:", {k: rep["drone"].get(k) for k in ("veg_pct", "water_pct", "soil_pct", "health", "health_ar", "water_text")})
check("3 scenarios irrigation", len(rep["irrigation"]["scenarios"]) == 3)
check("3 scenarios crops", len(rep["crops"]["scenarios"]) == 3)
check("3 scenarios protection", len(rep["protection"]["scenarios"]) == 3)
check("ndvi from modis", rep["nasa"]["ndvi"] and rep["nasa"]["ndvi"]["latest"]["ndvi"] > 0.5, str(rep["nasa"]["ndvi"] and rep["nasa"]["ndvi"]["latest"]))
check("vision ok on drone frame", rep["drone"].get("ok") and rep["drone"]["veg_pct"] > 30)

c = call("/api/chat", {"message": "اسقي ولا لا يا عم الكومي؟"}, tok); print("عم الكومي:", c["reply"]); check("chat irrigation", "اسق" in c["reply"] or "ميّه" in c["reply"] or "مي" in c["reply"])
c = call("/api/chat", {"message": "ازرع ايه"}, tok); print("عم الكومي:", c["reply"])
c = call("/api/chat", {"message": "اقفل الارض"}, tok); print("عم الكومي:", c["reply"]); check("chat closes cover", c["action"] == "close")
cmd = call("/api/device/command?device=FSF-CAM-01"); check("esp32 receives close", cmd["cmd"] == "close", str(cmd))
call("/api/device/ack", {"id": cmd["id"], "state": "closed"})
st = call("/api/farmer/state", token=tok); check("cover_state closed", st["farmer"]["cover_state"] == "closed")
cmd = call("/api/device/command?device=FSF-CAM-01"); check("queue empty after ack", cmd["cmd"] == "none")
call("/api/cover", {"state": "open"}, tok)
cmd = call("/api/device/command?device=FSF-CAM-01"); call("/api/device/ack", {"id": cmd["id"]}); check("open command", cmd["cmd"] == "open")

# عاصفة
call("/__storm?on=1", base="http://127.0.0.1:18091")
import os; os.environ.setdefault("x", "1")
time.sleep(0.2)
# امسح الكاش عشان التوقع الجديد يتقرا
import glob; [os.remove(f) for f in glob.glob("/tmp/fsf_test_data/cache/forecast*")]
al = call("/api/farmer/alert", token=tok); check("storm alert severe", al["severity"] == "severe", str(al))
job = call("/api/report/start", {"force": True}, tok)
t0 = time.time()
while job["state"] == "running" and time.time() - t0 < 45:
    time.sleep(1); job = call(f"/api/report/job?id={job['id']}", token=tok)
rep = job["report"]; print("عاصفة:", [b["text"] for b in rep["bullets"] if b["kind"] == "danger"])
print("   إجراءات تلقائية:", rep["actions_taken"])
check("auto-protect queued close", bool(rep["actions_taken"]))
cmd = call("/api/device/command?device=FSF-CAM-01"); check("esp32 gets auto close", cmd["cmd"] == "close")
call("/__storm?on=0", base="http://127.0.0.1:18091")

d = call("/api/dashboard/farmers"); check("dashboard lists farmer w/ maps link", d["farmers"][0]["maps_link"].startswith("https://www.google.com/maps"))
dd = call(f"/api/dashboard/farmer/{d['farmers'][0]['id']}"); check("dashboard detail", dd["report"] and dd["vision"])
print("\nALL PASS" if ok else "\nSOME FAILED"); sys.exit(0 if ok else 1)
