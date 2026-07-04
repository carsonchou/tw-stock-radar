@echo off
chcp 65001 >nul
:: 量化阿森 · 台股數據獵手 — 背景掃描迴圈(盤中5分/盤後30分，自動推 ntfy)
cd /d "%~dp0"
set PYTHON=D:\ClawWork\.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python
"%PYTHON%" loop.py >> loop.log 2>&1
