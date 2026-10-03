@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0runtime\pythonw.exe" (
  start "" "%~dp0runtime\pythonw.exe" gui.py
  goto :eof
)
where pyw >nul 2>nul && (
  start "" pyw -3 gui.py
  goto :eof
)
where pythonw >nul 2>nul && (
  start "" pythonw gui.py
  goto :eof
)
echo Python not found. Use the packaged exe instead.
pause
