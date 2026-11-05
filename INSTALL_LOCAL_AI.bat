@echo off
rem Optional: a free AI model that runs on THIS laptop (no key, no limits, but slower).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_local_ai.ps1"
pause
