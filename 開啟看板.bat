@echo off
chcp 65001 >nul
:: 量化阿森 · 台股數據獵手 — 開啟即時看板(先掃一輪再開瀏覽器)
cd /d "%~dp0"
set PYTHON=D:\ClawWork\.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python
"%PYTHON%" server.py --scan
pause
