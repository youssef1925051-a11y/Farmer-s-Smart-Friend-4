# -*- coding: utf-8 -*-
"""إعدادات وأدوات مشتركة - Farmer's Smart Friend"""
import json
import os
import socket
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FSF_DATA") or (ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "cache").mkdir(exist_ok=True)


def load_config():
    path = Path(os.environ.get("FSF_CONFIG") or (ROOT / "config.json"))
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    env = os.environ
    if env.get("ANTHROPIC_API_KEY"):
        cfg["ai"]["anthropic_api_key"] = env["ANTHROPIC_API_KEY"]
    if env.get("WHATSAPP_TOKEN"):
        cfg["whatsapp"]["access_token"] = env["WHATSAPP_TOKEN"]
    if env.get("WHATSAPP_PHONE_ID"):
        cfg["whatsapp"]["phone_number_id"] = env["WHATSAPP_PHONE_ID"]
    if env.get("FSF_DB"):
        cfg["_db_path"] = env["FSF_DB"]
    return cfg


CFG = load_config()

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Africa/Cairo")
except Exception:  # ويندوز من غير tzdata
    _TZ = timezone(timedelta(hours=2))


def now_local():
    return datetime.now(_TZ)


def iso_now():
    return now_local().isoformat(timespec="seconds")


def http_get(url, timeout=15, headers=None, data=None, method=None):
    h = {"User-Agent": "FarmersSmartFriend/1.0"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, data=data, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_json(url, timeout=15, headers=None, data=None, method=None):
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    if data is not None and not isinstance(data, (bytes, bytearray)):
        data = json.dumps(data).encode("utf-8")
        h.setdefault("Content-Type", "application/json")
    return json.loads(http_get(url, timeout, h, data, method).decode("utf-8"))


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def cached(key, ttl, fn):
    """يجيب من الكاش لو لسه صالح، وإلا ينادي fn، ولو فشلت يرجّع آخر نسخة ناجحة (معلّمة stale)."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
    path = DATA_DIR / "cache" / (safe + ".json")
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        try:
            return json.loads(path.read_text(encoding="utf-8")), False
        except Exception:
            pass
    try:
        val = fn()
        path.write_text(json.dumps(val, ensure_ascii=False), encoding="utf-8")
        return val, False
    except Exception:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")), True
        raise
