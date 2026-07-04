@echo off
chcp 65001 >nul
:: 量化阿森 · 台股數據獵手 — 全市場掃描(~1900檔上市櫃，twstock 真實產業別)
:: 用日線(全市場盤中分時不切實際)。產出 state.json 後可在看板看全市場版。
cd /d "%~dp0"
set PYTHON=D:\ClawWork\.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python
"%PYTHON%" scan.py --full
pause
