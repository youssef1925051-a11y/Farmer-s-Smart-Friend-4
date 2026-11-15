# -*- coding: utf-8 -*-
"""إعلان السيرفر على الشبكة (UDP) عشان الـ ESP32 يلاقيه لوحده من غير ما نكتب IP."""
import socket
import threading
import time

from common import CFG, local_ip

PORT = 4210


def _loop():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        ip = local_ip()
        msg = f"FSF|{ip}|{CFG['server']['http_port']}".encode()
        targets = ["255.255.255.255"]
        parts = ip.split(".")
        if len(parts) == 4:
            targets.append(".".join(parts[:3] + ["255"]))
        for t in targets:
            try:
                s.sendto(msg, (t, PORT))
            except Exception:
                pass
        time.sleep(2)


def start():
    threading.Thread(target=_loop, daemon=True).start()
