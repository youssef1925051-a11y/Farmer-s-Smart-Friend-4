#!/bin/bash
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Python3 مش متثبّت"; exit 1; }
python3 -m pip install -q -r requirements.txt 2>/dev/null || python3 -m pip install -q --break-system-packages -r requirements.txt 2>/dev/null
python3 server/main.py
