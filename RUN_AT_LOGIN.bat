@echo off
rem Starts Agent Office (minimised) every time you log in to Windows. Run again to turn it off.
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Agent Office.lnk"
if exist "%LNK%" (
  del "%LNK%"
  echo Agent Office will NO LONGER start when you log in.
) else (
  powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%'); $s.TargetPath='%~dp0START_OFFICE.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
  echo Agent Office will now start automatically when you log in.
  echo Run this file again to turn that off.
)
pause
