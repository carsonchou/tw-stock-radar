@echo off
chcp 65001 >nul
title 數據獵手 每日自動排程
cd /d "%~dp0"
set PYTHON=D:\ClawWork\.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python
:: 每交易日 17:00 自動跑完整 eod(刷資料+四維籌碼+掃描+產貼文+推ntfy)
"%PYTHON%" auto_eod.py
