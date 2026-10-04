@echo off
rem ============================================================
rem  CathayViewer - remove per-user file association (HKCU only)
rem  ASCII only. Double-click to run (normal user, no admin).
rem  Mirrors the add script. exe path only for reference:
rem    %~dp0CathayViewer.exe
rem  Never touches HKLM and installs no service.
rem ============================================================
setlocal
rem ---- .pdf ----
reg delete "HKCU\Software\Classes\CathayViewer.pdf" /f
reg delete "HKCU\Software\Classes\.pdf\OpenWithProgids" /v "CathayViewer.pdf" /f
reg delete "HKCU\Software\Classes\.pdf" /ve /f
rem ---- .epub ----
reg delete "HKCU\Software\Classes\CathayViewer.epub" /f
reg delete "HKCU\Software\Classes\.epub\OpenWithProgids" /v "CathayViewer.epub" /f
reg delete "HKCU\Software\Classes\.epub" /ve /f
rem ---- .txt ----
reg delete "HKCU\Software\Classes\CathayViewer.txt" /f
reg delete "HKCU\Software\Classes\.txt\OpenWithProgids" /v "CathayViewer.txt" /f
reg delete "HKCU\Software\Classes\.txt" /ve /f
rem ---- .md ----
reg delete "HKCU\Software\Classes\CathayViewer.md" /f
reg delete "HKCU\Software\Classes\.md\OpenWithProgids" /v "CathayViewer.md" /f
reg delete "HKCU\Software\Classes\.md" /ve /f
echo.
echo Done. Associations for this user removed.
pause
endlocal
