@echo off
cd /d "%~dp0"
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name CathayViewer --icon app.ico --add-data "config;config" --distpath "dev_dist" --workpath "dev_build" --specpath "." gui.py
pause
