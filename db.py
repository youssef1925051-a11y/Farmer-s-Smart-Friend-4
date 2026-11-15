# -*- coding: utf-8 -*-
"""قاعدة بيانات الوحدة الزراعية (SQLite)"""
import json
import sqlite3
import threading

from common import CFG, DATA_DIR, iso_now

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS farmers(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT UNIQUE NOT NULL,
  land_no TEXT NOT NULL, token TEXT, status TEXT DEFAULT 'pending',
  in_app_confirmed INTEGER DEFAULT 0, wa_status TEXT DEFAULT 'none', wa_confirmed INTEGER DEFAULT 0,
  lat REAL, lng REAL, polygon TEXT, canal TEXT, area_m2 REAL,
  crop TEXT, planting_date TEXT, prev_crop TEXT,
  priorities TEXT DEFAULT '["water","income","soil"]',
  soil_type TEXT, salinity REAL, last_irrigated TEXT,
  cover_state TEXT DEFAULT 'open', device_id TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY AUTOINCREMENT, farmer_id INTEGER, day TEXT, created_at TEXT, score REAL, json TEXT);
CREATE TABLE IF NOT EXISTS chat(
  id INTEGER PRIMARY KEY AUTOINCREMENT, farmer_id INTEGER, role TEXT, text TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS wa_outbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT, farmer_id INTEGER, to_phone TEXT, body TEXT, kind TEXT,
  status TEXT, error TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, farmer_id INTEGER, msg TEXT);
CREATE TABLE IF NOT EXISTS commands(
  id INTEGER PRIMARY KEY AUTOINCREMENT, farmer_id INTEGER, device_id TEXT, cmd TEXT,
  status TEXT DEFAULT 'pending', reason TEXT, created_at TEXT, acked_at TEXT);
CREATE TABLE IF NOT EXISTS vision(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, farmer_id INTEGER, json TEXT);
"""


def init():
    global _conn
    path = CFG.get("_db_path") or str(DATA_DIR / "farmers.db")
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    with _lock:
        _conn.executescript(SCHEMA)
        _conn.commit()


def q(sql, args=()):
    with _lock:
        cur = _conn.execute(sql, args)
        rows = [dict(r) for r in cur.fetchall()]
        return rows


def q1(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql, args=()):
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur.lastrowid


def event(kind, msg, farmer_id=None):
    ex("INSERT INTO events(ts,kind,farmer_id,msg) VALUES(?,?,?,?)", (iso_now(), kind, farmer_id, msg))


def update_farmer(fid, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = [json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for v in fields.values()]
    ex(f"UPDATE farmers SET {cols} WHERE id=?", vals + [fid])


def farmer_public(f):
    """نسخة آمنة من بيانات المزارع (من غير token) مع تحويل JSON."""
    if not f:
        return None
    d = dict(f)
    d.pop("token", None)
    for k in ("polygon", "canal", "priorities"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    if d.get("lat") is not None and d.get("lng") is not None:
        d["maps_link"] = f"https://www.google.com/maps?q={d['lat']},{d['lng']}"
    else:
        d["maps_link"] = None
    return d
