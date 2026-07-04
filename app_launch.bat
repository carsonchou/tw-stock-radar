@echo off
chcp 65001 >nul
title 量化阿森 · 台股數據獵手
cd /d "%~dp0"
set PYTHON=python
if exist ".venv\Scripts\python.exe" set PYTHON=.venv\Scripts\python.exe
"%PYTHON%" app.py
