# -*- coding: utf-8 -*-
"""
واتساب عن طريق WhatsApp Cloud API (Meta). بيشتغل فعلاً لو حطيت phone_number_id و access_token في config.json.
  - رسالة تأكيد بزرارين (تأكيد / لا) - template لو متسجّل، أو interactive جوه نافذة الـ 24 ساعة.
  - الرد بييجي على /webhook/whatsapp (محتاج رابط عام مثلاً ngrok) وبيفعّل المزارع.
من غير بيانات اعتماد: الرسالة بتتسجل في outbox وتظهر في الداشبورد بحالة "مش متفعّل" (مفيش إرسال وهمي).
"""
import json
import re

import db
from common import CFG, http_json, iso_now

GRAPH = "https://graph.facebook.com/v20.0"


def configured():
    w = CFG["whatsapp"]
    return bool(w.get("phone_number_id") and w.get("access_token"))


def normalize(phone):
    p = re.sub(r"\D", "", phone or "")
    if p.startswith("00"):
        p = p[2:]
    if p.startswith("0"):
        p = "20" + p[1:]
    return p


def _post(payload):
    w = CFG["whatsapp"]
    return http_json(f"{GRAPH}/{w['phone_number_id']}/messages", timeout=10,
                     headers={"Authorization": f"Bearer {w['access_token']}"}, data=payload, method="POST")


def _log(farmer_id, phone, body, kind, status, error=None):
    return db.ex("INSERT INTO wa_outbox(farmer_id,to_phone,body,kind,status,error,created_at) VALUES(?,?,?,?,?,?,?)",
                 (farmer_id, phone, body, kind, status, error, iso_now()))


def send_text(farmer, body, kind="alert"):
    to = normalize(farmer["phone"])
    if not configured():
        _log(farmer["id"], to, body, kind, "not_configured")
        return "not_configured"
    try:
        _post({"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}})
        _log(farmer["id"], to, body, kind, "sent")
        return "sent"
    except Exception as e:
        _log(farmer["id"], to, body, kind, "failed", str(e))
        return "failed"


def send_confirmation(farmer):
    """رسالة التسجيل بزرارين. بتتبعت في نفس لحظة ضغط "تم"."""
    to = normalize(farmer["phone"])
    tok = farmer["token"]
    body = (f"أهلاً يا حج {farmer['name'].split()[0]} 🌾\n"
            f"ده تأكيد تسجيلك في «صديق الفلاحين» للأرض رقم {farmer['land_no']}.\n"
            "اضغط (تأكيد) لو ده أنت.")
    if not configured():
        _log(farmer["id"], to, body, "confirm", "not_configured")
        return "not_configured"
    try:
        w = CFG["whatsapp"]
        if w.get("template_name"):
            payload = {"messaging_product": "whatsapp", "to": to, "type": "template", "template": {
                "name": w["template_name"], "language": {"code": w.get("template_lang", "ar")},
                "components": [
                    {"type": "body", "parameters": [{"type": "text", "text": farmer["name"].split()[0]},
                                                    {"type": "text", "text": str(farmer["land_no"])}]},
                    {"type": "button", "sub_type": "quick_reply", "index": "0",
                     "parameters": [{"type": "payload", "payload": f"yes:{tok}"}]},
                    {"type": "button", "sub_type": "quick_reply", "index": "1",
                     "parameters": [{"type": "payload", "payload": f"no:{tok}"}]}]}}
        else:
            payload = {"messaging_product": "whatsapp", "to": to, "type": "interactive", "interactive": {
                "type": "button", "body": {"text": body},
                "action": {"buttons": [
                    {"type": "reply", "reply": {"id": f"yes:{tok}", "title": "تأكيد ✅"}},
                    {"type": "reply", "reply": {"id": f"no:{tok}", "title": "لا ❌"}}]}}}
        _post(payload)
        _log(farmer["id"], to, body, "confirm", "sent")
        return "sent"
    except Exception as e:
        _log(farmer["id"], to, body, "confirm", "failed", str(e))
        return "failed"


def apply_reply(reply_id):
    """بيتنادى من الـ webhook (أو من زرار التأكيد اليدوي في الداشبورد للتجربة)."""
    if ":" not in (reply_id or ""):
        return None
    decision, token = reply_id.split(":", 1)
    f = db.q1("SELECT * FROM farmers WHERE token=?", (token,))
    if not f:
        return None
    if decision == "yes":
        db.update_farmer(f["id"], wa_confirmed=1, wa_status="confirmed")
        db.event("wa", f"{f['name']} أكّد على واتساب", f["id"])
    else:
        db.update_farmer(f["id"], wa_confirmed=0, wa_status="denied")
        db.event("wa", f"{f['name']} رفض التأكيد على واتساب", f["id"])
    return decision


def handle_webhook(payload):
    n = 0
    for entry in payload.get("entry", []):
        for ch in entry.get("changes", []):
            for m in ch.get("value", {}).get("messages", []):
                rid = None
                if m.get("type") == "interactive":
                    rid = (m["interactive"].get("button_reply") or {}).get("id")
                elif m.get("type") == "button":
                    rid = m["button"].get("payload")
                if rid and apply_reply(rid):
                    n += 1
    return n
