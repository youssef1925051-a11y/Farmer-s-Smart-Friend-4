# -*- coding: utf-8 -*-
"""
وسيط الكاميرا: بيفتح اتصال واحد بـ ESP32-CAM (MJPEG) وبيوزّع آخر لقطة على أي عدد من الشاشات
(الموبايل والداشبورد والتحليل). كده الـ ESP32 مش بيتحمل غير عميل واحد.
"""
import http.client
import threading
import time
from urllib.parse import urlparse

from common import CFG


class CameraHub:
    def __init__(self):
        self.host = None
        self.port = CFG["camera"].get("port", 81)
        self.path = "/stream"
        self.frame = None
        self.ts = 0.0
        self.error = "لسه مفيش كاميرا اتوصلت"
        self.cond = threading.Condition()
        self.frames = 0
        self._gen = 0
        if CFG["camera"].get("stream_url"):
            u = urlparse(CFG["camera"]["stream_url"])
            self.host, self.port, self.path = u.hostname, u.port or 80, u.path or "/stream"
        threading.Thread(target=self._run, daemon=True).start()

    def set_device(self, ip, port=None):
        if CFG["camera"].get("stream_url"):
            return
        if ip != self.host:
            self.host = ip
            if port:
                self.port = port
            self._gen += 1

    def online(self):
        return self.frame is not None and (time.time() - self.ts) < CFG["camera"].get("stale_seconds", 20)

    def status(self):
        return {"online": self.online(), "host": self.host, "port": self.port,
                "age_s": None if not self.ts else round(time.time() - self.ts, 1),
                "frames": self.frames, "error": None if self.online() else self.error}

    def latest(self):
        return self.frame, self.ts

    def wait_new(self, last_ts, timeout=5):
        with self.cond:
            if self.ts <= last_ts:
                self.cond.wait(timeout)
            return self.frame, self.ts

    def _publish(self, jpg):
        with self.cond:
            self.frame, self.ts = jpg, time.time()
            self.frames += 1
            self.error = None
            self.cond.notify_all()

    def _run(self):
        while True:
            if not self.host:
                time.sleep(1)
                continue
            gen = self._gen
            host, port, path = self.host, self.port, self.path
            try:
                self._stream(host, port, path, gen)
            except Exception as e:
                self.error = f"الاتصال بالكاميرا فشل: {e.__class__.__name__}"
            if gen == self._gen:
                self._poll_capture(host, port)
            time.sleep(2)

    def _stream(self, host, port, path, gen):
        conn = http.client.HTTPConnection(host, port, timeout=8)
        conn.request("GET", path)
        resp = conn.getresponse()
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        buf = b""
        while gen == self._gen:
            chunk = resp.read(4096)
            if not chunk:
                break
            buf += chunk
            while True:
                a = buf.find(b"\xff\xd8")
                if a < 0:
                    buf = buf[-2:]
                    break
                b = buf.find(b"\xff\xd9", a + 2)
                if b < 0:
                    buf = buf[a:]
                    break
                self._publish(buf[a:b + 2])
                buf = buf[b + 2:]
        conn.close()

    def _poll_capture(self, host, port):
        for p in (port, 80):
            try:
                conn = http.client.HTTPConnection(host, p, timeout=6)
                conn.request("GET", "/capture")
                r = conn.getresponse()
                data = r.read()
                conn.close()
                if r.status == 200 and data[:2] == b"\xff\xd8":
                    self._publish(data)
                    return
            except Exception:
                continue


HUB = None


def hub():
    global HUB
    if HUB is None:
        HUB = CameraHub()
    return HUB
