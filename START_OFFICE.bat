@echo off
title Agent Office
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY=

rem --- find Python 3.10 or newer ---
for %%C in ("py -3" "python" "%USERPROFILE%\anaconda3\python.exe" "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" "%LOCALAPPDATA%\Programs\Python\Python312\python.exe") do (
  if not defined PY (
    %%~C -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1 && set "PY=%%~C"
  )
)
if not defined PY (
  echo.
  echo  Python 3.10+ was not found.
  echo  Install it from https://www.python.org/downloads/  ^(tick "Add python.exe to PATH"^), then run this again.
  echo.
  pause
  exit /b 1
)

rem --- private environment, so nothing touches your other Python projects ---
if not exist ".venv\Scripts\python.exe" (
  echo  First start: setting up ^(1-2 minutes, only once^)...
  %PY% -m venv .venv || (echo Could not create the environment. & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || (echo Package install failed - check your internet. & pause & exit /b 1)

echo.
echo  Agent Office is starting. Your browser will open in a few seconds.
echo  Keep this window open - closing it sends everyone home.
echo.
start "" /min cmd /c "timeout /t 5 >nul & start http://localhost:8000"
".venv\Scripts\python.exe" run.py
echo.
echo  The office stopped. If you see an error above, send it to Claude.
pause
