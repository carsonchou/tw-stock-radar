@echo off
chcp 65001 >nul
title 量化阿森 · 台股數據獵手
cd /d "%~dp0"
set PYTHON=D:\ClawWork\.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python
"%PYTHON%" app.py
