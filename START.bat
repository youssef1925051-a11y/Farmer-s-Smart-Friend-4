@echo off
chcp 65001 >nul
title Farmer's Smart Friend - Server
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
where %PY% >nul 2>nul || (
  echo.
  echo  Python مش متثبّت على الجهاز. نزّله من:  https://www.python.org/downloads/
  echo  ^(علّم على Add Python to PATH وقت التثبيت^) وبعدها شغّل الملف ده تاني.
  start https://www.python.org/downloads/
  pause
  exit /b
)
echo  تجهيز المكتبات ^(أول مرة بس - محتاج إنترنت^)...
%PY% -m pip install -q -r requirements.txt 2>nul
echo.
%PY% server\main.py
pause
